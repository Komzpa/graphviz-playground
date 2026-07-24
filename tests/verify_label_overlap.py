#!/usr/bin/env python3
"""Measure edge-label collisions in Graphviz xdot output.

Definitions:
- A label box is the axis-aligned box of one xdot ``T`` (text) operation
  emitted for an edge ``label``, ``xlabel``, ``headlabel``, or ``taillabel``.
- A node shape is the largest polygon or ellipse in a node's xdot ``_draw_``
  stream. If no boundary is drawn, the node's layout box is used.
- A label-vs-node overlap instance is one label box/node-shape pair with
  positive intersection area.
- A label-vs-label overlap instance is one pair of edge-label boxes with
  positive intersection area.
- A label-vs-edge-path crossing is one label box/edge-path pair for which a
  flattened xdot spline or polyline enters the box interior.

Single mode measures one binary. Comparison mode measures the same frozen
fixtures with a base and candidate binary and fails if the candidate adds any
aggregate area or instance count beyond ``--epsilon``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable, Sequence


Point = tuple[float, float]
Box = tuple[float, float, float, float]
Polygon = list[Point]

LABEL_STREAMS = {
    "_ldraw_": "label",
    "_hldraw_": "headlabel",
    "_tldraw_": "taillabel",
    "_xldraw_": "xlabel",
}
METRICS = (
    "label_node_instances",
    "label_node_area",
    "label_label_instances",
    "label_label_area",
    "label_edge_crossings",
)
AREA_METRICS = {"label_node_area", "label_label_area"}
NUMBER = re.compile(rb"[^\s]+")
GEOMETRY_EPSILON = 1e-9


class ParseError(RuntimeError):
    """The rendered xdot stream did not match the documented grammar."""


@dataclass(frozen=True)
class TextOperation:
    x: float
    y: float
    align: int
    width: float
    text: str
    font_size: float


@dataclass(frozen=True)
class LabelBox:
    identity: str
    owner: str
    kind: str
    text: str
    box: Box


@dataclass(frozen=True)
class NodeShape:
    identity: str
    polygon: Polygon


@dataclass(frozen=True)
class EdgePath:
    identity: str
    polylines: list[list[Point]]


class XDotReader:
    """Read one unescaped xdot operation stream."""

    def __init__(self, value: str):
        self.data = value.encode("utf-8")
        self.offset = 0

    def _skip_space(self) -> None:
        while (
            self.offset < len(self.data)
            and self.data[self.offset : self.offset + 1].isspace()
        ):
            self.offset += 1

    def token(self) -> bytes:
        self._skip_space()
        match = NUMBER.match(self.data, self.offset)
        if match is None:
            raise ParseError(f"expected token at xdot byte {self.offset}")
        self.offset = match.end()
        return match.group()

    def integer(self) -> int:
        try:
            return int(self.token())
        except ValueError as error:
            raise ParseError("expected xdot integer") from error

    def number(self) -> float:
        try:
            return float(self.token())
        except ValueError as error:
            raise ParseError("expected xdot number") from error

    def string(self) -> str:
        length = self.integer()
        self._skip_space()
        if self.data[self.offset : self.offset + 1] != b"-":
            raise ParseError(f"expected xdot string marker at byte {self.offset}")
        self.offset += 1
        end = self.offset + length
        if end > len(self.data):
            raise ParseError("xdot length-prefixed string extends past stream")
        value = self.data[self.offset : end]
        self.offset = end
        return value.decode("utf-8", errors="replace")

    def done(self) -> bool:
        self._skip_space()
        return self.offset >= len(self.data)


def parse_xdot_operations(value: str) -> list[dict[str, Any]]:
    """Parse the subset of xdot operations needed by this gate."""

    reader = XDotReader(value)
    operations: list[dict[str, Any]] = []
    font_size = 14.0
    while not reader.done():
        op = reader.token().decode("ascii", errors="strict")
        if op in {"c", "C", "S"}:
            reader.string()
        elif op == "F":
            font_size = reader.number()
            reader.string()
            operations.append({"op": op, "font_size": font_size})
        elif op == "T":
            operation = {
                "op": op,
                "x": reader.number(),
                "y": reader.number(),
                "align": reader.integer(),
                "width": reader.number(),
                "text": reader.string(),
                "font_size": font_size,
            }
            operations.append(operation)
        elif op in {"E", "e"}:
            operations.append(
                {
                    "op": op,
                    "x": reader.number(),
                    "y": reader.number(),
                    "width": reader.number(),
                    "height": reader.number(),
                }
            )
        elif op in {"P", "p", "L", "B", "b"}:
            count = reader.integer()
            points = [(reader.number(), reader.number()) for _ in range(count)]
            operations.append({"op": op, "points": points})
        elif op == "I":
            operations.append(
                {
                    "op": op,
                    "x": reader.number(),
                    "y": reader.number(),
                    "width": reader.number(),
                    "height": reader.number(),
                    "name": reader.string(),
                }
            )
        elif op == "t":
            reader.integer()
        else:
            raise ParseError(f"unsupported xdot operation {op!r}")
    return operations


def split_statements(source: str) -> list[str]:
    """Split rendered DOT at semicolons outside quoted strings/attribute lists."""

    statements: list[str] = []
    start = 0
    quote = False
    escaped = False
    brackets = 0
    html = 0
    for offset, char in enumerate(source):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            continue
        if char == '"':
            quote = True
        elif char == "<":
            html += 1
        elif char == ">" and html:
            html -= 1
        elif not html and char == "[":
            brackets += 1
        elif not html and char == "]" and brackets:
            brackets -= 1
        elif char == ";" and brackets == 0 and html == 0:
            statements.append(source[start:offset])
            start = offset + 1
    if source[start:].strip():
        statements.append(source[start:])
    return statements


def _quoted_value(source: str, offset: int) -> tuple[str, int]:
    assert source[offset] == '"'
    offset += 1
    value: list[str] = []
    while offset < len(source):
        char = source[offset]
        if char == '"':
            return "".join(value), offset + 1
        if char != "\\":
            value.append(char)
            offset += 1
            continue
        if offset + 1 >= len(source):
            value.append("\\")
            return "".join(value), offset + 1
        following = source[offset + 1]
        if following == "\n":
            offset += 2
            continue
        if (
            following == "\r"
            and offset + 2 < len(source)
            and source[offset + 2] == "\n"
        ):
            offset += 3
            continue
        if following in {'"', "\\"}:
            value.append(following)
        else:
            # DOT preserves unknown escape sequences. Xdot operation strings
            # only require quote/backslash and line-continuation handling.
            value.extend(("\\", following))
        offset += 2
    raise ParseError("unterminated quoted DOT attribute")


def parse_attributes(statement: str) -> tuple[str, dict[str, str]]:
    """Return the statement prefix and all attributes from rendered DOT."""

    first = statement.find("[")
    if first < 0:
        return statement, {}
    prefix = statement[:first].strip()
    attrs: dict[str, str] = {}
    offset = first
    while offset < len(statement):
        if statement[offset] != "[":
            offset += 1
            continue
        offset += 1
        while offset < len(statement):
            while offset < len(statement) and (
                statement[offset].isspace() or statement[offset] == ","
            ):
                offset += 1
            if offset >= len(statement) or statement[offset] == "]":
                offset += 1
                break
            key_start = offset
            while offset < len(statement) and (
                statement[offset].isalnum() or statement[offset] in "_:"
            ):
                offset += 1
            key = statement[key_start:offset]
            if not key:
                # Skip an attribute that this gate does not need.
                while offset < len(statement) and statement[offset] not in ",]":
                    offset += 1
                continue
            while offset < len(statement) and statement[offset].isspace():
                offset += 1
            if offset >= len(statement) or statement[offset] != "=":
                attrs[key] = ""
                continue
            offset += 1
            while offset < len(statement) and statement[offset].isspace():
                offset += 1
            if offset < len(statement) and statement[offset] == '"':
                value, offset = _quoted_value(statement, offset)
            elif offset < len(statement) and statement[offset] == "<":
                value_start = offset
                depth = 0
                quote = False
                while offset < len(statement):
                    char = statement[offset]
                    if char == '"':
                        quote = not quote
                    elif not quote and char == "<":
                        depth += 1
                    elif not quote and char == ">":
                        depth -= 1
                        if depth == 0:
                            offset += 1
                            break
                    offset += 1
                value = statement[value_start:offset]
            else:
                value_start = offset
                while offset < len(statement) and statement[offset] not in ",]":
                    offset += 1
                value = statement[value_start:offset].strip()
            attrs[key] = value
    return prefix, attrs


def polygon_area(polygon: Sequence[Point]) -> float:
    if len(polygon) < 3:
        return 0.0
    return (
        abs(
            sum(
                first[0] * second[1] - second[0] * first[1]
                for first, second in zip(polygon, polygon[1:] + polygon[:1])
            )
        )
        / 2.0
    )


def ellipse_polygon(x: float, y: float, width: float, height: float) -> Polygon:
    return [
        (
            x + width * math.cos(2.0 * math.pi * index / 96),
            y + height * math.sin(2.0 * math.pi * index / 96),
        )
        for index in range(96)
    ]


def rectangle_polygon(box: Box) -> Polygon:
    x_min, y_min, x_max, y_max = box
    return [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]


def clip_polygon_to_box(polygon: Sequence[Point], box: Box) -> Polygon:
    """Clip a polygon against an axis-aligned rectangle."""

    def clip(
        points: Sequence[Point],
        inside: Any,
        intersection: Any,
    ) -> Polygon:
        if not points:
            return []
        output: Polygon = []
        previous = points[-1]
        previous_inside = inside(previous)
        for current in points:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersection(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersection(previous, current))
            previous = current
            previous_inside = current_inside
        return output

    x_min, y_min, x_max, y_max = box

    def vertical(first: Point, second: Point, x: float) -> Point:
        if abs(second[0] - first[0]) < GEOMETRY_EPSILON:
            return (x, first[1])
        scale = (x - first[0]) / (second[0] - first[0])
        return (x, first[1] + scale * (second[1] - first[1]))

    def horizontal(first: Point, second: Point, y: float) -> Point:
        if abs(second[1] - first[1]) < GEOMETRY_EPSILON:
            return (first[0], y)
        scale = (y - first[1]) / (second[1] - first[1])
        return (first[0] + scale * (second[0] - first[0]), y)

    result = list(polygon)
    result = clip(
        result, lambda point: point[0] >= x_min, lambda a, b: vertical(a, b, x_min)
    )
    result = clip(
        result, lambda point: point[0] <= x_max, lambda a, b: vertical(a, b, x_max)
    )
    result = clip(
        result, lambda point: point[1] >= y_min, lambda a, b: horizontal(a, b, y_min)
    )
    result = clip(
        result, lambda point: point[1] <= y_max, lambda a, b: horizontal(a, b, y_max)
    )
    return result


def box_intersection_area(first: Box, second: Box) -> float:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    if width <= 0.0 or height <= 0.0:
        return 0.0
    return width * height


def _point_line_distance(point: Point, start: Point, end: Point) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = math.hypot(dx, dy)
    if denominator <= GEOMETRY_EPSILON:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    return (
        abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0])
        / denominator
    )


def flatten_cubic(
    first: Point,
    control_one: Point,
    control_two: Point,
    last: Point,
    tolerance: float = 0.25,
    depth: int = 0,
) -> list[Point]:
    """Flatten one cubic Bezier into a polyline with bounded chord error."""

    flatness = max(
        _point_line_distance(control_one, first, last),
        _point_line_distance(control_two, first, last),
    )
    if flatness <= tolerance or depth >= 12:
        return [first, last]
    p01 = ((first[0] + control_one[0]) / 2, (first[1] + control_one[1]) / 2)
    p12 = ((control_one[0] + control_two[0]) / 2, (control_one[1] + control_two[1]) / 2)
    p23 = ((control_two[0] + last[0]) / 2, (control_two[1] + last[1]) / 2)
    p012 = ((p01[0] + p12[0]) / 2, (p01[1] + p12[1]) / 2)
    p123 = ((p12[0] + p23[0]) / 2, (p12[1] + p23[1]) / 2)
    midpoint = ((p012[0] + p123[0]) / 2, (p012[1] + p123[1]) / 2)
    left = flatten_cubic(first, p01, p012, midpoint, tolerance, depth + 1)
    right = flatten_cubic(midpoint, p123, p23, last, tolerance, depth + 1)
    return left[:-1] + right


def bezier_polyline(points: Sequence[Point]) -> list[Point]:
    if len(points) < 4 or (len(points) - 1) % 3:
        return list(points)
    output = [points[0]]
    for offset in range(0, len(points) - 1, 3):
        segment = flatten_cubic(
            points[offset],
            points[offset + 1],
            points[offset + 2],
            points[offset + 3],
        )
        output.extend(segment[1:])
    return output


def segment_enters_box(first: Point, second: Point, box: Box) -> bool:
    """Return whether a line segment enters the strict interior of a box."""

    x_min, y_min, x_max, y_max = box
    x_min += GEOMETRY_EPSILON
    y_min += GEOMETRY_EPSILON
    x_max -= GEOMETRY_EPSILON
    y_max -= GEOMETRY_EPSILON
    if x_min >= x_max or y_min >= y_max:
        return False
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    lower = 0.0
    upper = 1.0
    for p, q in (
        (-dx, first[0] - x_min),
        (dx, x_max - first[0]),
        (-dy, first[1] - y_min),
        (dy, y_max - first[1]),
    ):
        if abs(p) <= GEOMETRY_EPSILON:
            if q < 0.0:
                return False
            continue
        ratio = q / p
        if p < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return upper - lower > GEOMETRY_EPSILON


def _parse_point(value: str | None) -> Point | None:
    if not value:
        return None
    parts = value.split(",")
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError:
        return None


def _display_identity(prefix: str, fallback: str) -> str:
    compact = " ".join(prefix.split())
    for opening in ("digraph ", "graph ", "subgraph "):
        marker = compact.rfind("{")
        if marker >= 0:
            compact = compact[marker + 1 :].strip()
        if compact.startswith(opening) and "{" in compact:
            compact = compact.split("{", 1)[1].strip()
    return compact or fallback


def text_operations(value: str) -> list[TextOperation]:
    return [
        TextOperation(
            x=float(operation["x"]),
            y=float(operation["y"]),
            align=int(operation["align"]),
            width=float(operation["width"]),
            text=str(operation["text"]),
            font_size=float(operation["font_size"]),
        )
        for operation in parse_xdot_operations(value)
        if operation["op"] == "T"
    ]


def text_box(operation: TextOperation) -> Box:
    if operation.align < 0:
        left = operation.x
    elif operation.align > 0:
        left = operation.x - operation.width
    else:
        left = operation.x - operation.width / 2.0
    # This matches Graphviz's existing approximate edge-label test geometry.
    return (
        left,
        operation.y - 0.3 * operation.font_size,
        left + operation.width,
        operation.y + 0.9 * operation.font_size,
    )


def label_kind(
    stream: str,
    attrs: dict[str, str],
    operation: TextOperation,
) -> str:
    if stream == "_hldraw_":
        return "headlabel"
    if stream == "_tldraw_":
        return "taillabel"
    if stream == "_xldraw_":
        return "xlabel"
    positions = [
        (kind, point)
        for kind, point in (
            ("label", _parse_point(attrs.get("lp"))),
            ("xlabel", _parse_point(attrs.get("xlp"))),
        )
        if point is not None
    ]
    if len(positions) == 1:
        return positions[0][0]
    if positions:
        return min(
            positions,
            key=lambda item: (operation.x - item[1][0]) ** 2
            + (operation.y - item[1][1]) ** 2,
        )[0]
    if "xlabel" in attrs and "label" not in attrs:
        return "xlabel"
    return "label"


def parse_layout(xdot: str) -> tuple[list[LabelBox], list[NodeShape], list[EdgePath]]:
    labels: list[LabelBox] = []
    nodes: list[NodeShape] = []
    edges: list[EdgePath] = []
    node_index = 0
    edge_index = 0
    for statement in split_statements(xdot):
        prefix, attrs = parse_attributes(statement)
        if not attrs:
            continue
        is_edge = "->" in prefix or "--" in prefix
        if is_edge:
            owner = _display_identity(prefix, f"edge-{edge_index}")
            for stream, default_kind in LABEL_STREAMS.items():
                if stream not in attrs:
                    continue
                for text_index, operation in enumerate(text_operations(attrs[stream])):
                    kind = label_kind(stream, attrs, operation)
                    labels.append(
                        LabelBox(
                            identity=f"edge-{edge_index}:{stream}:{text_index}",
                            owner=owner,
                            kind=kind or default_kind,
                            text=operation.text,
                            box=text_box(operation),
                        )
                    )
            polylines: list[list[Point]] = []
            if "_draw_" in attrs:
                for operation in parse_xdot_operations(attrs["_draw_"]):
                    if operation["op"] == "L":
                        polylines.append(list(operation["points"]))
                    elif operation["op"] in {"B", "b"}:
                        polylines.append(bezier_polyline(operation["points"]))
            edges.append(EdgePath(f"edge-{edge_index}:{owner}", polylines))
            edge_index += 1
            continue
        if not {"pos", "width", "height"} <= attrs.keys():
            continue
        identity = _display_identity(prefix, f"node-{node_index}")
        candidates: list[Polygon] = []
        if "_draw_" in attrs:
            for operation in parse_xdot_operations(attrs["_draw_"]):
                if operation["op"] in {"P", "p"}:
                    candidates.append(list(operation["points"]))
                elif operation["op"] in {"E", "e"}:
                    candidates.append(
                        ellipse_polygon(
                            operation["x"],
                            operation["y"],
                            operation["width"],
                            operation["height"],
                        )
                    )
        if candidates:
            polygon = max(candidates, key=polygon_area)
        else:
            position = _parse_point(attrs.get("pos"))
            if position is None:
                raise ParseError(f"node {identity!r} has an invalid pos attribute")
            width = float(attrs["width"]) * 72.0
            height = float(attrs["height"]) * 72.0
            polygon = rectangle_polygon(
                (
                    position[0] - width / 2.0,
                    position[1] - height / 2.0,
                    position[0] + width / 2.0,
                    position[1] + height / 2.0,
                )
            )
        nodes.append(NodeShape(f"node-{node_index}:{identity}", polygon))
        node_index += 1
    return labels, nodes, edges


def measure_xdot(xdot: str) -> dict[str, Any]:
    labels, nodes, edges = parse_layout(xdot)
    label_node: list[dict[str, Any]] = []
    for label in labels:
        for node in nodes:
            area = polygon_area(clip_polygon_to_box(node.polygon, label.box))
            if area > GEOMETRY_EPSILON:
                label_node.append(
                    {
                        "label": label.identity,
                        "kind": label.kind,
                        "text": label.text,
                        "node": node.identity,
                        "area": round(area, 6),
                    }
                )
    label_label: list[dict[str, Any]] = []
    for first_index, first in enumerate(labels):
        for second in labels[first_index + 1 :]:
            area = box_intersection_area(first.box, second.box)
            if area > GEOMETRY_EPSILON:
                label_label.append(
                    {
                        "first_label": first.identity,
                        "first_kind": first.kind,
                        "first_text": first.text,
                        "second_label": second.identity,
                        "second_kind": second.kind,
                        "second_text": second.text,
                        "area": round(area, 6),
                    }
                )
    label_edge: list[dict[str, Any]] = []
    for label in labels:
        for edge in edges:
            if any(
                segment_enters_box(first, second, label.box)
                for polyline in edge.polylines
                for first, second in zip(polyline, polyline[1:])
            ):
                label_edge.append(
                    {
                        "label": label.identity,
                        "kind": label.kind,
                        "text": label.text,
                        "edge": edge.identity,
                    }
                )
    metrics = {
        "labels": len(labels),
        "nodes": len(nodes),
        "edge_paths": len(edges),
        "label_node_instances": len(label_node),
        "label_node_area": round(sum(item["area"] for item in label_node), 6),
        "label_label_instances": len(label_label),
        "label_label_area": round(sum(item["area"] for item in label_label), 6),
        "label_edge_crossings": len(label_edge),
    }
    metrics["total_overlapped_area"] = round(
        metrics["label_node_area"] + metrics["label_label_area"], 6
    )
    return {
        "status": "rendered",
        "metrics": metrics,
        "overlaps": {
            "label_node": label_node,
            "label_label": label_label,
            "label_edge": label_edge,
        },
    }


def fixture_paths(argument: Path) -> list[Path]:
    if argument.is_dir():
        paths = sorted(path.resolve() for path in argument.rglob("*.dot"))
    elif argument.is_file():
        paths = []
        for line_number, raw_line in enumerate(
            argument.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            path = Path(line)
            if not path.is_absolute():
                path = argument.parent / path
            if not path.is_file():
                raise SystemExit(
                    f"{argument}:{line_number}: fixture does not exist: {path}"
                )
            paths.append(path.resolve())
    else:
        raise SystemExit(f"--fixtures is neither a list file nor directory: {argument}")
    if not paths:
        raise SystemExit(f"--fixtures selected no .dot files: {argument}")
    duplicates = sorted({str(path) for path in paths if paths.count(path) > 1})
    if duplicates:
        raise SystemExit("duplicate fixtures: " + ", ".join(duplicates))
    return paths


def validate_binary(path: Path, option: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SystemExit(f"{option} is not an executable file: {path}")
    return resolved


def render_fixture(dot: Path, fixture: Path, timeout: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(dot), "-Txdot", fixture.name],
            cwd=fixture.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "status": "timeout",
            "timeout_seconds": timeout,
            "stderr": (error.stderr or b"").decode("utf-8", errors="replace")[-2000:],
        }
    if completed.returncode != 0:
        return {
            "status": "render_error",
            "returncode": completed.returncode,
            "stderr": completed.stderr.decode("utf-8", errors="replace")[-2000:],
        }
    try:
        measured = measure_xdot(completed.stdout.decode("utf-8", errors="strict"))
    except (ParseError, UnicodeDecodeError, ValueError) as error:
        return {"status": "parse_error", "error": str(error)}
    if completed.stderr:
        measured["stderr"] = completed.stderr.decode("utf-8", errors="replace")[-2000:]
    return measured


def render_all(
    dot: Path,
    fixtures: Sequence[Path],
    timeout: float,
    jobs: int,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(render_fixture, dot, fixture, timeout): fixture
            for fixture in fixtures
        }
        for future in as_completed(futures):
            fixture = futures[future]
            results[str(fixture)] = future.result()
    return dict(sorted(results.items()))


def empty_totals() -> dict[str, float | int]:
    return {
        "labels": 0,
        "nodes": 0,
        "edge_paths": 0,
        "label_node_instances": 0,
        "label_node_area": 0.0,
        "label_label_instances": 0,
        "label_label_area": 0.0,
        "label_edge_crossings": 0,
        "total_overlapped_area": 0.0,
    }


def totals(results: Iterable[dict[str, Any]]) -> dict[str, float | int]:
    output = empty_totals()
    for result in results:
        if result["status"] != "rendered":
            continue
        for key, value in result["metrics"].items():
            output[key] += value
    for key in AREA_METRICS | {"total_overlapped_area"}:
        output[key] = round(float(output[key]), 6)
    return output


def single_report(
    dot: Path,
    fixtures_source: Path,
    fixtures: Sequence[Path],
    results: dict[str, dict[str, Any]],
    timeout: float,
    jobs: int,
) -> tuple[dict[str, Any], int]:
    errors = [
        {"fixture": fixture, "status": result["status"]}
        for fixture, result in results.items()
        if result["status"] != "rendered"
    ]
    report = {
        "schema_version": 1,
        "mode": "single",
        "dot": str(dot),
        "fixtures_source": str(fixtures_source.resolve()),
        "timeout_seconds": timeout,
        "jobs": jobs,
        "fixtures": [
            {"fixture": fixture, **result} for fixture, result in results.items()
        ],
        "summary": {
            "fixtures_total": len(fixtures),
            "fixtures_rendered": len(fixtures) - len(errors),
            "fixtures_failed": len(errors),
            "totals": totals(results.values()),
            "verdict": "PASS" if not errors else "FAIL",
        },
    }
    return report, 0 if not errors else 1


def comparison_report(
    base_dot: Path,
    candidate_dot: Path,
    fixtures_source: Path,
    fixtures: Sequence[Path],
    base_results: dict[str, dict[str, Any]],
    candidate_results: dict[str, dict[str, Any]],
    epsilon: float,
    timeout: float,
    jobs: int,
) -> tuple[dict[str, Any], int]:
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for fixture in sorted(base_results):
        base = base_results[fixture]
        candidate = candidate_results[fixture]
        row: dict[str, Any] = {
            "fixture": fixture,
            "base": base,
            "candidate": candidate,
        }
        if base["status"] != "rendered" or candidate["status"] != "rendered":
            row["delta"] = None
            row["regressions"] = ["render_or_parse_failure"]
            failures.append(
                {
                    "fixture": fixture,
                    "reason": "render_or_parse_failure",
                    "base_status": base["status"],
                    "candidate_status": candidate["status"],
                }
            )
            rows.append(row)
            continue
        delta = {
            metric: round(candidate["metrics"][metric] - base["metrics"][metric], 6)
            for metric in METRICS
        }
        regressions = [metric for metric in METRICS if delta[metric] > epsilon]
        row["delta"] = delta
        row["regressions"] = regressions
        if regressions:
            failures.append(
                {
                    "fixture": fixture,
                    "reason": "added_overlap",
                    "metrics": regressions,
                    "delta": {metric: delta[metric] for metric in regressions},
                }
            )
        rows.append(row)
    comparable = [row for row in rows if row["delta"] is not None]
    base_totals = totals(row["base"] for row in comparable)
    candidate_totals = totals(row["candidate"] for row in comparable)
    summary_delta = {
        metric: round(candidate_totals[metric] - base_totals[metric], 6)
        for metric in METRICS
    }
    report = {
        "schema_version": 1,
        "mode": "comparison",
        "base_dot": str(base_dot),
        "candidate_dot": str(candidate_dot),
        "fixtures_source": str(fixtures_source.resolve()),
        "epsilon": epsilon,
        "timeout_seconds": timeout,
        "jobs": jobs,
        "fixtures": rows,
        "summary": {
            "fixtures_total": len(fixtures),
            "fixtures_compared": sum(row["delta"] is not None for row in rows),
            "fixtures_failed_to_compare": sum(row["delta"] is None for row in rows),
            "fixtures_with_added_overlap": sum(
                bool(row["regressions"])
                and row["regressions"] != ["render_or_parse_failure"]
                for row in rows
            ),
            "base_totals": base_totals,
            "candidate_totals": candidate_totals,
            "delta": summary_delta,
            "failures": failures,
            "verdict": "FAIL" if failures else "PASS",
        },
    }
    return report, 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dot", type=Path, help="binary for single mode")
    parser.add_argument("--base-dot", type=Path, help="base binary for comparison")
    parser.add_argument(
        "--candidate-dot", type=Path, help="candidate binary for comparison"
    )
    parser.add_argument(
        "--fixtures", type=Path, required=True, help="fixture list file or directory"
    )
    parser.add_argument("--out", type=Path, required=True, help="JSON report path")
    parser.add_argument(
        "--epsilon",
        type=float,
        default=0.01,
        help="maximum allowed positive per-fixture metric delta (default: 0.01)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="per-render timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="parallel render jobs (default: min(8, CPU count))",
    )
    args = parser.parse_args()
    single = args.dot is not None
    comparison = args.base_dot is not None or args.candidate_dot is not None
    if single == comparison:
        parser.error("use either --dot or both --base-dot and --candidate-dot")
    if comparison and (args.base_dot is None or args.candidate_dot is None):
        parser.error("comparison mode requires --base-dot and --candidate-dot")
    if args.epsilon < 0.0:
        parser.error("--epsilon must be non-negative")
    if args.timeout <= 0.0:
        parser.error("--timeout must be positive")
    if args.jobs <= 0:
        parser.error("--jobs must be positive")
    return args


def main() -> int:
    args = parse_args()
    fixtures = fixture_paths(args.fixtures)
    if args.dot is not None:
        dot = validate_binary(args.dot, "--dot")
        results = render_all(dot, fixtures, args.timeout, args.jobs)
        report, returncode = single_report(
            dot,
            args.fixtures,
            fixtures,
            results,
            args.timeout,
            args.jobs,
        )
    else:
        base_dot = validate_binary(args.base_dot, "--base-dot")
        candidate_dot = validate_binary(args.candidate_dot, "--candidate-dot")
        base_results = render_all(base_dot, fixtures, args.timeout, args.jobs)
        candidate_results = render_all(candidate_dot, fixtures, args.timeout, args.jobs)
        report, returncode = comparison_report(
            base_dot,
            candidate_dot,
            args.fixtures,
            fixtures,
            base_results,
            candidate_results,
            args.epsilon,
            args.timeout,
            args.jobs,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    print(
        f"label-overlap: verdict={summary['verdict']} "
        f"fixtures={summary['fixtures_total']} out={args.out}"
    )
    return returncode


if __name__ == "__main__":
    sys.exit(main())
