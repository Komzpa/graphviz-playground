#!/usr/bin/env python3
"""Verify concentrated junction burst arms leave from the junction point."""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


BASELINE_SHA = "e1b2e4bc83df341910ca353bba715f74420a9bae"
PROTOTYPE_SHA = "8dd559097"
UPSTREAM_SHA = "aef8a6fd8"
ANGLE_DEGREES = 35.0
JUNCTION_SUPPORT_RADIUS = 6.0
MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 60
SAMPLES_PER_CUBIC = 24


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Junction:
    name: str
    center: Point
    radius: float


@dataclass(frozen=True)
class Arm:
    junction: str
    tail: str
    head: str
    edge_index: int
    piece_index: int
    start_distance: float
    turn: float


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(
    args: list[str],
    cwd: Path,
    *,
    stdout=None,
    text: bool = False,
    memory: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=True,
        timeout=TIMEOUT,
        preexec_fn=cap_memory if memory else None,
    )


def configured_dot(root: Path, sha: str) -> Path:
    worktree = root.parent / f"{root.name}-junction-burst-{sha[:9]}"
    if not worktree.exists():
        run(["git", "worktree", "add", "--detach", str(worktree), sha], root, memory=False)
    dot = worktree / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        run(
            [
                "cmake",
                "-S",
                ".",
                "-B",
                "build",
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=ON",
                "-DBUILD_TESTING=ON",
            ],
            worktree,
            memory=False,
        )
        run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree, memory=False)
    return dot


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root, memory=False)
    return dot


def point(value: list[float]) -> Point:
    return Point(float(value[0]), float(value[1]))


def parse_pos(value: str) -> Point:
    x, y = value.split(",", 1)
    return Point(float(x), float(y))


def distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def turn_angle(incoming: Point, joint: Point, outgoing: Point) -> float:
    vin = (joint.x - incoming.x, joint.y - incoming.y)
    vout = (outgoing.x - joint.x, outgoing.y - joint.y)
    nin = math.hypot(*vin)
    nout = math.hypot(*vout)
    if nin < 1e-6 or nout < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, (vin[0] * vout[0] + vin[1] * vout[1]) / (nin * nout)))
    return math.degrees(math.acos(cos))


def bezier_point(points: list[Point], t: float) -> Point:
    omt = 1.0 - t
    return Point(
        omt**3 * points[0].x
        + 3.0 * omt**2 * t * points[1].x
        + 3.0 * omt * t**2 * points[2].x
        + t**3 * points[3].x,
        omt**3 * points[0].y
        + 3.0 * omt**2 * t * points[1].y
        + 3.0 * omt * t**2 * points[2].y
        + t**3 * points[3].y,
    )


def flatten_bezier(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for offset in range(0, len(points) - 3, 3):
        cubic = points[offset : offset + 4]
        start = 0 if offset == 0 else 1
        for step in range(start, SAMPLES_PER_CUBIC + 1):
            out.append(bezier_point(cubic, step / SAMPLES_PER_CUBIC))
    return out


def is_junction_node(node: dict) -> bool:
    name = node.get("name", "")
    return (
        name.startswith("__junction")
        or name.startswith("_concentrate_junction_")
        or node.get("_concentrate_junction_node") == "true"
    )


def junctions(layout: dict) -> list[Junction]:
    out = []
    for obj in layout.get("objects", []):
        if "pos" not in obj or not is_junction_node(obj):
            continue
        radius = max(float(obj.get("width", 0.0)), float(obj.get("height", 0.0))) * 36.0 / 2.0
        out.append(Junction(obj["name"], parse_pos(obj["pos"]), radius))
    return out


def penwidth(edge: dict) -> float:
    try:
        return float(edge.get("penwidth", 1.0))
    except ValueError:
        return 1.0


def support_polyline(polyline: list[Point], center: Point) -> list[Point]:
    prefix = [polyline[0]]
    for p in polyline[1:]:
        prefix.append(p)
        if distance(p, center) > JUNCTION_SUPPORT_RADIUS:
            break
    return prefix


def support_turn(polyline: list[Point], center: Point) -> float:
    local = support_polyline(polyline, center)
    return sum(turn_angle(a, b, c) for a, b, c in zip(local, local[1:], local[2:]))


def _has_nonempty_color(value: object) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped != "" and stripped.lower() != "none"


def has_filled_ellipse_marker(node: dict) -> bool:
    draw = node.get("_draw_", [])
    if not isinstance(draw, list) or not draw:
        return False
    has_ellipse = False
    has_fill = False
    for op in draw:
        if not isinstance(op, dict):
            continue
        op_type = op.get("op", "")
        if op_type in {"e", "E"}:
            has_ellipse = True
        if _has_nonempty_color(op.get("fill")) or _has_nonempty_color(op.get("fillcolor")):
            has_fill = True
    return has_ellipse and has_fill


def edge_names(layout: dict) -> dict[int, str]:
    return {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}


def candidate_edge(edge: dict, names: dict[int, str]) -> bool:
    if edge.get("_concentrate_junction_original") == "true":
        return True
    return is_junction_name(names[int(edge["tail"])]) or is_junction_name(names[int(edge["head"])])


def is_junction_name(name: str) -> bool:
    return name.startswith("__junction") or name.startswith("_concentrate_junction_")


def arms(layout: dict) -> list[Arm]:
    js = junctions(layout)
    names = edge_names(layout)
    found = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        if "_draw_" not in edge or not candidate_edge(edge, names):
            continue
        tail = names[int(edge["tail"])]
        head = names[int(edge["head"])]
        limit_pad = penwidth(edge)
        for piece_index, op in enumerate(op for op in edge["_draw_"] if op.get("op") == "b"):
            pts = [point(p) for p in op.get("points", [])]
            if len(pts) < 4:
                continue
            endpoints = [(distance(pts[0], j.center), False, j) for j in js]
            endpoints.extend((distance(pts[-1], j.center), True, j) for j in js)
            start_distance, reverse, junction = min(endpoints, key=lambda item: item[0])
            start_limit = junction.radius + limit_pad
            if start_distance > start_limit:
                continue
            oriented = list(reversed(pts)) if reverse else pts
            polyline = flatten_bezier(oriented)
            found.append(
                Arm(
                    junction=junction.name,
                    tail=tail,
                    head=head,
                    edge_index=edge_index,
                    piece_index=piece_index,
                    start_distance=start_distance,
                    turn=support_turn(polyline, junction.center),
                )
            )
    return found


def original_junction_count(layout: dict) -> int:
    names = edge_names(layout)
    count = 0
    for edge in layout.get("edges", []):
        if candidate_edge(edge, names):
            count += 1
    return count


def junction_node_map(layout: dict) -> dict[str, dict]:
    return {
        obj["name"]: obj
        for obj in layout.get("objects", [])
        if "name" in obj and "pos" in obj and is_junction_node(obj)
    }


def render_json(dot: Path, graph: Path, root: Path, *extra: str) -> dict:
    proc = run([str(dot), *extra, "-Tjson", str(graph)], root)
    return json.loads(proc.stdout)


def prototype_graph(root: Path, graph: Path, dot: Path, outdir: Path) -> Path:
    script = outdir / "junctionize.py"
    script.write_text(run(["git", "show", f"{PROTOTYPE_SHA}:contrib/junction-prototype/junctionize.py"], root, text=True, memory=False).stdout)
    script.chmod(0o755)
    out = outdir / "awilliams.prototype.gv"
    with out.open("wb") as stdout:
        run([str(script), "--dot", str(dot), str(graph)], root, stdout=stdout)
    return out


def layout_for_mode(root: Path, mode: str) -> tuple[str, dict]:
    graph = root / "graphs" / "directed" / "awilliams.gv"
    if mode == "current":
        return "current", render_json(current_dot(root), graph, root, "-Gconcentrate=true")
    if mode == "baseline":
        return BASELINE_SHA[:9], render_json(configured_dot(root, BASELINE_SHA), graph, root, "-Gconcentrate=true")
    if mode == "prototype":
        upstream = configured_dot(root, UPSTREAM_SHA)
        with tempfile.TemporaryDirectory(prefix="junction-burst-") as tmp:
            rewritten = prototype_graph(root, graph, upstream, Path(tmp))
            return f"prototype-{PROTOTYPE_SHA}", render_json(upstream, rewritten, root)
    raise ValueError(mode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("current", "baseline", "prototype"), default="current")
    args = parser.parse_args()

    root = repo_root()
    label, layout = layout_for_mode(root, args.mode)
    js = junctions(layout)
    junction_nodes = junction_node_map(layout)
    found = arms(layout)
    failures = []
    by_junction = {j.name: [] for j in js}
    for arm in found:
        by_junction.setdefault(arm.junction, []).append(arm)

    print(f"mode={label} graph=graphs/directed/awilliams.gv")
    print("| junction | arms | max_start_pt | max_turn_deg | status |")
    print("| --- | ---: | ---: | ---: | --- |")
    for name, junction_arms in sorted(by_junction.items()):
        if not junction_arms:
            continue
        max_start = max(arm.start_distance for arm in junction_arms)
        max_turn = max(arm.turn for arm in junction_arms)
        status = "ok"
        bad = [arm for arm in junction_arms if arm.turn > ANGLE_DEGREES]
        if bad:
            status = "FAIL"
            failures.extend(bad)
        print(f"| {name} | {len(junction_arms)} | {max_start:.2f} | {max_turn:.2f} | {status} |")

    if not js:
        print("FAIL verify_junction_burst: no junction nodes")
        return 1
    if not found:
        print(
            "FAIL verify_junction_burst: no junction arms within the rendered point "
            f"plus one pen width; candidate_edges={original_junction_count(layout)}"
        )
        return 1
    marker_failures = [name for name in sorted(junction_nodes) if not has_filled_ellipse_marker(junction_nodes[name])]
    if marker_failures:
        print(
            "FAIL verify_junction_burst: junction marker missing filled ellipse "
            f"in _draw_ for {', '.join(marker_failures)}"
        )
        return 1
    if failures:
        print(f"FAIL verify_junction_burst: failures={len(failures)}")
        for arm in sorted(failures, key=lambda item: (-item.turn, item.junction, item.edge_index))[:20]:
            print(
                f"  {arm.junction} {arm.tail}->{arm.head} edge={arm.edge_index} "
                f"piece={arm.piece_index} start={arm.start_distance:.2f}pt turn={arm.turn:.2f}deg"
            )
        return 1
    print("OK verify_junction_burst")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
