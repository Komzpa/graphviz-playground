#!/usr/bin/env python3
"""Verify drawn arrowheads attach to the clipped spline endpoint."""

from __future__ import annotations

import json
import math
import os
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOT = ROOT / "build" / "cmd" / "dot" / "dot_builtins"
FIXTURES = (
    Path("graphs/directed/pgram.gv"),
    Path("tests/graphs/concentrate-demo/ellipse-arrowhead-overlap.dot"),
)
RANKERS = (("default", ()), ("newrank", ("-Gnewrank=true",)))
TIMEOUT_SECONDS = 30
MEMORY_LIMIT_BYTES = 2097152 * 1024
MAX_ENDPOINT_DISTANCE = 1.0
MAX_TANGENT_ANGLE = 5.0
COINCIDENT_CENTROID_DISTANCE = 2.0
MAX_COINCIDENT_GROUP = 2
MAX_ARROWHEAD_OVERLAP_AREA = 1.0
BASELINE_ARROW_COUNTS = {
    ("graphs/directed/pgram.gv", "default"): 53,
    ("graphs/directed/pgram.gv", "newrank"): 53,
    ("tests/graphs/concentrate-demo/ellipse-arrowhead-overlap.dot", "default"): 5,
    ("tests/graphs/concentrate-demo/ellipse-arrowhead-overlap.dot", "newrank"): 5,
}


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    @classmethod
    def from_pair(cls, pair: list[float]) -> "Point":
        return cls(float(pair[0]), float(pair[1]))


@dataclass(frozen=True)
class Arrowhead:
    edge_name: str
    label: str
    node_gvid: int
    node_has_ellipse_outline: bool
    centroid: Point
    polygon: list[Point]


def limit_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))


def render_json(dot: Path, graph: Path, ranker_args: tuple[str, ...]) -> dict[str, Any]:
    cmd = [str(dot), *ranker_args, "-Gconcentrate=true", "-Tjson", str(graph)]
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=TIMEOUT_SECONDS,
        preexec_fn=limit_memory,
    )
    return json.loads(completed.stdout)


def point_distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def distance_to_segment(point: Point, start: Point, end: Point) -> float:
    vx = end.x - start.x
    vy = end.y - start.y
    length2 = vx * vx + vy * vy
    if length2 == 0:
        return point_distance(point, start)
    t = ((point.x - start.x) * vx + (point.y - start.y) * vy) / length2
    t = max(0.0, min(1.0, t))
    nearest = Point(start.x + t * vx, start.y + t * vy)
    return point_distance(point, nearest)


def distance_to_polygon(point: Point, polygon: list[Point]) -> float:
    return min(
        distance_to_segment(point, polygon[i], polygon[(i + 1) % len(polygon)])
        for i in range(len(polygon))
    )


def parse_pos_endpoint(pos: str, marker: str) -> Point | None:
    for item in pos.split():
        parts = item.split(",")
        if parts[0] == marker and len(parts) == 3:
            return Point(float(parts[1]), float(parts[2]))
    return None


def drawn_polygon(node: dict[str, Any]) -> list[Point] | None:
    for op in node.get("_draw_", []):
        if op.get("op") in {"p", "P"}:
            return [Point.from_pair(pair) for pair in op.get("points", [])]
    return None


def ellipse_boundary_distance(node: dict[str, Any], point: Point) -> float | None:
    for op in node.get("_draw_", []):
        if op.get("op") not in {"e", "E"}:
            continue
        rect = op.get("rect", [])
        if len(rect) != 4:
            continue
        center = Point(float(rect[0]), float(rect[1]))
        rx = max(float(rect[2]), 0.01)
        ry = max(float(rect[3]), 0.01)
        dx = point.x - center.x
        dy = point.y - center.y
        norm = math.hypot(dx / rx, dy / ry)
        if norm == 0:
            return min(rx, ry)
        boundary = Point(center.x + dx / norm, center.y + dy / norm)
        return point_distance(point, boundary)
    return None


def node_outline_distance(node: dict[str, Any], point: Point) -> float:
    polygon = drawn_polygon(node)
    if polygon:
        return distance_to_polygon(point, polygon)
    ellipse_distance = ellipse_boundary_distance(node, point)
    if ellipse_distance is not None:
        return ellipse_distance
    raise ValueError(f"node {node.get('name')} has no supported drawn outline")


def bezier_points(edge: dict[str, Any]) -> list[Point]:
    points: list[Point] = []
    for op in edge.get("_draw_", []):
        if op.get("op") == "b":
            points.extend(Point.from_pair(pair) for pair in op.get("points", []))
    return points


def polygon_points(edge: dict[str, Any], stream: str) -> list[Point] | None:
    for op in edge.get(stream, []):
        if op.get("op") == "P":
            return [Point.from_pair(pair) for pair in op.get("points", [])]
    return None


def arrow_axis(polygon: list[Point], endpoint: Point) -> tuple[Point, Point]:
    tip_index = min(range(len(polygon)), key=lambda i: point_distance(polygon[i], endpoint))
    tip = polygon[tip_index]
    base = [point for i, point in enumerate(polygon) if i != tip_index]
    midpoint = Point(
        sum(point.x for point in base) / len(base),
        sum(point.y for point in base) / len(base),
    )
    return midpoint, tip


def angle_between(a: Point, b: Point) -> float:
    alen = math.hypot(a.x, a.y)
    blen = math.hypot(b.x, b.y)
    if alen == 0 or blen == 0:
        return 0.0
    cosine = (a.x * b.x + a.y * b.y) / (alen * blen)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def fmt_point(point: Point) -> str:
    return f"({point.x:.2f},{point.y:.2f})"


def fmt_polygon(points: list[Point]) -> str:
    return "[" + " ".join(fmt_point(point) for point in points) + "]"


def polygon_centroid(points: list[Point]) -> Point:
    return Point(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
    )


def polygon_bbox(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def coincident_groups(arrows: list[Arrowhead]) -> list[list[int]]:
    groups: set[tuple[int, ...]] = set()
    for left, left_arrow in enumerate(arrows):
        group = [left]
        for right, right_arrow in enumerate(arrows):
            if left == right:
                continue
            if (
                point_distance(left_arrow.centroid, right_arrow.centroid)
                <= COINCIDENT_CENTROID_DISTANCE
            ):
                group.append(right)
        if len(group) > 1:
            groups.add(tuple(sorted(group)))
    return [list(group) for group in sorted(groups)]


def overlapping_head_groups(arrows: list[Arrowhead]) -> list[tuple[int, int, float]]:
    overlaps: list[tuple[int, int, float]] = []
    head_arrows = [
        (index, arrow) for index, arrow in enumerate(arrows) if arrow.label == "head"
    ]
    for left, left_arrow in head_arrows:
        left_bbox = polygon_bbox(left_arrow.polygon)
        for right, right_arrow in head_arrows:
            if right <= left or right_arrow.node_gvid != left_arrow.node_gvid:
                continue
            if (
                not left_arrow.node_has_ellipse_outline
                or not right_arrow.node_has_ellipse_outline
            ):
                continue
            area = bbox_overlap_area(left_bbox, polygon_bbox(right_arrow.polygon))
            if area > MAX_ARROWHEAD_OVERLAP_AREA:
                overlaps.append((left, right, area))
    return overlaps


def check_layout(graph: Path, ranker: str, layout: dict[str, Any]) -> int:
    objects = {int(obj["_gvid"]): obj for obj in layout.get("objects", []) if "_gvid" in obj}
    failures = 0
    arrow_count = 0
    arrows: list[Arrowhead] = []
    for edge in layout.get("edges", []):
        points = bezier_points(edge)
        if len(points) < 2:
            continue
        tail = objects[int(edge["tail"])]
        head = objects[int(edge["head"])]
        edge_name = f"{tail['name']}->{head['name']}"
        for label, stream, marker, node, tangent in (
            ("tail", "_tdraw_", "s", tail, Point(points[1].x - points[0].x, points[1].y - points[0].y)),
            ("head", "_hdraw_", "e", head, Point(points[-1].x - points[-2].x, points[-1].y - points[-2].y)),
        ):
            polygon = polygon_points(edge, stream)
            endpoint = parse_pos_endpoint(edge.get("pos", ""), marker)
            if polygon is None or endpoint is None:
                continue
            arrow_count += 1
            arrows.append(
                Arrowhead(
                    edge_name=edge_name,
                    label=label,
                    node_gvid=int(node["_gvid"]),
                    node_has_ellipse_outline=any(
                        op.get("op") in {"e", "E"} for op in node.get("_draw_", [])
                    ),
                    centroid=polygon_centroid(polygon),
                    polygon=polygon,
                )
            )
            base, tip = arrow_axis(polygon, endpoint)
            arrow = Point(tip.x - base.x, tip.y - base.y)
            distance = node_outline_distance(node, endpoint)
            angle = angle_between(arrow, tangent)
            status = "OK"
            if distance > MAX_ENDPOINT_DISTANCE or angle > MAX_TANGENT_ANGLE:
                status = "FAIL"
                failures += 1
            print(
                f"{status} {graph} [{ranker}] {edge_name} {label}: "
                f"polygon={fmt_polygon(polygon)} endpoint={fmt_point(endpoint)} "
                f"node={node['name']} outline_distance={distance:.2f}pt "
                f"tangent_angle={angle:.2f}deg"
            )
    print(
        f"checked {arrow_count} drawn arrowheads for {graph} [{ranker}] "
        f"with tolerances distance<={MAX_ENDPOINT_DISTANCE:.2f}pt "
        f"angle<={MAX_TANGENT_ANGLE:.2f}deg"
    )
    baseline = BASELINE_ARROW_COUNTS[(graph.as_posix(), ranker)]
    print(
        f"drawn arrowhead count control for {graph} [{ranker}]: "
        f"current={arrow_count} baseline={baseline}"
    )
    if arrow_count < baseline:
        print(
            f"FAIL {graph} [{ranker}] drawn arrowhead count {arrow_count} "
            f"fell below baseline {baseline}"
        )
        failures += 1
    groups = coincident_groups(arrows)
    coincident_count = len({index for group in groups for index in group})
    max_group = max((len(group) for group in groups), default=1)
    print(
        f"coincident arrowhead groups for {graph} [{ranker}] "
        f"distance<={COINCIDENT_CENTROID_DISTANCE:.2f}pt: "
        f"counted_arrowheads={coincident_count} groups={len(groups)} "
        f"max_group={max_group}"
    )
    for group in groups:
        centroid = polygon_centroid([arrows[index].centroid for index in group])
        members = "; ".join(
            f"{arrows[index].edge_name} {arrows[index].label}" for index in group
        )
        print(f"  group size={len(group)} centroid={fmt_point(centroid)} {members}")
    if max_group > MAX_COINCIDENT_GROUP:
        print(
            f"FAIL {graph} [{ranker}] coincident arrowhead group "
            f"{max_group} exceeds {MAX_COINCIDENT_GROUP}"
        )
        failures += 1
    overlaps = overlapping_head_groups(arrows)
    print(
        f"overlapping head-arrow pairs for {graph} [{ranker}] "
        f"area>{MAX_ARROWHEAD_OVERLAP_AREA:.2f}pt^2: pairs={len(overlaps)}"
    )
    for left, right, area in overlaps:
        print(
            f"  overlap area={area:.2f} "
            f"{arrows[left].edge_name} head; {arrows[right].edge_name} head"
        )
    if overlaps:
        print(f"FAIL {graph} [{ranker}] overlapping head arrowheads remain")
        failures += 1
    return failures


def main() -> int:
    dot = Path(os.environ.get("DOT", DEFAULT_DOT))
    if not dot.exists():
        print(f"dot binary not found: {dot}", file=sys.stderr)
        return 2
    failures = 0
    for graph in FIXTURES:
        for ranker, args in RANKERS:
            layout = render_json(dot, graph, args)
            failures += check_layout(graph, ranker, layout)
    if failures:
        print(f"FAIL verify_arrow_attachment: failures={failures}")
        return 1
    print("OK verify_arrow_attachment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
