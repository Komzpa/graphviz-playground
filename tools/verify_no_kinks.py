#!/usr/bin/env python3
"""Report open-space spline kinks in concentrate-junction renders."""

from __future__ import annotations

import json
import math
import os
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BASELINE_SHA = "73b8ef683bf78ae4f374f508291974daa8839651"
PROTOTYPE_SHA = "8dd559097"
ANGLE_DEGREES = 35.0
CLEAR_RADIUS = 30.0
MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 30
GRAPH = Path("graphs/directed/awilliams.gv")


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Turn:
    tail: str
    head: str
    point: Point
    angle: float
    nearest: float
    nearest_kind: str
    classification: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(args: list[str], cwd: Path, *, stdout=None, text=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=True,
        timeout=TIMEOUT,
        preexec_fn=cap_memory,
    )


def host_run(args: list[str], cwd: Path, *, stdout=None, text=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=True,
        timeout=TIMEOUT,
    )


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        host_run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root)
    return dot


def baseline_dot(root: Path) -> Path:
    override = os.environ.get("NO_KINKS_BASELINE_DOT")
    if override:
        return Path(override)
    worktree = root.parent / f"{root.name}-baseline-{BASELINE_SHA[:9]}"
    if not worktree.exists():
        host_run(["git", "worktree", "add", "--detach", str(worktree), BASELINE_SHA], root)
    build = worktree / "build"
    dot = build / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        host_run(
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
        )
        host_run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree)
    return dot


def upstream_dot(root: Path) -> Path:
    override = os.environ.get("NO_KINKS_UPSTREAM_DOT")
    if override:
        return Path(override)
    sibling = root.parent.parent / "graphviz-upstream-20260728" / "build" / "cmd" / "dot" / "dot_builtins"
    return sibling if sibling.exists() else current_dot(root)


def render_json(dot: Path, graph: Path, root: Path, *extra: str) -> dict:
    proc = run([str(dot), *extra, "-Tjson", str(graph)], root)
    return json.loads(proc.stdout)


def prototype_graph(root: Path, dot: Path, outdir: Path) -> Path:
    script = outdir / "junctionize.py"
    graph = outdir / "awilliams.prototype.gv"
    text = host_run(
        ["git", "show", f"{PROTOTYPE_SHA}:contrib/junction-prototype/junctionize.py"],
        root,
        text=True,
    ).stdout
    script.write_text(text)
    script.chmod(0o755)
    with graph.open("wb") as stdout:
        run([str(script), "--dot", str(dot), str(GRAPH)], root, stdout=stdout)
    return graph


def point(value: list[float]) -> Point:
    return Point(float(value[0]), float(value[1]))


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


def node_obstacles(layout: dict) -> list[tuple[str, Point, float, float]]:
    out = []
    for obj in layout.get("objects", []):
        if "pos" not in obj:
            continue
        center = Point(*(float(part) for part in obj["pos"].split(",", 1)))
        rx = max(float(obj.get("width", 0.0)) * 36.0, 0.01)
        ry = max(float(obj.get("height", 0.0)) * 36.0, 0.01)
        out.append((obj.get("name", "<node>"), center, rx, ry))
    return out


def node_distance(obstacle: tuple[str, Point, float, float], p: Point) -> float:
    _name, center, rx, ry = obstacle
    dx = abs(p.x - center.x)
    dy = abs(p.y - center.y)
    if dx <= rx and dy <= ry:
        return 0.0
    return math.hypot(max(0.0, dx - rx), max(0.0, dy - ry))


def edge_endpoints(layout: dict) -> list[tuple[int, Point]]:
    points = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            pts = [point(p) for p in op.get("points", [])]
            if pts:
                points.append((edge_index, pts[0]))
                points.append((edge_index, pts[-1]))
    return points


def nearest_obstacle(layout: dict, p: Point, own_edge_index: int) -> tuple[float, str]:
    best = (math.inf, "none")
    for obs in node_obstacles(layout):
        d = node_distance(obs, p)
        if d < best[0]:
            best = (d, f"node:{obs[0]}")
    for edge_index, endpoint in edge_endpoints(layout):
        if edge_index == own_edge_index:
            continue
        d = distance(endpoint, p)
        if d < best[0]:
            best = (d, "edge-endpoint")
    return best


def score(layout: dict) -> list[Turn]:
    names = {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}
    turns: list[Turn] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        tail = names[int(edge["tail"])]
        head = names[int(edge["head"])]
        pieces = []
        for op in edge.get("_draw_", []):
            if op.get("op") == "b":
                pts = [point(p) for p in op.get("points", [])]
                if len(pts) >= 4:
                    pieces.append(pts)
        for left, right in zip(pieces, pieces[1:]):
            if distance(left[-1], right[0]) > 0.5:
                continue
            angle = turn_angle(left[-2], left[-1], right[1])
            if angle <= ANGLE_DEGREES:
                continue
            nearest, kind = nearest_obstacle(layout, left[-1], edge_index)
            classification = "junction" if nearest <= CLEAR_RADIUS else "kink"
            turns.append(Turn(tail, head, left[-1], angle, nearest, kind, classification))
    return sorted(turns, key=lambda t: (-t.angle, t.tail, t.head))


def print_score(label: str, turns: list[Turn]) -> None:
    kinks = [turn for turn in turns if turn.classification == "kink"]
    junctions = [turn for turn in turns if turn.classification == "junction"]
    print(
        f"{label}: turns>{ANGLE_DEGREES:g}deg={len(turns)} "
        f"junctions={len(junctions)} kinks={len(kinks)} radius={CLEAR_RADIUS:g}pt"
    )
    for turn in turns:
        print(
            f"  {turn.classification} {turn.tail}->{turn.head} "
            f"at {turn.point.x:.2f},{turn.point.y:.2f} "
            f"angle={turn.angle:.1f} nearest={turn.nearest:.1f}pt {turn.nearest_kind}"
        )


def main() -> int:
    root = repo_root()
    outdir = root / "build" / "no-kinks"
    outdir.mkdir(parents=True, exist_ok=True)

    baseline = baseline_dot(root)
    current = current_dot(root)
    upstream = upstream_dot(root)
    proto = prototype_graph(root, upstream, outdir)

    prototype_layout = render_json(upstream, proto, root)
    baseline_layout = render_json(baseline, GRAPH, root, "-Gconcentrate=true")
    current_layout = render_json(current, GRAPH, root, "-Gconcentrate=true")

    scores = {
        "prototype-upstream": score(prototype_layout),
        f"baseline-{BASELINE_SHA[:9]}": score(baseline_layout),
        "current": score(current_layout),
    }
    for label, turns in scores.items():
        print_score(label, turns)

    baseline_kinks = {(t.tail, t.head, round(t.point.x, 1), round(t.point.y, 1)) for t in scores[f"baseline-{BASELINE_SHA[:9]}"] if t.classification == "kink"}
    new_kinks = [
        t
        for t in scores["current"]
        if t.classification == "kink"
        and (t.tail, t.head, round(t.point.x, 1), round(t.point.y, 1)) not in baseline_kinks
    ]
    if new_kinks:
        print(f"FAIL: {len(new_kinks)} current clear-space kinks were not in {BASELINE_SHA[:9]}")
        return 1
    print("OK verify_no_kinks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
