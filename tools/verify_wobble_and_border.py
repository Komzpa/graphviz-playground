#!/usr/bin/env python3
"""Verify the 2437 wobble and 1308 cluster-border regressions."""

from __future__ import annotations

import contextlib
import filecmp
import json
import math
import re
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOTS = {
    "upstream": ROOT.parent.parent / "graphviz-fairtime-20260727/upstream/build/cmd/dot/dot_builtins",
    "before": ROOT.parent.parent / "graphviz-port249-20260727/src/build/cmd/dot/dot_builtins",
    "after": ROOT.parent.parent / "graphviz-clusterbox-20260727/src/build/cmd/dot/dot_builtins",
    "fixed": ROOT / "build/cmd/dot/dot_builtins",
}
RANKERS = {"default": (), "newrank=true": ("-Gnewrank=true",)}
CONCENTRATE_MODES = {"concentrate_absent": (), "concentrate_true": ("-Gconcentrate=true",)}
MOVED_MODULE = "lib/dotgen/flat_edge_splines.c"
RATCHET_PATH = Path("lib/dotgen/dotsplines.c")
IDENTITY_FIXTURE_LIMIT = 80
IDENTITY_FIXTURE_MINIMUM = 60
ULIMIT_V_KB = 2 * 1024 * 1024
TIMEOUT = 10
WOBBLE_LIMIT = 0.5
BORDER_EPSILON = 0.5
BORDER_CLEARANCE = 4.0
POINT_EPSILON = 0.02
TINY_FLOAT_RE = re.compile(rb"-?(?:\d+(?:\.\d*)?|\.\d+)e-\d+")


@dataclass(frozen=True)
class BorderHit:
    graph: Path
    ranker: str
    edge: str
    border: str
    separation: float
    overlap: float


@dataclass
class IdentityStats:
    matched: int = 0
    comparable: int = 0
    skipped: int = 0


def capped_command(dot: Path, fmt: str, args: tuple[str, ...], graph: Path) -> list[str]:
    argv = [str(dot), "-Kdot", f"-T{fmt}", *args, str(graph)]
    return [
        "bash",
        "-lc",
        f"ulimit -v {ULIMIT_V_KB}; exec timeout {TIMEOUT}s \"$@\"",
        "dot-ulimit",
        *argv,
    ]


def render_json(dot: Path, graph: Path, args: tuple[str, ...]) -> dict[str, Any]:
    proc = subprocess.run(
        capped_command(dot, "json", args, graph),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=TIMEOUT + 2,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{dot} {' '.join(args)} {graph} exited {proc.returncode}: "
            f"{proc.stderr.splitlines()[:1]}"
        )
    return json.loads(proc.stdout)


def render_xdot_to(dot: Path, graph: Path, args: tuple[str, ...], out: Path) -> bool:
    with out.open("wb") as stdout:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc = subprocess.run(
                capped_command(dot, "xdot", args, graph),
                cwd=ROOT,
                stdout=stdout,
                stderr=subprocess.PIPE,
                check=False,
                timeout=TIMEOUT + 2,
            )
            return proc.returncode == 0 and out.stat().st_size > 0
    return False


def zero_normalized_bytes(path: Path) -> bytes:
    def replace(match: re.Match[bytes]) -> bytes:
        value = float(match.group(0))
        return b"0" if abs(value) < 1e-9 else match.group(0)

    return TINY_FLOAT_RE.sub(replace, path.read_bytes())


def identity_match(left: Path, right: Path) -> str | None:
    if filecmp.cmp(left, right, shallow=False):
        return "byte"
    if zero_normalized_bytes(left) == zero_normalized_bytes(right):
        return "zero-normalized"
    return None


def gvid_names(layout: dict[str, Any]) -> dict[int, str]:
    return {obj["_gvid"]: obj["name"] for obj in layout.get("objects", []) if "name" in obj}


def edge_name(edge: dict[str, Any], names: dict[int, str]) -> str:
    return f"{names[edge['tail']]} -> {names[edge['head']]}"


def edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    return [
        (float(x), float(y))
        for op in edge.get("_draw_", [])
        if op.get("op") in {"b", "B"}
        for x, y in op["points"]
    ]


def drawn_edges(layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [edge for edge in layout.get("edges", []) if "_draw_" in edge]


def arrow_count(edge: dict[str, Any], stream: str) -> int:
    return sum(1 for op in edge.get(stream, []) if op.get("op") in {"P", "p"})


def node_box(node: dict[str, Any]) -> tuple[float, float, float, float]:
    x, y = (float(value) for value in node["pos"].split(","))
    half_width = float(node["width"]) * 36.0
    half_height = float(node["height"]) * 36.0
    return x - half_width, y - half_height, x + half_width, y + half_height


def points_from_stream(stream: list[dict[str, Any]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for op in stream:
        if "points" in op:
            points.extend((float(x), float(y)) for x, y in op["points"])
        elif "rect" in op:
            x, y, w, h = (float(value) for value in op["rect"])
            points.extend(((x - w, y - h), (x + w, y + h)))
    return points


def extent(points: list[tuple[float, float]], axis: int) -> float:
    values = [point[axis] for point in points]
    return max(values) - min(values)


def boxes_overlap(left: tuple[float, float, float, float],
                  right: tuple[float, float, float, float]) -> bool:
    return (
        max(left[0], right[0]) < min(left[2], right[2]) - BORDER_EPSILON
        and max(left[1], right[1]) < min(left[3], right[3]) - BORDER_EPSILON
    )


def line_deviation(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    x1, y1 = points[0]
    x2, y2 = points[-1]
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 1e-9:
        return 0.0
    return max(abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / length for x, y in points)


def cluster_borders(layout: dict[str, Any]) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    borders = []
    for obj in layout.get("objects", []):
        name = obj.get("name", "")
        if not name.startswith("cluster") or "bb" not in obj:
            continue
        left, bottom, right, top = (float(part) for part in obj["bb"].split(","))
        borders.extend(
            [
                (f"{name} bottom ({left:g},{bottom:g})-({right:g},{bottom:g})", (left, bottom), (right, bottom)),
                (f"{name} top ({left:g},{top:g})-({right:g},{top:g})", (left, top), (right, top)),
                (f"{name} left ({left:g},{bottom:g})-({left:g},{top:g})", (left, bottom), (left, top)),
                (f"{name} right ({right:g},{bottom:g})-({right:g},{top:g})", (right, bottom), (right, top)),
            ]
        )
    return borders


def segment_distance(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> float:
    def point_segment_distance(p: tuple[float, float], x: tuple[float, float], y: tuple[float, float]) -> float:
        px, py = p
        x1, y1 = x
        x2, y2 = y
        dx, dy = x2 - x1, y2 - y1
        denom = dx * dx + dy * dy
        if denom <= 1e-12:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denom))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


def colinear_overlap(edge_a: tuple[float, float], edge_b: tuple[float, float], border_a: tuple[float, float], border_b: tuple[float, float]) -> tuple[float, float]:
    ex1, ey1 = edge_a
    ex2, ey2 = edge_b
    bx1, by1 = border_a
    bx2, by2 = border_b
    if abs(ey1 - ey2) <= BORDER_EPSILON and abs(by1 - by2) <= BORDER_EPSILON:
        sep = abs((ey1 + ey2) / 2.0 - (by1 + by2) / 2.0)
        overlap = min(max(ex1, ex2), max(bx1, bx2)) - max(min(ex1, ex2), min(bx1, bx2))
        return sep, max(0.0, overlap)
    if abs(ex1 - ex2) <= BORDER_EPSILON and abs(bx1 - bx2) <= BORDER_EPSILON:
        sep = abs((ex1 + ex2) / 2.0 - (bx1 + bx2) / 2.0)
        overlap = min(max(ey1, ey2), max(by1, by2)) - max(min(ey1, ey2), min(by1, by2))
        return sep, max(0.0, overlap)
    return math.inf, 0.0


def border_hits(graph: Path, ranker: str, layout: dict[str, Any]) -> tuple[float, BorderHit | None]:
    names = gvid_names(layout)
    min_distance = math.inf
    worst: BorderHit | None = None
    for edge in layout.get("edges", []):
        points = edge_points(edge)
        if len(points) < 2:
            continue
        for border_name, border_a, border_b in cluster_borders(layout):
            for a, b in zip(points, points[1:]):
                distance = segment_distance(a, b, border_a, border_b)
                min_distance = min(min_distance, distance)
                sep, overlap = colinear_overlap(a, b, border_a, border_b)
                if overlap >= BORDER_CLEARANCE and sep <= BORDER_EPSILON:
                    hit = BorderHit(graph, ranker, edge_name(edge, names), border_name, sep, overlap)
                    if worst is None or hit.overlap > worst.overlap:
                        worst = hit
    return (0.0 if min_distance is math.inf else min_distance), worst


def verify_focus() -> int:
    failures = 0
    for ranker, args in RANKERS.items():
        print(f"2437 [{ranker}] offending_edge=AA3 -> AA4")
        for name, dot in DOTS.items():
            layout = render_json(dot, ROOT / "tests/2437.dot", args)
            edge = layout["edges"][0]
            points = edge_points(edge)
            deviation = line_deviation(points)
            print(f"  {name}: control_points={points} deviation_pt={deviation:.3f}")
            if name == "fixed" and deviation > WOBBLE_LIMIT:
                print(f"  REGRESSION fixed deviation {deviation:.3f} > {WOBBLE_LIMIT}")
                failures += 1

        print(f"1308 [{ranker}] cluster_border_clearance={BORDER_CLEARANCE:g}pt overlap threshold")
        for name in ("before", "after", "fixed"):
            layout = render_json(DOTS[name], ROOT / "tests/1308.dot", args)
            min_distance, hit = border_hits(ROOT / "tests/1308.dot", ranker, layout)
            print(f"  {name}: min_edge_border_distance_pt={min_distance:.3f}")
            if hit is None:
                print("    along_border=none")
            else:
                print(
                    f"    along_border edge={hit.edge} border={hit.border} "
                    f"separation_pt={hit.separation:.3f} overlap_pt={hit.overlap:.3f}"
                )
            if name == "fixed" and hit is not None:
                print(f"  REGRESSION fixed has border overlap {hit.overlap:.3f}pt")
                failures += 1
    return failures


def verify_same_rank_between_nodes() -> int:
    failures = 0
    graph = ROOT / "tests/graphs/concentrate-demo/same-rank-equivalent-edges-concentrate.dot"
    print("no_regression same_rank_pair_between_nodes")
    for ranker, args in RANKERS.items():
        layout = render_json(DOTS["fixed"], graph, args)
        edges = drawn_edges(layout)
        objects = {node["name"]: node for node in layout["objects"] if "pos" in node}
        low = max(node_box(objects["a"])[1], node_box(objects["b"])[1])
        high = min(node_box(objects["a"])[3], node_box(objects["b"])[3])
        points = edge_points(edges[0]) if edges else []
        head_arrows = arrow_count(edges[0], "_hdraw_") if edges else 0
        tail_arrows = arrow_count(edges[0], "_tdraw_") if edges else 0
        print(
            f"  {ranker}: drawn_edges={len(edges)} head_arrows={head_arrows} "
            f"tail_arrows={tail_arrows} vertical_band=({low:.3f},{high:.3f}) "
            f"control_points={points}"
        )
        if (
            len(edges) != 1
            or head_arrows != 1
            or tail_arrows != 1
            or not points
            or any(
                point[1] < low - POINT_EPSILON
                or point[1] > high + POINT_EPSILON
                for point in points
            )
        ):
            failures += 1
    return failures


def verify_flat_arrow_room() -> int:
    failures = 0
    print("no_regression flat_dir_both_arrowhead_room")
    for ranker, args in RANKERS.items():
        layout = render_json(DOTS["fixed"], ROOT / "tests/2437.dot", args)
        edges = drawn_edges(layout)
        if len(edges) != 1:
            print(f"  {ranker}: REGRESSION drawn_edges={len(edges)}")
            failures += 1
            continue
        edge = edges[0]
        objects = {node["_gvid"]: node for node in layout["objects"] if "pos" in node}
        tail_box = node_box(objects[edge["tail"]])
        head_box = node_box(objects[edge["head"]])
        gap = max(tail_box[0], head_box[0]) - min(tail_box[2], head_box[2])
        tail_arrow = extent(points_from_stream(edge.get("_tdraw_", [])), 0)
        head_arrow = extent(points_from_stream(edge.get("_hdraw_", [])), 0)
        clearance = gap - tail_arrow - head_arrow
        print(
            f"  {ranker}: gap_pt={gap:.3f} tail_arrow_pt={tail_arrow:.3f} "
            f"head_arrow_pt={head_arrow:.3f} clearance_pt={clearance:.3f}"
        )
        if arrow_count(edge, "_tdraw_") != 1 or arrow_count(edge, "_hdraw_") != 1:
            print(f"  {ranker}: REGRESSION expected one arrow at each end")
            failures += 1
        if clearance < -POINT_EPSILON or math.isnan(clearance):
            print(f"  {ranker}: REGRESSION arrow clearance {clearance:.3f}pt")
            failures += 1
    return failures


def verify_cluster_containment() -> int:
    failures = 0
    print("no_regression non_member_nodes_clear_cluster_boxes")
    for ranker, args in RANKERS.items():
        layout = render_json(DOTS["fixed"], ROOT / "tests/1308.dot", args)
        nodes = {
            obj["_gvid"]: obj
            for obj in layout.get("objects", [])
            if "pos" in obj and "width" in obj and "height" in obj
        }
        clusters = [
            obj for obj in layout.get("objects", [])
            if obj.get("name", "").startswith("cluster") and "bb" in obj
        ]
        checked = 0
        for cluster in clusters:
            cluster_box = tuple(float(part) for part in cluster["bb"].split(","))
            members = set(cluster.get("nodes", ()))
            for node_id, node in nodes.items():
                if node_id in members:
                    continue
                checked += 1
                nbox = node_box(node)
                if boxes_overlap(cluster_box, nbox):
                    print(
                        f"  {ranker}: REGRESSION cluster={cluster['name']} "
                        f"box={cluster_box} non_member={node['name']} node_box={nbox}"
                    )
                    failures += 1
        print(f"  {ranker}: clusters={len(clusters)} non_member_node_boxes_checked={checked}")
    return failures


def verify_no_regressions() -> int:
    return (
        verify_same_rank_between_nodes()
        + verify_flat_arrow_room()
        + verify_cluster_containment()
    )


def graph_files() -> list[Path]:
    proc = subprocess.run(["git", "ls-files", "*.dot", "*.gv"], cwd=ROOT, stdout=subprocess.PIPE, text=True, check=True)
    return [ROOT / line for line in sorted(proc.stdout.splitlines())[:IDENTITY_FIXTURE_LIMIT]]


def source_limit(path: Path) -> int:
    config = json.loads((ROOT / "ci/source_length_limits.json").read_text(encoding="utf-8"))
    return int(config["paths"][str(path)])


def line_count(path: Path) -> int:
    with (ROOT / path).open("rb") as handle:
        return sum(1 for _ in handle)


def verify_identity() -> int:
    failures = 0
    attempted = 0
    stats: dict[tuple[str, str], IdentityStats] = defaultdict(IdentityStats)
    changed: list[tuple[str, str, Path]] = []
    graphs = graph_files()
    print(
        f"identity_fixture_subset={len(graphs)} "
        f"minimum_comparable_per_mode={IDENTITY_FIXTURE_MINIMUM}"
    )
    with tempfile.TemporaryDirectory(prefix="wobble-border-identity-") as tmp:
        tmpdir = Path(tmp)
        for graph in graphs:
            rel = graph.relative_to(ROOT)
            for ranker, args in RANKERS.items():
                for concentrate, concentrate_args in CONCENTRATE_MODES.items():
                    mode_args = args + concentrate_args
                    mode = (concentrate, ranker)
                    before = tmpdir / f"before-{attempted}.xdot"
                    after = tmpdir / f"after-{attempted}.xdot"
                    attempted += 1
                    if not render_xdot_to(DOTS["after"], graph, mode_args, before):
                        stats[mode].skipped += 1
                        continue
                    if not render_xdot_to(DOTS["fixed"], graph, mode_args, after):
                        print(f"identity skipped {concentrate} {ranker} {rel}: fixed render failed")
                        stats[mode].skipped += 1
                        failures += 1
                        continue
                    stats[mode].comparable += 1
                    total_comparable = sum(item.comparable for item in stats.values())
                    if total_comparable % 200 == 0:
                        print(f"identity progress comparable={total_comparable}", flush=True)
                    match_kind = identity_match(before, after)
                    if match_kind is not None:
                        if match_kind != "byte":
                            print(f"identity {match_kind} matched {concentrate} {ranker} {rel}", flush=True)
                        stats[mode].matched += 1
                        continue
                    retry_before = tmpdir / f"before-{attempted}-retry.xdot"
                    retry_after = tmpdir / f"after-{attempted}-retry.xdot"
                    if (
                        render_xdot_to(DOTS["after"], graph, mode_args, retry_before)
                        and render_xdot_to(DOTS["fixed"], graph, mode_args, retry_after)
                        and identity_match(retry_before, retry_after) is not None
                    ):
                        print(f"identity retry matched {concentrate} {ranker} {rel}", flush=True)
                        stats[mode].matched += 1
                        continue
                    changed.append((concentrate, ranker, rel))
                    failures += 1
    for concentrate in CONCENTRATE_MODES:
        for ranker in RANKERS:
            item = stats[(concentrate, ranker)]
            print(
                f"byte_identity[{concentrate}][{ranker}]="
                f"{item.matched}/{item.comparable} skipped={item.skipped}"
            )
            if item.comparable < IDENTITY_FIXTURE_MINIMUM:
                print(f"  REGRESSION identity corpus too small for {concentrate} {ranker}")
                failures += 1
            if item.matched != item.comparable:
                failures += 1
    print("changed_graphs:")
    if not changed:
        print("  none")
    for concentrate, ranker, rel in changed:
        print(f"  {concentrate} {ranker} {rel}: unexpected")
    return failures


def main() -> int:
    print(f"commit={subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}")
    print(f"moved_module={MOVED_MODULE}")
    print(
        f"source_length {RATCHET_PATH}: "
        f"{line_count(RATCHET_PATH)} lines <= unchanged limit {source_limit(RATCHET_PATH)}"
    )
    print(f"fixed_dot={DOTS['fixed']}")
    print(f"clearance_rule=an edge is along a cluster border when a rendered control segment is within {BORDER_EPSILON:g}pt of the border and overlaps it by at least {BORDER_CLEARANCE:g}pt")
    for name, dot in DOTS.items():
        if not dot.exists():
            raise SystemExit(f"{name} dot not found: {dot}")
    failures = verify_focus()
    failures += verify_no_regressions()
    failures += verify_identity()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
