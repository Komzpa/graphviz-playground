#!/usr/bin/env python3
"""Report concentrate layout process metrics for DOT fixtures.

The tool intentionally separates measured values from unavailable ones. Metrics
that require internal lane/obstacle structures not present in JSON output are
reported as null with a blocker string instead of being guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


def point_ops(edge: dict) -> list[list[tuple[float, float]]]:
    return [
        [(float(x), float(y)) for x, y in operation["points"]]
        for operation in edge.get("_draw_", [])
        if operation.get("op") in {"B", "b", "L"} and len(operation.get("points", [])) >= 2
    ]


def drawn_edges(layout: dict) -> list[dict]:
    return [edge for edge in layout.get("edges", []) if "_draw_" in edge]


def segments(layout: dict) -> list[tuple[int, tuple[float, float], tuple[float, float]]]:
    result: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
    for edge_index, edge in enumerate(drawn_edges(layout)):
        for points in point_ops(edge):
            for start, end in zip(points, points[1:]):
                if math.dist(start, end) > 0.01:
                    result.append((edge_index, start, end))
    return result


def orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def crosses(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False
    return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(
        c, d, a
    ) * orientation(c, d, b) < 0


def crossings_visible(layout: dict) -> int:
    pairs = set()
    edge_segments = segments(layout)
    for left_index, (left_edge, a, b) in enumerate(edge_segments):
        for right_edge, c, d in edge_segments[left_index + 1 :]:
            if left_edge != right_edge and crosses(a, b, c, d):
                pairs.add(tuple(sorted((left_edge, right_edge))))
    return len(pairs)


def visible_ink(layout: dict) -> float:
    return sum(math.dist(start, end) for _, start, end in segments(layout))


def rounded_segment(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[tuple[float, float], tuple[float, float]]:
    left = (round(start[0], 1), round(start[1], 1))
    right = (round(end[0], 1), round(end[1], 1))
    return tuple(sorted((left, right)))  # type: ignore[return-value]


def bundle_shared_length(layout: dict) -> float:
    buckets: dict[tuple[tuple[float, float], tuple[float, float]], list[float]] = {}
    for _, start, end in segments(layout):
        buckets.setdefault(rounded_segment(start, end), []).append(math.dist(start, end))
    return sum(sum(lengths) for lengths in buckets.values() if len(lengths) > 1)


def junctions(layout: dict) -> dict[tuple[float, float], list[tuple[float, float]]]:
    result: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for _, start, end in segments(layout):
        a = (round(start[0], 1), round(start[1], 1))
        b = (round(end[0], 1), round(end[1], 1))
        result.setdefault(a, []).append((b[0] - a[0], b[1] - a[1]))
        result.setdefault(b, []).append((a[0] - b[0], a[1] - b[1]))
    return {point: vectors for point, vectors in result.items() if len(vectors) > 2}


def vector_angle(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_len = math.hypot(left[0], left[1])
    right_len = math.hypot(right[0], right[1])
    if left_len == 0 or right_len == 0:
        return 0.0
    cosine = max(-1.0, min(1.0, (left[0] * right[0] + left[1] * right[1]) / (left_len * right_len)))
    return math.degrees(math.acos(cosine))


def max_junction_angle(layout: dict) -> float | None:
    angles = [
        vector_angle(left, right)
        for vectors in junctions(layout).values()
        for index, left in enumerate(vectors)
        for right in vectors[index + 1 :]
    ]
    return max(angles) if angles else None


def max_tangent_discontinuity(layout: dict) -> float | None:
    angles = []
    for edge in drawn_edges(layout):
        for points in point_ops(edge):
            for left, middle, right in zip(points, points[1:], points[2:]):
                angles.append(
                    vector_angle(
                        (middle[0] - left[0], middle[1] - left[1]),
                        (right[0] - middle[0], right[1] - middle[1]),
                    )
                )
    return max(angles) if angles else None


def parse_oracle(stderr: str) -> tuple[int | None, int | None]:
    match = re.search(r"candidates_accepted=(\d+)\s+candidates_rejected=(\d+)", stderr)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def run_dot(dot: Path, fixture: Path, oracle: bool, timeout: float) -> tuple[dict, str, float]:
    env = os.environ.copy()
    if oracle:
        env.setdefault("GV_CONCENTRATE_ORACLE", "1")
    start = time.monotonic()
    completed = subprocess.run(
        [str(dot), "-Tjson", str(fixture)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )
    return json.loads(completed.stdout), completed.stderr, time.monotonic() - start


def measure_fixture(dot: Path, fixture: Path, oracle: bool, timeout: float) -> dict:
    layout, stderr, runtime = run_dot(dot, fixture, oracle, timeout)
    accepted, rejected = parse_oracle(stderr)
    unsupported = "requires internal mincross/lane/label-obstacle structures"
    return {
        "fixture": str(fixture),
        "mincross_logical": None,
        "crossings_visible": crossings_visible(layout),
        "bundle_shared_length": round(bundle_shared_length(layout), 3),
        "visible_ink": round(visible_ink(layout), 3),
        "junction_count": len(junctions(layout)),
        "max_junction_angle": max_junction_angle(layout),
        "max_tangent_discontinuity": max_tangent_discontinuity(layout),
        "bbox": layout.get("bb"),
        "label_overlap": None,
        "node_obstacle_intersections": None,
        "runtime": round(runtime, 6),
        "candidates_accepted": accepted,
        "candidates_rejected": rejected,
        "blocked_metrics": {
            "mincross_logical": unsupported,
            "label_overlap": unsupported,
            "node_obstacle_intersections": unsupported,
        },
    }


def print_tsv(rows: Iterable[dict]) -> None:
    fields = [
        "fixture",
        "mincross_logical",
        "crossings_visible",
        "bundle_shared_length",
        "visible_ink",
        "junction_count",
        "max_junction_angle",
        "max_tangent_discontinuity",
        "bbox",
        "label_overlap",
        "node_obstacle_intersections",
        "runtime",
        "candidates_accepted",
        "candidates_rejected",
    ]
    print("\t".join(fields))
    for row in rows:
        print("\t".join("" if row[field] is None else str(row[field]) for field in fields))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+", type=Path)
    parser.add_argument("--dot", type=Path, default=Path("dot"))
    parser.add_argument("--format", choices=("json", "tsv"), default="tsv")
    parser.add_argument("--oracle", action="store_true", default=True)
    parser.add_argument("--no-oracle", dest="oracle", action="store_false")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = [
        measure_fixture(args.dot, fixture, args.oracle, args.timeout)
        for fixture in args.fixtures
    ]
    if args.format == "json":
        json.dump(rows, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print_tsv(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
