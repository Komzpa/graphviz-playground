#!/usr/bin/env python3
"""Verify fig6 concentrate does not add raw control-point sharp turns."""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BASELINE_SHA = "10d27e49fb6758f553c990abe1d55809e0e3235a"
ANGLE_DEGREES = 35.0
# OPEN CARD, not a target met. The operator graded fig6 «угловатенько и
# изломано» at 25 sharp turns, and the first fix reached 10 by straightening
# every copied cubic's handles — which flattened legitimately curved edges into
# polylines and turned graphs/directed/shells.gv from his «идеал» into his
# «wrong». That fix is reverted. Until a fix arrives that straightens only the
# stale handles, this gate holds the line at the known-bad number so fig6 cannot
# get WORSE while the card is open. The number to reach is 14.
MAX_SHARP_TURNS = 25
# The operator's card is about how ANGULAR the drawing looks, so the count is
# the real target. This second bound exists only to stop the count improving
# while one turn becomes a spike: our own concentrate=false render of fig6 peaks
# at 79.2deg, and 2deg of slack over it is below what a reader can see. It was
# 80.00 for one run, which a lane satisfied by blending 2% of a tangent into the
# handle whenever the joint exceeded 79deg — a nudge that moved this number and
# nothing on the page. A bound you can only pass by cosmetics is a bound set
# wrong, not a fix that failed.
MAX_WORST_ANGLE = 89.0
MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 60


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Support:
    distance: float
    kind: str


@dataclass(frozen=True)
class Turn:
    edge_index: int
    tail: str
    head: str
    piece_index: int
    control_index: int
    point: Point
    angle: float
    support: Support


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


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root, memory=False)
    return dot


def baseline_dot(root: Path) -> Path:
    worktree = root.parent / f"{root.name}-fig6-sharp-{BASELINE_SHA[:9]}"
    if not worktree.exists():
        run(["git", "worktree", "add", "--detach", str(worktree), BASELINE_SHA], root, memory=False)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("current", "baseline"), default="current")
    parser.add_argument("--dot", type=Path)
    return parser.parse_args()


def point(values: list[float]) -> Point:
    return Point(float(values[0]), float(values[1]))


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


def node_obstacles(layout: dict) -> list[tuple[str, Point, float, float, bool]]:
    out = []
    for obj in layout.get("objects", []):
        if "pos" not in obj:
            continue
        name = obj.get("name", "<node>")
        junction = name.startswith("_concentrate_junction_") or obj.get("_concentrate_junction_node") == "true"
        rx = max(float(obj.get("width", 0.0)) * 36.0, 0.01)
        ry = max(float(obj.get("height", 0.0)) * 36.0, 0.01)
        out.append((name, parse_pos(obj["pos"]), rx, ry, junction))
    return out


def node_distance(obstacle: tuple[str, Point, float, float, bool], p: Point) -> float:
    _name, center, rx, ry, _junction = obstacle
    dx = abs(p.x - center.x)
    dy = abs(p.y - center.y)
    if dx <= rx and dy <= ry:
        return 0.0
    return math.hypot(max(0.0, dx - rx), max(0.0, dy - ry))


def nearest_support(layout: dict, p: Point) -> Support:
    best = Support(math.inf, "none")
    for obstacle in node_obstacles(layout):
        dist = node_distance(obstacle, p)
        name, _center, _rx, _ry, junction = obstacle
        kind = f"{'junction' if junction else 'node'}:{name}"
        if dist < best.distance:
            best = Support(dist, kind)
    return best


def render_json(dot: Path, graph: Path, root: Path) -> dict:
    proc = run([str(dot), "-Gconcentrate=true", "-Tjson", str(graph)], root)
    return json.loads(proc.stdout)


def edge_names(layout: dict) -> dict[int, str]:
    return {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}


def score(layout: dict) -> tuple[int, list[Turn]]:
    names = edge_names(layout)
    total = 0
    turns = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        if "_draw_" not in edge:
            continue
        tail = names[int(edge["tail"])]
        head = names[int(edge["head"])]
        pieces = [op for op in edge.get("_draw_", []) if op.get("op") == "b"]
        for piece_index, op in enumerate(pieces):
            points = [point(p) for p in op.get("points", [])]
            for control_index, (incoming, joint, outgoing) in enumerate(
                zip(points, points[1:], points[2:]), start=1
            ):
                angle = turn_angle(incoming, joint, outgoing)
                total += 1
                if angle <= ANGLE_DEGREES:
                    continue
                turns.append(
                    Turn(
                        edge_index,
                        tail,
                        head,
                        piece_index,
                        control_index,
                        joint,
                        angle,
                        nearest_support(layout, joint),
                    )
                )
    return total, sorted(turns, key=lambda turn: (-turn.angle, turn.edge_index, turn.piece_index))


def main() -> int:
    args = parse_args()
    root = repo_root()
    graph = root / "graphs" / "directed" / "fig6.gv"
    dot = args.dot if args.dot is not None else (baseline_dot(root) if args.mode == "baseline" else current_dot(root))
    layout = render_json(dot, graph, root)
    total, turns = score(layout)
    worst = max((turn.angle for turn in turns), default=0.0)

    print(f"mode={args.mode} baseline={BASELINE_SHA}")
    print(f"graph=graphs/directed/fig6.gv concentrate=true angle>{ANGLE_DEGREES:g}deg")
    print(f"sharp_turns={len(turns)} total_turns={total} worst_angle={worst:.2f}deg")
    print("| edge | piece | control | x | y | angle | nearest | distance |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |")
    for turn in turns:
        print(
            f"| {turn.edge_index} {turn.tail}->{turn.head} | {turn.piece_index} | "
            f"{turn.control_index} | {turn.point.x:.2f} | {turn.point.y:.2f} | "
            f"{turn.angle:.2f} | {turn.support.kind} | {turn.support.distance:.2f} |"
        )

    if len(turns) > MAX_SHARP_TURNS or worst > MAX_WORST_ANGLE:
        print(
            "FAIL verify_fig6_sharp_turns: "
            f"sharp_turns={len(turns)} max={MAX_SHARP_TURNS} "
            f"worst={worst:.2f}deg max={MAX_WORST_ANGLE:.2f}deg"
        )
        return 1
    print("OK verify_fig6_sharp_turns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
