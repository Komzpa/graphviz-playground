"""Concentrate-specific Graphviz regression tests."""

import itertools
import json
import math
import operator
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator, Optional, Union

import pytest

sys.path.append(os.path.dirname(__file__))
from gvtest import (  # pylint: disable=wrong-import-position
    compile_c,
    dot,
    is_cmake,
    is_mingw,
    is_macos,
    is_static_build,
    plugin_version,
    run,
    run_c,
    which,
)

def _json_layout(source: str) -> dict:
    """Render DOT source once and parse its JSON xdot payload."""

    return json.loads(dot("json", source=source))


def _drawn_edges_from_layout(layout: dict) -> list[dict]:
    """Return edges with an xdot ``_draw_`` stream and thus visible geometry."""

    return [edge for edge in layout["edges"] if "_draw_" in edge]


def _drawn_edges(source: str) -> list[dict]:
    """Render DOT source and return visible drawn edges."""

    return _drawn_edges_from_layout(_json_layout(source))


def _drawn_edge_piece_end_gaps(edge: dict) -> list[float]:
    """Measure endpoint gaps between consecutive drawn spline pieces."""

    pieces = [
        operation["points"]
        for operation in edge.get("_draw_", [])
        if operation["op"] in {"B", "b", "L"} and len(operation["points"]) >= 2
    ]
    return [math.dist(left[-1], right[0]) for left, right in zip(pieces, pieces[1:])]


def _assert_regular_g1_piece_join(
    left_points: list[list[float]], right_points: list[list[float]]
) -> None:
    """Assert a rendered cubic seam is C0, regular, and forward G1."""

    assert len(left_points) >= 4 and len(left_points) % 3 == 1
    assert len(right_points) >= 4 and len(right_points) % 3 == 1
    assert math.dist(left_points[-1], right_points[0]) <= 0.01
    incoming = (
        left_points[-1][0] - left_points[-2][0],
        left_points[-1][1] - left_points[-2][1],
    )
    outgoing = (
        right_points[1][0] - right_points[0][0],
        right_points[1][1] - right_points[0][1],
    )
    incoming_length = math.hypot(*incoming)
    outgoing_length = math.hypot(*outgoing)
    assert incoming_length > 1e-6
    assert outgoing_length > 1e-6
    dot_product = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    assert dot_product > 0
    residual = abs(incoming[0] * outgoing[1] - incoming[1] * outgoing[0])
    # JSON control points are rounded to 0.01 pt, so allow the corresponding
    # sub-degree cross-product drift while still rejecting a visible kink.
    assert residual <= 2e-3 * incoming_length * outgoing_length


def _named_drawn_cubic_geometry(
    source: str, tails: set[str], head: str
) -> list[tuple[str, str, tuple]]:
    """Canonicalize rendered cubics by endpoint name, independent of edge order."""

    layout = json.loads(dot("json", source=source))
    names = {node["_gvid"]: node["name"] for node in layout["objects"]}
    geometry = []
    for edge in layout["edges"]:
        tail_name = names[edge["tail"]]
        head_name = names[edge["head"]]
        if tail_name not in tails or head_name != head or "_draw_" not in edge:
            continue
        cubics = tuple(
            tuple(tuple(point) for point in operation["points"])
            for operation in edge["_draw_"]
            if operation["op"] == "b"
        )
        geometry.append((tail_name, head_name, cubics))
    return sorted(geometry)


def _concentrated_graph(splines: str, *body: str) -> str:
    """Wrap readable DOT statements in a concentrated directed graph."""

    body_source = "\n".join(body)
    return f"""
        digraph {{
          graph [concentrate=true {splines}]
          {body_source}
        }}
    """


def _graph_with_concentrate(concentrate: bool, *body: str) -> str:
    """Wrap readable DOT statements in a directed graph with concentrate set."""

    body_source = "\n".join(body)
    return f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {body_source}
        }}
    """


def _set_graph_concentrate(source: str, concentrate: bool) -> str:
    """Force the first graph-level concentrate assignment in a source fixture."""

    return re.sub(
        r"\bconcentrate\s*=\s*(?:true|false)\b",
        f"concentrate={str(concentrate).lower()}",
        source,
        count=1,
    )


def _assert_concentrate_route_gain(
    source: str, plain_count: int, concentrate_count: int
) -> None:
    """Assert exact drawn-route counts and that concentration is not a no-op."""

    plain_routes = len(_drawn_edges(_set_graph_concentrate(source, False)))
    concentrate_routes = len(_drawn_edges(_set_graph_concentrate(source, True)))
    assert plain_routes == plain_count
    assert concentrate_routes == concentrate_count
    assert concentrate_routes < plain_routes


def _drawn_route_topology(source: str) -> tuple[int, tuple[str, ...]]:
    """Return a compact topology signature for concentrate metamorphic checks."""

    colors = tuple(sorted(_drawn_edge_color(edge) for edge in _drawn_edges(source)))
    return len(colors), colors


def _drawn_edge_color(edge: dict) -> str:
    """Read the pen color from an edge's xdot ``c`` operation."""

    operation = next(
        operation for operation in edge["_draw_"] if operation["op"] == "c"
    )
    return operation["color"]


def _drawn_edge_colors(source: str) -> list[str]:
    """Read each drawn edge's pen color."""

    return [_drawn_edge_color(edge) for edge in _drawn_edges(source)]


def _drawn_edges_by_color(source: str) -> dict[str, dict]:
    """Group drawn edges by their xdot pen color."""

    return {_drawn_edge_color(edge): edge for edge in _drawn_edges(source)}


def _drawn_edge_styles(source: str) -> list[str]:
    """Read line styles from xdot's ``S`` operations."""

    return [
        operation["style"]
        for edge in _drawn_edges(source)
        for operation in edge["_draw_"]
        if operation["op"] == "S"
    ]


def _drawn_label_texts(edge: dict) -> list[str]:
    """Read text emitted in an edge's xdot label stream."""

    return [
        operation["text"]
        for operation in edge.get("_ldraw_", [])
        if operation["op"] == "T"
    ]


def _edge_label_draw_streams(edge: dict, include_endpoint: bool = False) -> list[list[dict]]:
    """Read main and endpoint edge label xdot streams."""

    streams = ("_ldraw_", "_hldraw_", "_tldraw_") if include_endpoint else ("_ldraw_",)
    return [
        edge.get(stream, [])
        for stream in streams
        if stream in edge
    ]


def _edge_label_texts(source: str) -> list[str]:
    """Read text emitted by all edge label draw streams."""

    return [
        text
        for edge in json.loads(dot("json", source=source))["edges"]
        for text in _drawn_label_texts(edge)
    ]


def _edge_label_boxes_from_layout(
    layout: dict, include_endpoint: bool = False
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Read approximate edge-label boxes from a parsed JSON xdot layout."""

    boxes = []
    for edge in layout["edges"]:
        for stream in _edge_label_draw_streams(edge, include_endpoint):
            font_size = 14.0
            for operation in stream:
                if operation["op"] == "F":
                    font_size = float(operation.get("size", font_size))
                if operation["op"] != "T":
                    continue
                x, y = operation["pt"]
                width = float(operation.get("width", 0.0))
                align = operation.get("align", "c")
                if align == "l":
                    left = x
                elif align == "r":
                    left = x - width
                else:
                    left = x - width / 2
                boxes.append(
                    (
                        operation["text"],
                        (
                            left,
                            y - 0.3 * font_size,
                            left + width,
                            y + 0.9 * font_size,
                        ),
                    )
                )
    return boxes


def _edge_label_boxes(
    source: str, include_endpoint: bool = False
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Render DOT source and read approximate edge-label boxes."""

    return _edge_label_boxes_from_layout(_json_layout(source), include_endpoint)


def _box_gap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Measure the shortest distance between two axis-aligned boxes."""

    dx = max(first[0] - second[2], second[0] - first[2], 0.0)
    dy = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(dx, dy)


def _boxes_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Return whether two axis-aligned boxes overlap or touch."""

    return max(first[0], second[0]) <= min(first[2], second[2]) and max(
        first[1], second[1]
    ) <= min(first[3], second[3])


def _node_boxes(layout: dict) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Read node boxes from dot JSON coordinates."""

    boxes = []
    for node in layout["objects"]:
        x, y = (float(value) for value in node["pos"].split(","))
        width = float(node["width"]) * 72.0
        height = float(node["height"]) * 72.0
        boxes.append(
            (
                node["name"],
                (x - width / 2, y - height / 2, x + width / 2, y + height / 2),
            )
        )
    return boxes


def _arrow_polygon_point_count(edge: dict, endpoint: str) -> int:
    """Distinguish arrow shapes by the size of their xdot ``P`` polygon."""

    endpoint_draw_operations = edge[f"_{endpoint}draw_"]
    polygon = next(
        operation for operation in endpoint_draw_operations if operation["op"] == "P"
    )
    return len(polygon["points"])


def _arrow_fill_color(edge: dict, endpoint: str) -> str:
    """Read an arrow's fill color from its xdot ``C`` operation."""

    operation = next(
        operation
        for operation in edge[f"_{endpoint}draw_"]
        if operation["op"] == "C"
    )
    return operation["color"]


def _drawn_edge_spline_point_count(edge: dict) -> int:
    """Count points across every xdot Bezier segment drawn for one edge."""

    return sum(
        len(operation["points"])
        for operation in edge["_draw_"]
        if operation["op"] == "b"
    )


def _edge_position_tokens(
    edge: dict,
) -> tuple[list[tuple[str, tuple[float, float]]], list[tuple[float, float]]]:
    """Split an edge's ``pos`` into endpoint markers and spline points."""

    markers = []
    points = []
    for token in edge["pos"].split():
        marker = ""
        coordinates = token
        if token.startswith(("e,", "s,")):
            marker = token[0]
            coordinates = token[2:]
        x, y = coordinates.split(",", 1)
        point = (float(x), float(y))
        if marker:
            markers.append((marker, point))
        else:
            points.append(point)
    return markers, points


def _edge_physical_endpoint(edge: dict, endpoint: str) -> tuple[float, float]:
    """Return the visible head or tail endpoint from Graphviz JSON output."""

    markers, points = _edge_position_tokens(edge)
    marker = "e" if endpoint == "head" else "s"
    marked_points = [point for kind, point in markers if kind == marker]
    if marked_points:
        assert len(marked_points) == 1
        return marked_points[0]
    assert points
    return points[-1] if endpoint == "head" else points[0]


def _group_anchor_points(
    source: str,
    *,
    node_name: str,
    endpoint: str,
    attribute: str,
    groups: set[str],
) -> set[tuple[float, float]]:
    """Collect rounded visible anchors for selected samehead/sametail groups."""

    layout = json.loads(dot("json", source=source))
    node_ids = {node["name"]: node["_gvid"] for node in layout["objects"]}
    node_id = node_ids[node_name]
    endpoint_id = "head" if endpoint == "head" else "tail"
    return {
        tuple(
            round(coordinate, 3)
            for coordinate in _edge_physical_endpoint(edge, endpoint)
        )
        for edge in layout["edges"]
        if "_draw_" in edge
        and edge[endpoint_id] == node_id
        and edge.get(attribute, "") in groups
    }


def _samehead_mixed_route_fixture(concentrate: bool) -> str:
    """Exercise regular, backward-classified, and flat samehead members."""

    return f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          rankdir=TB

          s0 -> A [samehead=x]
          s1 -> A [samehead=x]
          A -> back_anchor [style=invis, weight=100]
          back_anchor -> A [samehead=x]
          {{ rank=same; flat; A; }}
          flat -> A [samehead=x]

          b0 -> B [samehead=y]
          b1 -> B [samehead=z]
        }}
    """


def _sametail_mixed_route_fixture(concentrate: bool) -> str:
    """Exercise regular, backward-classified, and flat sametail members."""

    return f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          rankdir=TB

          A -> t0 [sametail=x]
          A -> t1 [sametail=x]
          back -> A [style=invis, weight=100]
          A -> back [sametail=x]
          {{ rank=same; A; flat; }}
          A -> flat [sametail=x]

          A -> u0 [sametail=y]
          A -> u1 [sametail=z]
        }}
    """


def _assert_concentrate_keeps_zero_crossings(source: str) -> None:
    plain_edges = _drawn_edges(_set_graph_concentrate(source, False))
    concentrated_edges = _drawn_edges(_set_graph_concentrate(source, True))

    assert _sampled_edge_crossing_count(plain_edges) == 0
    assert _sampled_edge_crossing_count(concentrated_edges) == 0


def _arrowhead_shaft_angle(edge: dict, stream: str = "_hdraw_") -> float:
    """Return the angle between a normal arrow and its shaft tangent."""

    polygon = next(
        operation["points"]
        for operation in edge[stream]
        if operation["op"] == "P"
    )
    assert len(polygon) == 3
    tip = polygon[1]
    bezier, endpoint_index = min(
        (
            (operation["points"], endpoint_index)
            for operation in edge["_draw_"]
            if operation["op"] == "b"
            for endpoint_index in (0, len(operation["points"]) - 1)
        ),
        key=lambda candidate: math.dist(candidate[0][candidate[1]], tip),
    )
    base_midpoint = (
        (polygon[0][0] + polygon[2][0]) / 2,
        (polygon[0][1] + polygon[2][1]) / 2,
    )
    endpoint = bezier[endpoint_index]
    oriented = bezier if endpoint_index == 0 else list(reversed(bezier))
    prior = next(
        point for point in oriented[1:] if math.dist(point, endpoint) > 0.001
    )
    shaft = (endpoint[0] - prior[0], endpoint[1] - prior[1])
    arrow_axis = (
        polygon[1][0] - base_midpoint[0],
        polygon[1][1] - base_midpoint[1],
    )
    cosine = sum(a * b for a, b in zip(shaft, arrow_axis)) / (
        math.hypot(*shaft) * math.hypot(*arrow_axis)
    )
    return math.degrees(math.acos(max(-1, min(1, cosine))))


def _polygon_self_intersections(points: list[list[float]]) -> list[tuple[int, int]]:
    """Return pairs of non-adjacent polygon edges that cross."""

    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (
            c[0] - a[0]
        )

    intersections = []
    for first in range(len(points)):
        for second in range(first + 1, len(points)):
            if second == first + 1 or (
                first == 0 and second == len(points) - 1
            ):
                continue
            a = points[first]
            b = points[(first + 1) % len(points)]
            c = points[second]
            d = points[(second + 1) % len(points)]
            if orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(
                c, d, a
            ) * orientation(c, d, b) < 0:
                intersections.append((first, second))
    return intersections


def _point_box(points: list[list[float]]) -> tuple[float, float, float, float]:
    """Return a point-list bounding box as left, bottom, right, top."""

    return tuple(map(min, zip(*points))) + tuple(map(max, zip(*points)))


def _boxes_are_disjoint(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Return whether two axis-aligned boxes have no area overlap."""

    return (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def _edge_arrow_polygon(edge: dict, stream: str) -> list[list[float]]:
    return next(
        operation["points"] for operation in edge[stream] if operation["op"] == "P"
    )


def _drawn_edges_between(source: str, tails: set[str], head: str) -> list[dict]:
    """Return visible edges from named tails into one named head node."""

    layout = json.loads(dot("json", source=source))
    node_ids = {node["name"]: node["_gvid"] for node in layout["objects"]}
    tail_ids = {node_ids[tail] for tail in tails}
    head_id = node_ids[head]
    return [
        edge
        for edge in layout["edges"]
        if edge["tail"] in tail_ids and edge["head"] == head_id and "_draw_" in edge
    ]


def _drawn_edge_between(layout: dict, tail: str, head: str) -> dict:
    """Return one visible edge between two named nodes."""

    node_ids = {node["name"]: node["_gvid"] for node in layout["objects"]}
    edges = [
        edge
        for edge in layout["edges"]
        if edge["tail"] == node_ids[tail]
        and edge["head"] == node_ids[head]
        and "_draw_" in edge
    ]
    assert len(edges) == 1
    return edges[0]


def _route_x_coordinates(edge: dict) -> tuple[float, ...]:
    """Return x coordinates from xdot's ``b`` Bezier operation."""

    bezier = next(operation for operation in edge["_draw_"] if operation["op"] == "b")
    return tuple(point[0] for point in bezier["points"])


def _sample_bezier_points(
    points: list[list[float]],
) -> Iterator[tuple[float, float]]:
    """Sample every cubic segment in one xdot Bezier operation."""

    for start in range(0, len(points) - 1, 3):
        control = points[start : start + 4]
        assert len(control) == 4
        for step in range(1001):
            t = step / 1000
            u = 1 - t
            yield (
                u**3 * control[0][0]
                + 3 * u**2 * t * control[1][0]
                + 3 * u * t**2 * control[2][0]
                + t**3 * control[3][0],
                u**3 * control[0][1]
                + 3 * u**2 * t * control[1][1]
                + 3 * u * t**2 * control[2][1]
                + t**3 * control[3][1],
            )


def _sample_bezier_points_with_tangents(
    points: list[list[float]],
) -> Iterator[tuple[tuple[float, float], tuple[float, float]]]:
    """Sample every cubic segment with its tangent vector."""

    for start in range(0, len(points) - 1, 3):
        control = points[start : start + 4]
        assert len(control) == 4
        for step in range(1001):
            t = step / 1000
            u = 1 - t
            point = (
                u**3 * control[0][0]
                + 3 * u**2 * t * control[1][0]
                + 3 * u * t**2 * control[2][0]
                + t**3 * control[3][0],
                u**3 * control[0][1]
                + 3 * u**2 * t * control[1][1]
                + 3 * u * t**2 * control[2][1]
                + t**3 * control[3][1],
            )
            tangent = (
                3 * u**2 * (control[1][0] - control[0][0])
                + 6 * u * t * (control[2][0] - control[1][0])
                + 3 * t**2 * (control[3][0] - control[2][0]),
                3 * u**2 * (control[1][1] - control[0][1])
                + 6 * u * t * (control[2][1] - control[1][1])
                + 3 * t**2 * (control[3][1] - control[2][1]),
            )
            yield point, tangent


def _angle_between_vectors(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Return the acute angle between two vectors in degrees."""

    denominator = math.hypot(*first) * math.hypot(*second)
    assert denominator > 0
    cosine = sum(a * b for a, b in zip(first, second)) / denominator
    angle = math.degrees(math.acos(max(-1, min(1, cosine))))
    return min(angle, 180 - angle)


def _crossing_angle(first_edge: dict, second_edge: dict) -> float:
    first_samples = tuple(
        _sample_bezier_points_with_tangents(_edge_bezier_points(first_edge))
    )
    second_samples = tuple(
        _sample_bezier_points_with_tangents(_edge_bezier_points(second_edge))
    )
    _, first, second = min(
        (math.dist(first[0], second[0]), first, second)
        for first in first_samples
        for second in second_samples
    )
    return _angle_between_vectors(first[1], second[1])


def _drawn_edge_arc_length(edge: dict) -> float:
    samples = _sample_drawn_edge(edge)
    return sum(math.dist(first, second) for first, second in zip(samples, samples[1:]))


def _orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False

    first = _orientation(a, b, c)
    second = _orientation(a, b, d)
    third = _orientation(c, d, a)
    fourth = _orientation(c, d, b)
    return first * second <= 0 and third * fourth <= 0


def _sampled_edge_crossing_count(edges: list[dict]) -> int:
    segments = []
    for edge in edges:
        samples = []
        for operation in edge["_draw_"]:
            if operation["op"] != "b":
                continue
            points = operation["points"]
            for segment_start in range(0, len(points) - 1, 3):
                control = points[segment_start : segment_start + 4]
                for step in range(9):
                    t = step / 8
                    u = 1 - t
                    samples.append(
                        (
                            u**3 * control[0][0]
                            + 3 * u**2 * t * control[1][0]
                            + 3 * u * t**2 * control[2][0]
                            + t**3 * control[3][0],
                            u**3 * control[0][1]
                            + 3 * u**2 * t * control[1][1]
                            + 3 * u * t**2 * control[2][1]
                            + t**3 * control[3][1],
                        )
                    )
        for start, end in zip(samples, samples[1:]):
            if math.dist(start, end) > 0.01:
                segments.append((edge.get("_gvid"), start, end))

    crossing_pairs = set()
    for index, (first_edge, a, b) in enumerate(segments):
        for second_edge, c, d in segments[index + 1 :]:
            if first_edge != second_edge and _segments_cross(a, b, c, d):
                crossing_pairs.add(tuple(sorted((first_edge, second_edge))))
    return len(crossing_pairs)


def _graph_width(layout: dict) -> float:
    left, _, right, _ = (float(value) for value in layout["bb"].split(","))
    return max(1.0, right - left)


def _pos_points(pos: str) -> list[tuple[float, float]]:
    values = [
        float(value)
        for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", pos)
    ]
    return list(zip(values[0::2], values[1::2]))


def _point_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.dist(point, start)
    t = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2,
        ),
    )
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _drawn_edge_polyline(edge: dict) -> list[tuple[float, float]]:
    """Flatten an edge's drawn route enough to catch tangent near-touches."""

    route = []
    for operation in edge.get("_draw_", []):
        if operation["op"] == "L":
            samples = [tuple(point) for point in operation["points"]]
        elif operation["op"] in {"B", "b"}:
            samples = []
            points = operation["points"]
            for start in range(0, len(points) - 1, 3):
                control = points[start : start + 4]
                assert len(control) == 4
                for step in range(13):
                    t = step / 12
                    u = 1 - t
                    samples.append(
                        (
                            u**3 * control[0][0]
                            + 3 * u**2 * t * control[1][0]
                            + 3 * u * t**2 * control[2][0]
                            + t**3 * control[3][0],
                            u**3 * control[0][1]
                            + 3 * u**2 * t * control[1][1]
                            + 3 * u * t**2 * control[2][1]
                            + t**3 * control[3][1],
                        )
                    )
        else:
            continue
        if route and samples and math.dist(route[-1], samples[0]) < 0.01:
            route.extend(samples[1:])
        else:
            route.extend(samples)
    return route


def _segment_distance(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> tuple[float, float, float]:
    """Return segment distance and normalized positions along both segments."""

    candidates = []
    for point, start, end, first_segment in (
        (a, c, d, True),
        (b, c, d, True),
        (c, a, b, False),
        (d, a, b, False),
    ):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length2 = dx * dx + dy * dy
        t = 0.0
        if length2 != 0:
            t = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                    / length2,
                ),
            )
        foot = (start[0] + t * dx, start[1] + t * dy)
        if first_segment:
            candidates.append((math.dist(point, foot), 0.0 if point == a else 1.0, t))
        else:
            candidates.append((math.dist(point, foot), t, 0.0 if point == c else 1.0))
    return min(candidates, key=lambda candidate: candidate[0])


def _route_lengths(points: list[tuple[float, float]]) -> list[float]:
    lengths = [0.0]
    for first, second in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(first, second))
    return lengths


def _tangential_touching_pair_count(layout: dict) -> int:
    """Count distinct drawn route pairs that almost touch while tangent."""

    routes = [
        (edge, _drawn_edge_polyline(edge))
        for edge in layout["edges"]
        if "_draw_" in edge
    ]
    routes = [(edge, route) for edge, route in routes if len(route) >= 2]
    count = 0
    for index, (first_edge, first_route) in enumerate(routes):
        first_lengths = _route_lengths(first_route)
        for second_edge, second_route in routes[index + 1 :]:
            second_lengths = _route_lengths(second_route)
            found = False
            for first_index, (a, b) in enumerate(zip(first_route, first_route[1:])):
                first_len = max(0.001, math.dist(a, b))
                for second_index, (c, d) in enumerate(
                    zip(second_route, second_route[1:])
                ):
                    if _segments_cross(a, b, c, d):
                        continue
                    distance, ta, tb = _segment_distance(a, b, c, d)
                    if distance >= 5:
                        continue
                    first_pos = first_lengths[first_index] + ta * first_len
                    second_len = max(0.001, math.dist(c, d))
                    second_pos = second_lengths[second_index] + tb * second_len
                    if (
                        min(
                            first_pos,
                            first_lengths[-1] - first_pos,
                            second_pos,
                            second_lengths[-1] - second_pos,
                        )
                        < 20
                    ):
                        continue
                    if _angle_between_vectors(
                        (b[0] - a[0], b[1] - a[1]),
                        (d[0] - c[0], d[1] - c[1]),
                    ) < 15:
                        found = True
                        break
                if found:
                    break
            if found:
                count += 1
    return count


def _endpoint_label_text_ops(edge: dict, stream: str) -> list[dict]:
    font_size = 8.0
    labels = []
    for operation in edge.get(stream, []):
        if operation["op"] == "F":
            font_size = operation["size"]
        elif operation["op"] == "T":
            labels.append(
                {
                    "center": tuple(operation["pt"]),
                    "width": operation["width"],
                    "height": font_size,
                    "font_size": font_size,
                }
            )
    return labels


def _endpoint_label_overlap_area(first: dict, second: dict) -> float:
    ax, ay = first["center"]
    bx, by = second["center"]
    x_overlap = max(
        0.0,
        min(ax + first["width"] / 2, bx + second["width"] / 2)
        - max(ax - first["width"] / 2, bx - second["width"] / 2),
    )
    y_overlap = max(
        0.0,
        min(ay + first["height"] / 2, by + second["height"] / 2)
        - max(ay - first["height"] / 2, by - second["height"] / 2),
    )
    return x_overlap * y_overlap


def _endpoint_label_detach_honda_score(layout: dict) -> tuple[int, int]:
    labels = []
    detached = 0
    for edge in layout["edges"]:
        points = _pos_points(edge.get("pos", ""))
        for stream in ("_hldraw_", "_tldraw_"):
            for label in _endpoint_label_text_ops(edge, stream):
                distance = min(
                    (
                        _point_segment_distance(label["center"], start, end)
                        for start, end in zip(points, points[1:])
                    ),
                    default=math.inf,
                )
                if distance / label["font_size"] > 0.45:
                    detached += 1
                labels.append(label)

    overlaps = 0
    for first, second in itertools.combinations(labels, 2):
        area = _endpoint_label_overlap_area(first, second)
        smaller = min(
            first["width"] * first["height"], second["width"] * second["height"]
        )
        if smaller > 0 and area / smaller > 0.50:
            overlaps += 1
    return detached, overlaps


def _longest_horizontal_chain(points: list[tuple[float, float]]) -> float:
    best = 0.0
    current = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        angle = abs(math.degrees(math.atan2(dy, dx)))
        angle = min(angle, 180 - angle)
        if angle <= 10:
            current += length
            best = max(best, current)
        else:
            current = 0.0
    return best


def _long_horizontal_edge_count(layout: dict) -> int:
    min_chain = _graph_width(layout) * 0.4
    return sum(
        _longest_horizontal_chain(_pos_points(edge["pos"])) > min_chain
        for edge in layout["edges"]
        if "pos" in edge
    )


def _longest_layout_path(nodes: set[int], edges: list[tuple[int, int]]) -> list[int]:
    outgoing = {node: [] for node in nodes}
    for tail, head in edges:
        if tail in nodes and head in nodes:
            outgoing.setdefault(tail, []).append(head)

    def walk(node: int, active: frozenset[int] = frozenset()) -> tuple[int, ...]:
        if node in active:
            return ()
        best = (node,)
        next_active = active | {node}
        for head in outgoing.get(node, []):
            suffix = walk(head, next_active)
            if suffix and len((node,) + suffix) > len(best):
                best = (node,) + suffix
        return best

    return list(max((walk(node) for node in nodes), key=len, default=()))


def _normalized_longest_path_drift(layout: dict) -> float:
    centers = {
        node["_gvid"]: tuple(float(value) for value in node["pos"].split(",", 1))
        for node in layout["objects"]
        if "pos" in node
    }
    path = _longest_layout_path(
        set(centers),
        [(edge["tail"], edge["head"]) for edge in layout["edges"]],
    )
    xs = [centers[node][0] for node in path]
    assert xs
    return (max(xs) - min(xs)) / _graph_width(layout)


_CONCENTRATE_ABSTRACT_HORIZONTAL_BUS_MINIMIZED = r"""
    digraph abstract {
      graph [concentrate=true, size="6,6"]
      10 -> T1
      10 -> 11
      10 -> 14
      10 -> 13
      10 -> 12
      11 -> 4
      14 -> 15
      13 -> 19
      4 -> 5
      15 -> T1
      3 -> 4
      S35 -> 36
      S35 -> 43
      36 -> 19
      43 -> 38
      43 -> 40
      38 -> 4
      40 -> 19
      S30 -> 31
      S30 -> 33
      31 -> T1
      31 -> 32
      9 -> T1
      9 -> 42
      42 -> 4
      37 -> 38
      37 -> 40
      37 -> 39
      37 -> 41
      39 -> 15
    }
"""


_CONCENTRATE_ROWE_SPINE_BEND_MINIMIZED = r"""
    digraph rowe {
      graph [concentrate=true, size="6,6"]
      node [shape=box]
      4 -> 5
      5 -> 23
      5 -> 35
      23 -> 24
      35 -> 36
      24 -> 25
      24 -> 27
      36 -> 19
      25 -> 26
      19 -> 28
      19 -> 21
      26 -> 4
      28 -> 29
      21 -> 22
      29 -> 30
      22 -> 23
      30 -> 31
      30 -> 33
      31 -> 32
      33 -> 34
      32 -> 23
      40 -> 19
      38 -> 4
      37 -> 40
      37 -> 38
    }
"""


def _point_distance_to_line(
    point: tuple[float, float] | list[float],
    start: tuple[float, float] | list[float],
    end: tuple[float, float] | list[float],
) -> float:
    """Return a point's perpendicular distance from an infinite line."""

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    assert length > 0
    return abs(dx * (start[1] - point[1]) - dy * (start[0] - point[0])) / length


def _max_bezier_deviation_from_chord(points: list[list[float]]) -> float:
    """Sample a Bezier and measure how far it bows from its endpoint chord."""

    return max(
        _point_distance_to_line(point, points[0], points[-1])
        for point in _sample_bezier_points(points)
    )


def _edge_bezier_points(edge: dict) -> list[list[float]]:
    """Return the control points of an edge's visible route."""

    return next(operation["points"] for operation in edge["_draw_"] if operation["op"] == "b")


def _sample_drawn_edge(edge: dict) -> tuple[tuple[float, float], ...]:
    """Sample all visible Bezier segments for one drawn edge."""

    return tuple(
        point
        for operation in edge["_draw_"]
        if operation["op"] == "b"
        for point in _sample_bezier_points(operation["points"])
    )


def _max_pointwise_route_distance(first_edge: dict, second_edge: dict) -> float:
    """Compare two sampled routes at equal normalized sample indexes."""

    first = _sample_drawn_edge(first_edge)
    second = _sample_drawn_edge(second_edge)
    sample_count = min(len(first), len(second))
    assert sample_count > 1
    return max(math.dist(first[index], second[index]) for index in range(sample_count))


def _ellipse(node: dict) -> tuple[float, float, float, float]:
    """Return an ellipse node's center and radii."""

    return tuple(
        next(operation["rect"] for operation in node["_draw_"] if operation["op"] == "e")
    )


def _assert_endpoint_departure(layout: dict, edge: dict, endpoint: str) -> None:
    """A route leaves an endpoint outward and does not re-enter its stroke."""

    node_id = edge["tail" if endpoint == "tail" else "head"]
    node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
    center_x, center_y, radius_x, radius_y = _ellipse(node)
    points = _edge_bezier_points(edge)
    # Flat auxiliary routing can preserve the Bezier in physical rather than
    # logical edge order. Orient it by the endpoint node under test.
    route = min(
        (points, list(reversed(points))),
        key=lambda candidate: math.dist(candidate[0], (center_x, center_y)),
    )
    anchor = route[0]
    outward_point = next(point for point in route[1:] if math.dist(point, anchor) > 0.001)
    outward_normal = (anchor[0] - center_x, anchor[1] - center_y)
    departure = (outward_point[0] - anchor[0], outward_point[1] - anchor[1])
    assert sum(a * b for a, b in zip(outward_normal, departure)) > 0

    pen_radius = float(edge.get("penwidth", 1)) / 2
    sampled = tuple(_sample_bezier_points(route))
    normalized = tuple(
        ((x - center_x) / (radius_x + pen_radius)) ** 2
        + ((y - center_y) / (radius_y + pen_radius)) ** 2
        for x, y in sampled
    )
    clear_index = next(index for index, distance in enumerate(normalized) if distance >= 1)
    # Xdot rounds every control point to 0.01 pt; allow the corresponding
    # sub-point sampling drift around the half-stroke clearance boundary.
    assert min(normalized[clear_index:]) >= 0.98


def _assert_compass_attachment(layout: dict, edge: dict, endpoint: str) -> None:
    """A named compass port attaches at the requested ellipse extremum."""

    port_name = edge.get(f"{endpoint}port")
    if port_name not in {"n", "s", "e", "w"}:
        return
    node_id = edge["tail" if endpoint == "tail" else "head"]
    node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
    center_x, center_y, radius_x, radius_y = _ellipse(node)
    attachment = _edge_physical_endpoint(edge, endpoint)
    expected = {
        "n": (center_x, center_y + radius_y),
        "s": (center_x, center_y - radius_y),
        "e": (center_x + radius_x, center_y),
        "w": (center_x - radius_x, center_y),
    }[port_name]
    # The route clips against the shape outline, while the `e` marker records
    # the arrow tip one point beyond its shaft and xdot reports the stroked
    # ellipse. Account for both representations without admitting a field- or
    # node-center attachment.
    assert attachment == pytest.approx(expected, abs=1.5)


def _assert_distinct_drawn_edge_routes(source: str, expected_count: int) -> None:
    """Assert the number of distinct visible routes in a graph."""

    routes = {_route_x_coordinates(edge) for edge in _drawn_edges(source)}
    assert len(routes) == expected_count


def _edge_count_case(expected_count: int, *body: str) -> tuple[int, tuple[str, ...]]:
    """Describe one graph and its expected number of visible edges."""

    return expected_count, body


def _named_edge_count_cases(case_id: str, *cases: tuple[int, tuple[str, ...]]):
    """Give a group of count oracles one meaningful pytest ID."""

    return pytest.param(cases, id=case_id)


def _fixed_edge_count_cases(
    case_id: str, splines: str, *cases: tuple[int, tuple[str, ...]]
):
    """Give fixed-spline count oracles one meaningful pytest ID."""

    return pytest.param(splines, cases, id=case_id)


def _assert_concentrated_edge_counts(
    splines: str, cases: tuple[tuple[int, tuple[str, ...]], ...]
) -> None:
    """Check every independent drawn-edge-count oracle in a table row."""

    for expected_count, body in cases:
        source = _concentrated_graph(splines, *body)
        assert len(_drawn_edges(source)) == expected_count


_SHARED_TRUNK_FIXTURE = """
    digraph {
      graph [concentrate=true]
      { rank=min; a; b }
      a -> c
      c -> e
      e -> d
      %s
    }
"""


_SHARED_TRUNK_DISTINCT_ORDERS = (
    pytest.param(
        (
            "a -> d [color=blue]",
            "b -> d [color=red]",
        ),
        id="blue-a-red-b",
    ),
    pytest.param(
        (
            "b -> d [color=red]",
            "a -> d [color=blue]",
        ),
        id="red-b-blue-a",
    ),
)


def _shared_trunk_source(*edges: str) -> str:
    return _SHARED_TRUNK_FIXTURE % "\n".join(f"        {edge}" for edge in edges)


_BACKWARD_PARALLEL_COLORS_FIXTURE = """
    digraph {
      graph [concentrate=true, ranksep=1.2]
      node [shape=circle, width=0.45, fixedsize=true]
      edge [arrowsize=0.8, penwidth=3]
      b -> c [style=invis]
      c -> a [style=invis]
      a -> b [constraint=false, color=red]
      a -> b [constraint=false, color=blue]
      a -> b [constraint=false, color=red]
    }
"""


def _find_plugin_so(plugin: str) -> Optional[Path]:
    """
    find the absolute path to the dynamic library for a given Graphviz plugin

    Args:
        plugin: Name of the plugin being sought

    Return:
        An absolute path to the corresponding installed dynamic library or `None` if it
        could not be found.
    """

    dot_bin = which("dot")
    root = dot_bin.parents[1]
    current, revision, age = plugin_version()

    for subdir in ("lib", "lib64"):
        if is_macos():
            candidate = root / subdir / f"graphviz/libgvplugin_{plugin}.dylib"
        elif is_mingw():
            candidate = root / f"bin/libgvplugin_{plugin}-{current - age}.dll"
        elif platform.system() == "Windows":
            candidate = root / subdir / f"gvplugin_{plugin}.lib"
        else:
            candidate = root / subdir / f"graphviz/libgvplugin_{plugin}.so"
        print(f"checking {candidate}")
        if candidate.exists():
            return candidate

        if platform.system() == "Linux":
            suffix = f".{current - age}.{age}.{revision}"
            candidate = root / subdir / f"graphviz/libgvplugin_{plugin}.so{suffix}"
            print(f"checking {candidate}")
            if candidate.exists():
                return candidate

    return None


def _dynamic_graphviz_link() -> tuple[list[Union[str, Path]], tuple[Path, ...]]:
    core = _find_plugin_so("core")
    dot_layout = _find_plugin_so("dot_layout")
    if core is None or dot_layout is None:
        build_root = which("dot").resolve().parents[2]
        core = build_root / "plugin/core/libgvplugin_core.so"
        dot_layout = build_root / "plugin/dot_layout/libgvplugin_dot_layout.so"
        cgraph = build_root / "lib/cgraph/libcgraph.so"
        gvc = build_root / "lib/gvc/libgvc.so"
        for library in (core, dot_layout, cgraph, gvc):
            assert library.exists(), f"missing build library {library}"
        link = [cgraph, gvc, core, dot_layout]
        library_directories = (
            cgraph.parent,
            gvc.parent,
            core.parent,
            dot_layout.parent,
        )
    else:
        link = ["cgraph", "gvc", core, dot_layout]
        library_directories = (core.parent, dot_layout.parent)

    return link, library_directories


def _env_with_library_path(library_directories: tuple[Path, ...]) -> dict:
    env = os.environ.copy()
    library_paths = os.pathsep.join(str(path) for path in library_directories)
    loader_path = "DYLD_LIBRARY_PATH" if is_macos() else "LD_LIBRARY_PATH"
    env[loader_path] = os.pathsep.join(
        part for part in (library_paths, env.get(loader_path, "")) if part
    )
    return env


def _compile_concentrate_c_test(
    tmp_path: Path,
    source_name: str,
    exe_name: str,
    *,
    extra_includes: tuple[Path, ...] = (),
) -> tuple[Path, dict]:
    link, library_directories = _dynamic_graphviz_link()
    source_lib = Path(__file__).parent.parent / "lib"
    exe = tmp_path / exe_name
    compile_c(
        Path(__file__).parent / source_name,
        cflags=[
            *(f"-I{include}" for include in extra_includes),
            f"-I{source_lib}",
            f"-I{source_lib / 'cdt'}",
            f"-I{source_lib / 'cgraph'}",
            f"-I{source_lib / 'common'}",
            f"-I{source_lib / 'gvc'}",
            f"-I{source_lib / 'pathplan'}",
        ],
        link=link,
        dst=exe,
    )
    return exe, _env_with_library_path(library_directories)


def _compile_concentrate_edge_identity_tooltip_test(tmp_path: Path) -> tuple[Path, dict]:
    return _compile_concentrate_c_test(
        tmp_path,
        "concentrate_edge_identity_tooltip.c",
        "concentrate-edge-identity-tooltip",
    )


_SAME_RANK_REVERSE_EDGES = """
    strict digraph {
      concentrate=true
      subgraph same_rank {
        rank=same
        a
        b
      }
      a -> b
      b -> a
    }
"""


def _assert_public_concentrate_crash_repro_renders(issue: int):
    input = Path(__file__).parent / f"{issue}.dot"
    assert input.exists(), "unexpectedly missing test case"

    proc = subprocess.run(
        [which("dot"), "-Kdot", "-Tdot", input],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    for token in ("AddressSanitizer", "DEADLYSIGNAL", "SEGV"):
        assert token not in proc.stderr
    assert any("->" in line and "pos=" in line for line in proc.stdout.splitlines())
