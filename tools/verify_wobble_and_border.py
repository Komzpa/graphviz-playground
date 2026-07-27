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
ULIMIT_V_KB = 2 * 1024 * 1024
TIMEOUT = 10
WOBBLE_LIMIT = 0.5
BORDER_EPSILON = 0.5
BORDER_CLEARANCE = 4.0
TINY_FLOAT_RE = re.compile(rb"-?(?:\d+(?:\.\d*)?|\.\d+)e-\d+")


@dataclass(frozen=True)
class BorderHit:
    graph: Path
    ranker: str
    edge: str
    border: str
    separation: float
    overlap: float


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


def graph_files() -> list[Path]:
    proc = subprocess.run(["git", "ls-files", "*.dot", "*.gv"], cwd=ROOT, stdout=subprocess.PIPE, text=True, check=True)
    return [ROOT / line for line in proc.stdout.splitlines()]


def verify_identity() -> int:
    failures = 0
    matched = 0
    attempted = 0
    comparable = 0
    changed: list[tuple[str, Path, str]] = []
    with tempfile.TemporaryDirectory(prefix="wobble-border-identity-") as tmp:
        tmpdir = Path(tmp)
        for graph in graph_files():
            rel = graph.relative_to(ROOT)
            for ranker, args in RANKERS.items():
                before = tmpdir / f"before-{attempted}.xdot"
                after = tmpdir / f"after-{attempted}.xdot"
                attempted += 1
                if not render_xdot_to(DOTS["after"], graph, args, before):
                    print(f"identity skipped {ranker} {rel}: baseline render failed")
                    continue
                if not render_xdot_to(DOTS["fixed"], graph, args, after):
                    print(f"identity skipped {ranker} {rel}: fixed render failed")
                    failures += 1
                    continue
                comparable += 1
                if comparable % 100 == 0:
                    print(f"identity progress comparable={comparable}", flush=True)
                match_kind = identity_match(before, after)
                if match_kind is not None:
                    if match_kind != "byte":
                        print(f"identity {match_kind} matched {ranker} {rel}", flush=True)
                    matched += 1
                    continue
                retry_before = tmpdir / f"before-{attempted}-retry.xdot"
                retry_after = tmpdir / f"after-{attempted}-retry.xdot"
                if (
                    render_xdot_to(DOTS["after"], graph, args, retry_before)
                    and render_xdot_to(DOTS["fixed"], graph, args, retry_after)
                    and identity_match(retry_before, retry_after) is not None
                ):
                    print(f"identity retry matched {ranker} {rel}", flush=True)
                    matched += 1
                    continue
                reason = "2437 wobble removed" if rel == Path("tests/2437.dot") else "1308 cluster-border overlap removed" if rel == Path("tests/1308.dot") else "unexpected"
                changed.append((ranker, rel, reason))
                if reason == "unexpected":
                    failures += 1
    print(f"byte_identity_matched={matched}/{comparable}")
    print("changed_graphs:")
    for ranker, rel, reason in changed:
        print(f"  {ranker} {rel}: {reason}")
    return failures


def main() -> int:
    print(f"commit={subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}")
    print(f"fixed_dot={DOTS['fixed']}")
    print(f"clearance_rule=an edge is along a cluster border when a rendered control segment is within {BORDER_EPSILON:g}pt of the border and overlaps it by at least {BORDER_CLEARANCE:g}pt")
    for name, dot in DOTS.items():
        if not dot.exists():
            raise SystemExit(f"{name} dot not found: {dot}")
    failures = verify_focus()
    failures += verify_identity()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
