#!/usr/bin/env python3
"""Measure concentrated-edge readability from drawn JSON geometry."""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MEMORY_KIB = 2_097_152
TIMEOUT = 60.0
CURVE_STEPS = 48
LABEL_PAD = 3.0
NODE_PAD = 1.0
EPS = 1e-7


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    edge_index: int
    a: Point
    b: Point


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    rx: float
    ry: float

    def contains(self, p: Point) -> bool:
        return abs(p.x - self.cx) <= self.rx and abs(p.y - self.cy) <= self.ry


def cap_memory() -> None:
    limit = MEMORY_KIB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def render_json(dot: Path, graph: Path, flag: str) -> dict[str, Any]:
    args = [str(dot), flag, "-Tjson", str(graph)]
    with tempfile.NamedTemporaryFile(prefix="mesivo-", suffix=".json") as out:
        proc = subprocess.run(
            args,
            stdout=out,
            stderr=subprocess.PIPE,
            check=False,
            timeout=TIMEOUT,
            preexec_fn=cap_memory,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"dot failed rc={proc.returncode}: {stderr}")
        out.seek(0)
        return json.load(out)


def parse_point_pair(raw: list[float]) -> Point:
    return Point(float(raw[0]), float(raw[1]))


def parse_pos(pos: str) -> Point:
    x, y = pos.split(",", 1)
    return Point(float(x), float(y))


def cubic(points: list[Point], t: float) -> Point:
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


def sample_drawn_points(raw: list[Point]) -> list[Point]:
    if len(raw) < 2:
        return []
    if len(raw) == 2:
        return raw
    out: list[Point] = []
    for offset in range(0, len(raw) - 3, 3):
        curve = raw[offset : offset + 4]
        samples = [cubic(curve, i / CURVE_STEPS) for i in range(CURVE_STEPS + 1)]
        out.extend(samples if not out else samples[1:])
    return out


def drawn_segments(layout: dict[str, Any]) -> list[Segment]:
    segments: list[Segment] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        if edge.get("_concentrate_junction_internal") == "true":
            continue
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            raw = [parse_point_pair(p) for p in op.get("points", [])]
            points = sample_drawn_points(raw)
            for a, b in zip(points, points[1:]):
                segments.append(Segment(edge_index, a, b))
    return segments


def label_boxes(layout: dict[str, Any]) -> list[tuple[int, Box]]:
    labels: list[tuple[int, Box]] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        if edge.get("_concentrate_junction_internal") == "true":
            continue
        for stream in ("_ldraw_", "_hldraw_", "_tldraw_"):
            current_size = 14.0
            for op in edge.get(stream, []):
                if op.get("op") == "F":
                    current_size = float(op.get("size", current_size))
                    continue
                if op.get("op") != "T":
                    continue
                pt = op.get("pt")
                width = float(op.get("width", 0.0))
                if not pt or width <= 0:
                    continue
                center = parse_point_pair(pt)
                labels.append(
                    (
                        edge_index,
                        Box(
                            center.x,
                            center.y,
                            width / 2.0 + LABEL_PAD,
                            current_size / 2.0 + LABEL_PAD,
                        ),
                    )
                )
    return labels


def node_boxes(layout: dict[str, Any]) -> list[Box]:
    boxes: list[Box] = []
    for obj in layout.get("objects", []):
        if obj.get("_concentrate_junction_node") == "true":
            continue
        if "pos" not in obj:
            continue
        center = parse_pos(obj["pos"])
        boxes.append(
            Box(
                center.x,
                center.y,
                float(obj.get("width", 0.0)) * 36.0 + NODE_PAD,
                float(obj.get("height", 0.0)) * 36.0 + NODE_PAD,
            )
        )
    return boxes


def segment_hits_box(segment: Segment, box: Box) -> bool:
    steps = max(2, int(math.hypot(segment.b.x - segment.a.x, segment.b.y - segment.a.y) / 2.0))
    for index in range(steps + 1):
        t = index / steps
        p = Point(
            segment.a.x + (segment.b.x - segment.a.x) * t,
            segment.a.y + (segment.b.y - segment.a.y) * t,
        )
        if box.contains(p):
            return True
    return False


def label_crossings(layout: dict[str, Any]) -> tuple[int, int]:
    labels = label_boxes(layout)
    segments = drawn_segments(layout)
    hit_labels = set()
    hits = 0
    for label_index, (_owner, box) in enumerate(labels):
        for segment in segments:
            if segment_hits_box(segment, box):
                hits += 1
                hit_labels.add(label_index)
                break
    return len(hit_labels), len(labels)


def edge_node_hits(layout: dict[str, Any]) -> tuple[int, int]:
    boxes = node_boxes(layout)
    segments = drawn_segments(layout)
    hit_nodes = set()
    hits = 0
    for box_index, box in enumerate(boxes):
        for segment in segments:
            if segment_hits_box(segment, box):
                hits += 1
                hit_nodes.add(box_index)
                break
    return len(hit_nodes), hits


def polygon_centers(layout: dict[str, Any]) -> list[Point]:
    centers: list[Point] = []
    for edge in layout.get("edges", []):
        if edge.get("_concentrate_junction_internal") == "true":
            continue
        for stream in ("_hdraw_", "_tdraw_"):
            for op in edge.get(stream, []):
                if op.get("op") != "P":
                    continue
                points = [parse_point_pair(p) for p in op.get("points", [])]
                if not points:
                    continue
                centers.append(
                    Point(
                        sum(p.x for p in points) / len(points),
                        sum(p.y for p in points) / len(points),
                    )
                )
    return centers


def arrow_heap(layout: dict[str, Any], radius: float) -> tuple[int, int]:
    centers = polygon_centers(layout)
    worst = 0
    crowded = 0
    for center in centers:
        count = sum(
            1 for other in centers if math.hypot(center.x - other.x, center.y - other.y) <= radius
        )
        worst = max(worst, count)
        if count >= 3:
            crowded += 1
    return crowded, worst


def rank_count(layout: dict[str, Any]) -> int:
    return len(
        {
            round(parse_pos(obj["pos"]).y, 6)
            for obj in layout.get("objects", [])
            if "pos" in obj and obj.get("_concentrate_junction_node") != "true"
        }
    )


def format_row(label: str, layout: dict[str, Any], arrow_radius: float) -> str:
    crossed, labels = label_crossings(layout)
    hit_nodes, node_hits = edge_node_hits(layout)
    crowded, worst = arrow_heap(layout, arrow_radius)
    return (
        f"{label}\tlabels_crossed={crossed}/{labels}\t"
        f"nodes_hit={hit_nodes}/{len(node_boxes(layout))}\t"
        f"node_hit_segments={node_hits}\t"
        f"arrowheads_crowded={crowded}\tarrowhead_worst_cluster={worst}\t"
        f"node_ranks={rank_count(layout)}\t"
        f"drawn_edges={sum(1 for e in layout.get('edges', []) if e.get('_concentrate_junction_internal') != 'true')}\t"
        f"bb={layout.get('bb', '')}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("--dot", type=Path, default=Path("build/cmd/dot/dot_builtins"))
    parser.add_argument("--upstream-dot", type=Path)
    parser.add_argument("--label", default=None)
    parser.add_argument("--arrow-radius", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    label = args.label or args.graph.name
    cases = [
        ("ours_concentrate_true", args.dot, "-Gconcentrate=true"),
        ("ours_concentrate_false", args.dot, "-Gconcentrate=false"),
    ]
    if args.upstream_dot:
        cases.append(("upstream_concentrate_true", args.upstream_dot, "-Gconcentrate=true"))
    print(f"graph={label}")
    for case_label, dot, flag in cases:
        layout = render_json(dot, args.graph, flag)
        print(format_row(case_label, layout, args.arrow_radius))


if __name__ == "__main__":
    main()
