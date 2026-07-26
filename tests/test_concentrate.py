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

def test_curved_concentrated_attributed_edges_do_not_crash():
    """Curved route concentration compares original edges, not virtual pieces."""

    for input in (
        Path(__file__).parent
        / "graphs"
        / "concentrate-demo"
        / "curved-concentrated-attributed-parallel.dot",
        Path(__file__).parent
        / "graphs"
        / "concentrate-demo"
        / "curved-concentrated-attributed-chain.dot",
    ):
        assert input.exists(), "unexpectedly missing test case"
        proc = subprocess.run(
            [which("dot"), "-Tjson", input],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")


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


@pytest.mark.parametrize(
    ("case", "source", "plain_count", "concentrate_count"),
    (
        pytest.param(
            "plain-duplicates",
            _graph_with_concentrate(True, "a -> b", "a -> b", "a -> b"),
            3,
            1,
            id="plain-duplicates",
        ),
        pytest.param(
            "same-rendered-attrs",
            _graph_with_concentrate(
                True,
                "a -> b [color=blue style=dashed penwidth=2]",
                "a -> b [color=blue style=dashed penwidth=2]",
                "a -> b [color=blue style=dashed penwidth=2]",
            ),
            3,
            1,
            id="same-rendered-attrs",
        ),
        pytest.param(
            "reverse-same-physical-endpoint",
            _graph_with_concentrate(
                True,
                "a -> b [samehead=x]",
                "b -> a [sametail=x]",
            ),
            2,
            1,
            id="reverse-same-physical-endpoint",
        ),
    ),
)
def test_concentrate_positive_route_gain(
    case: str, source: str, plain_count: int, concentrate_count: int
):
    """Equivalent edge groups still reduce visible drawn routes."""

    assert case
    _assert_concentrate_route_gain(source, plain_count, concentrate_count)


def test_concentrate_issue_2764_bogus_record_ports_still_merge():
    """Unresolved record ports fall back to center and remain concentratable."""

    source = Path(__file__).with_name("2764.dot").read_text(encoding="utf-8")
    _assert_concentrate_route_gain(source, 5, 4)


def _drawn_route_topology(source: str) -> tuple[int, tuple[str, ...]]:
    """Return a compact topology signature for concentrate metamorphic checks."""

    colors = tuple(sorted(_drawn_edge_color(edge) for edge in _drawn_edges(source)))
    return len(colors), colors


def test_concentrate_metamorphic_edge_permutation_invariance():
    """Equivalent duplicate placement does not change the chosen topology."""

    bodies = [
        ("a -> b [color=red]", "a -> b [color=blue]", "a -> b [color=red]"),
        ("a -> b [color=red]", "a -> b [color=red]", "a -> b [color=blue]"),
        ("a -> b [color=blue]", "a -> b [color=red]", "a -> b [color=red]"),
    ]
    signatures = {
        _drawn_route_topology(_concentrated_graph("", *body)) for body in bodies
    }
    assert signatures == {(2, ("#0000ff", "#ff0000"))}


def test_concentrate_metamorphic_duplicate_pressure_monotonicity():
    """Adding exact duplicates never adds visible concentrate lanes."""

    route_counts = [
        len(_drawn_edges(_concentrated_graph("", *("a -> b" for _ in range(count)))))
        for count in range(1, 7)
    ]
    assert route_counts == [1, 1, 1, 1, 1, 1]


def test_concentrate_metamorphic_exact_duplicates_add_no_visual_lanes():
    """Exact duplicates render as one visual lane, not N parallel lanes."""

    source = _graph_with_concentrate(
        True,
        "a -> b [color=red penwidth=2]",
        "a -> b [color=red penwidth=2]",
        "a -> b [color=red penwidth=2]",
    )
    assert _drawn_route_topology(source) == (1, ("#ff0000",))


def test_concentrate_metamorphic_rankdir_mirror_symmetry():
    """Mirroring rank direction preserves the concentrate topology."""

    left_to_right = _concentrated_graph(
        "rankdir=LR",
        "a -> b [color=red]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    right_to_left = _concentrated_graph(
        "rankdir=RL",
        "a -> b [color=red]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    assert _drawn_route_topology(left_to_right) == _drawn_route_topology(right_to_left)


def test_concentrate_metamorphic_invisible_degree2_node_insertion_stability():
    """An unrelated invisible degree-2 node does not perturb duplicate routing."""

    base = _concentrated_graph("", "a -> b", "a -> b")
    inserted = _concentrated_graph(
        "",
        "a -> b",
        "a -> x [style=invis]",
        "x -> b [style=invis]",
        "a -> b",
    )
    assert len(_drawn_edges(base)) == 1
    assert len(_drawn_edges(inserted)) == 1


def test_concentrate_metamorphic_no_worse_than_baseline_primary_metric():
    """The primary route-count metric is no worse with concentrate enabled."""

    source = _graph_with_concentrate(
        True,
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
        "b -> c",
        "b -> c",
    )
    plain_routes = len(_drawn_edges(_set_graph_concentrate(source, False)))
    concentrated_routes = len(_drawn_edges(_set_graph_concentrate(source, True)))
    assert concentrated_routes <= plain_routes


def test_concentrate_metamorphic_layout_determinism():
    """The same concentrated input produces stable JSON layout topology."""

    source = _concentrated_graph(
        "",
        "a -> b [color=red]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "b -> c",
    )
    layouts = [json.loads(dot("json", source=source)) for _ in range(3)]
    assert layouts[0]["edges"] == layouts[1]["edges"] == layouts[2]["edges"]


def test_concentrate_oracle_trace_developer_mode(tmp_path: Path):
    """GV_CONCENTRATE_ORACLE emits a fenced plan diff only when requested."""

    source = tmp_path / "oracle.dot"
    source.write_text(
        """
        digraph {
          graph [concentrate=true]
          a -> b [color=red]
          a -> b [color=red]
          a -> b [color=blue]
        }
        """,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["GV_CONCENTRATE_ORACLE"] = "1"
    env["GV_CONCENTRATE_ORACLE_MAX_EDGES"] = "8"
    completed = subprocess.run(
        ["dot", "-Txdot", source],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert "concentrate-oracle: begin" in completed.stderr
    assert "heuristic=" in completed.stderr
    assert "best=" in completed.stderr
    assert "variants=no-merge,join-at-rank,sub-bundles,full-trunk" in completed.stderr


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


@pytest.mark.parametrize("fixture", ("sb_box_dbl.gv", "sb_circle_dbl.gv"))
def test_concentrated_duplicate_self_edge_labels_do_not_overlap(fixture: str):
    """
    Concentrating identical labeled self-edges should not overprint duplicate
    label instances.
    """

    source = (Path(__file__).parent / "graphs" / fixture).read_text().replace(
        "{", "{\n  graph [concentrate=true];", 1
    )
    boxes = _edge_label_boxes(source)
    assert len(boxes) == 2
    for (_, first), (_, second) in itertools.combinations(boxes, 2):
        assert _box_gap(first, second) >= 4.0


@pytest.mark.parametrize("fixture", ("sb_box_dbl.gv", "sb_circle_dbl.gv"))
def test_concentrated_duplicate_self_edge_labels_sit_clear_of_drawing(fixture: str):
    """
    Deduped self-edge labels should sit beside their loop, not on nodes or
    routed strokes.
    """

    source = (Path(__file__).parent / "graphs" / fixture).read_text().replace(
        "{", "{\n  graph [concentrate=true];", 1
    )
    layout = _json_layout(source)
    boxes = _edge_label_boxes_from_layout(layout)
    assert len(boxes) == 2

    for text, box in boxes:
        for node_name, node_box in _node_boxes(layout):
            assert not _boxes_overlap(box, node_box), (text, node_name)
        for edge in layout["edges"]:
            route = _drawn_edge_polyline(edge)
            for start, end in zip(route, route[1:]):
                assert _point_segment_distance(
                    ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), start, end
                ) > min(box[2] - box[0], box[3] - box[1]) / 2


def test_concentrated_duplicate_self_edge_routes_merge_with_one_label():
    """Exact duplicate labeled self-loops render as one loop and one label."""

    source = _concentrated_graph(
        "",
        'node [shape=circle]',
        'a -> a [label="tailport=n headport=n" tailport=n headport=n]',
        'a -> a [label="tailport=n headport=n" tailport=n headport=n]',
    )
    layout = _json_layout(source)
    drawn_self_edges = [
        edge
        for edge in layout["edges"]
        if edge["tail"] == edge["head"] and "_draw_" in edge
    ]
    labeled_edges = [
        edge
        for edge in drawn_self_edges
        if any(stream.get("op") == "T" for stream in edge.get("_ldraw_", []))
    ]

    assert len(drawn_self_edges) == 1
    assert len(labeled_edges) == 1


def test_concentrated_duplicate_self_edge_label_is_not_replaced_as_xlabel():
    """A deduped duplicate label must not be placed again by addXLabels()."""

    label = "tailport=n headport=n"
    source = _concentrated_graph(
        "",
        "node [shape=circle]",
        f'a -> a [label="{label}" tailport=n headport=n]',
        f'a -> a [label="{label}" tailport=n headport=n]',
    )
    layout = _json_layout(source)
    label_draws = [
        operation["text"]
        for edge in layout["edges"]
        for operation in edge.get("_ldraw_", [])
        if operation["op"] == "T"
    ]

    assert label_draws == [label]


def test_concentrated_left_self_edge_label_releases_duplicate_route_width():
    """Left self-loop dedupe should not reserve the deleted duplicate lane."""

    source = (Path(__file__).parent / "graphs" / "sl_box_dbl.gv").read_text()
    concentrated = source.replace("{", "{\n  graph [concentrate=true];", 1)
    unconcentrated = source.replace("{", "{\n  graph [concentrate=false];", 1)

    layout = _json_layout(concentrated)
    drawn_self_edges = [
        edge
        for edge in layout["edges"]
        if edge["tail"] == edge["head"] and "_draw_" in edge
    ]
    label_draws = [
        operation["text"]
        for edge in layout["edges"]
        for operation in edge.get("_ldraw_", [])
        if operation["op"] == "T"
    ]

    assert len(drawn_self_edges) == 21
    assert len(label_draws) == 21
    assert _graph_width(layout) < _graph_width(_json_layout(unconcentrated)) * 0.7


def test_concentrated_left_self_edge_label_tracks_loop_height():
    """Deduped left self-loop labels should center on the retained loop."""

    source = (Path(__file__).parent / "graphs" / "sl_box_dbl.gv").read_text()
    concentrated = source.replace("{", "{\n  graph [concentrate=true];", 1)

    layout = _json_layout(concentrated)
    objects = layout["objects"]
    labels_by_tail = {}
    for edge in layout["edges"]:
        tail_name = objects[edge["tail"]]["name"]
        head_name = objects[edge["head"]]["name"]
        if tail_name != head_name or "_draw_" not in edge or "lp" not in edge:
            continue
        route = _drawn_edge_polyline(edge)
        route_center_y = (
            min(point[1] for point in route) + max(point[1] for point in route)
        ) / 2
        label_y = float(edge["lp"].split(",")[1])
        labels_by_tail[tail_name] = (label_y, route_center_y)

    for node_name in ("node11", "node12"):
        label_y, route_center_y = labels_by_tail[node_name]
        assert label_y == pytest.approx(route_center_y, abs=1.0)


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


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_samehead_anchor_spans_regular_backward_and_flat_routes(
    concentrate: bool,
):
    """Every samehead member meets its node at one physical point (GitLab #448)."""

    anchors = _group_anchor_points(
        _samehead_mixed_route_fixture(concentrate),
        node_name="A",
        endpoint="head",
        attribute="samehead",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_sametail_anchor_spans_regular_backward_and_flat_routes(
    concentrate: bool,
):
    """Every sametail member leaves its node at one physical point (GitLab #448)."""

    anchors = _group_anchor_points(
        _sametail_mixed_route_fixture(concentrate),
        node_name="A",
        endpoint="tail",
        attribute="sametail",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_samehead_flat_edges_share_the_resolved_anchor(
    concentrate: bool,
):
    """Flat adjacent samehead edges keep the group port selected by dot."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; a; b; z; }}
          a -> z [samehead=x]
          b -> z [samehead=x]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="head",
        attribute="samehead",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_sametail_flat_edges_share_the_resolved_anchor(
    concentrate: bool,
):
    """Flat adjacent sametail edges keep the group port selected by dot."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; z; a; b; }}
          z -> a [sametail=x]
          z -> b [sametail=x]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="tail",
        attribute="sametail",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_distinct_samehead_groups_keep_distinct_anchors(
    concentrate: bool,
):
    """Different samehead IDs do not acquire a shared physical port."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          b0 -> B [samehead=y]
          b1 -> B [samehead=z]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="B",
        endpoint="head",
        attribute="samehead",
        groups={"y", "z"},
    )
    assert len(anchors) == 2, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_distinct_sametail_groups_keep_distinct_anchors(
    concentrate: bool,
):
    """Different sametail IDs do not acquire a shared physical port."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          A -> a0 [sametail=y]
          A -> a1 [sametail=z]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="A",
        endpoint="tail",
        attribute="sametail",
        groups={"y", "z"},
    )
    assert len(anchors) == 2, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_ungrouped_flat_edges_keep_independent_anchors(
    concentrate: bool,
):
    """The flat-edge fix does not create samehead behavior without samehead."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; a; b; z; }}
          a -> z
          b -> z
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="head",
        attribute="samehead",
        groups={""},
    )
    assert len(anchors) == 2, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_ungrouped_flat_tails_keep_independent_anchors(
    concentrate: bool,
):
    """The flat-edge fix does not create sametail behavior without sametail."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; z; a; b; }}
          z -> a
          z -> b
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="tail",
        attribute="sametail",
        groups={""},
    )
    assert len(anchors) == 2, anchors


def test_concentrate_flat_port_pair_crosses_steeply():
    """Opposite same-rank compass ports cross at a readable angle."""

    source = """
        digraph {
          graph [concentrate=true]
          { rank=same; a; b }
          a:n -> b:s [color=red]
          a:s -> b:n [color=red]
        }
    """
    edges = _drawn_edges(source)
    assert len(edges) == 2

    assert _crossing_angle(edges[0], edges[1]) >= 45


def test_concentrate_p3_crossings_do_not_exceed_base():
    """Concentration keeps p3 at the true-base sampled crossing count."""

    source = (Path(__file__).parent / "graphs" / "p3.gv").read_text().replace(
        "graph G {", "graph G {\n  graph [concentrate=true];", 1
    )
    edges = _drawn_edges(source)

    assert _sampled_edge_crossing_count(edges) <= 1


def _assert_concentrate_keeps_zero_crossings(source: str) -> None:
    plain_edges = _drawn_edges(_set_graph_concentrate(source, False))
    concentrated_edges = _drawn_edges(_set_graph_concentrate(source, True))

    assert _sampled_edge_crossing_count(plain_edges) == 0
    assert _sampled_edge_crossing_count(concentrated_edges) == 0


@pytest.mark.parametrize(
    ("case", "source"),
    (
        pytest.param(
            "corpus-0005-tree",
            _graph_with_concentrate(
                True,
                "root -> {lead_a lead_b}",
                "lead_a -> {team_a team_b team_c}",
                "lead_b -> {team_d team_e}",
                "team_d -> {leaf_a leaf_b leaf_c}",
            ),
            id="corpus-0005-tree",
        ),
        pytest.param(
            "corpus-0007-fanout",
            _graph_with_concentrate(
                True,
                "root -> {finance product engineering operations}",
                "product -> {analyst designer}",
                "engineering -> {frontend backend qa}",
                "operations -> support",
            ),
            id="corpus-0007-fanout",
        ),
        pytest.param(
            "corpus-0017-pipeline",
            _graph_with_concentrate(
                True,
                "rankdir=LR",
                "provider -> raw",
                "raw -> normalize -> normalized",
                "normalized -> recombine -> event -> rollup",
                "normalized -> event",
                "normalized -> episode",
                "feed -> snapshot -> version -> episode",
            ),
            id="corpus-0017-pipeline",
        ),
    ),
)
def test_concentrate_preserves_zero_crossings(case: str, source: str):
    """Before-zero corpus fixtures stay crossing-free after concentration."""

    assert case
    _assert_concentrate_keeps_zero_crossings(source)


def test_concentrate_train11_minimized_route_has_no_line_gap():
    """A reversed concentrated route keeps a continuous shaft to its endpoint."""

    source = r"""
        digraph G {
          graph [concentrate=true, rankdir=LR, size="6,6"]
          node [fontsize=8, shape=circle]
          st0 -> st0 [label="00/0"]
          st1 [fontsize=""]
          st0 -> st1 [label="10/-"]
          st5 [fontsize=""]
          st1 -> st5
          st3 [fontsize=""]
          st1 -> st3
          st5 -> st6 [label="01/1"]
          st4 [fontsize=""]
          st3 -> st4
          st6 -> st0 [label="00/-"]
          st6 -> st6 [label="01/1"]
          st4 -> st0 [label="00/-"]
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "st6", "st0")

    assert "_hdraw_" in edge
    assert all(gap <= 20 for gap in _drawn_edge_piece_end_gaps(edge))


def test_concentrate_drbd_minimized_routes_do_not_touch_tangentially():
    """Distinct concentrated routes should stay visually traceable."""

    source = r"""
        digraph disk_states {
          graph [concentrate=true]
          Inconsistent -> UpToDate [label="resync completed"]
          Inconsistent -> Failed [label="io completion error"]
          UpToDate -> Inconsistent [label=ioctl_replicate]
          UpToDate -> Failed [label="io completion error"]
          Consistent -> Inconsistent [label="start resync"]
          Consistent -> UpToDate [label="receive_param()"]
          Consistent -> Failed [label="io completion error"]
          Outdated -> Inconsistent [label="start resync"]
          Outdated -> Failed [label="io completion error"]
        }
    """
    layout = json.loads(dot("json", source=source))

    assert _tangential_touching_pair_count(layout) == 0


@pytest.mark.skipif(which("neato") is None, reason="neato not available")
def test_concentrate_train11_suppressed_arrows_survive_pos_roundtrip():
    """Suppressed junction arrows do not reappear after a positioned rerender."""

    source = (Path(__file__).parent / "graphs" / "train11.gv").read_text().replace(
        "digraph G {", "digraph G {\n  graph [concentrate=true];", 1
    )
    dot_layout = json.loads(dot("json", source=source))
    positioned = dot("dot", source=source)
    layout = json.loads(run(which("neato"), "-n2", "-Tjson", input=positioned))

    expected_hdraw = {
        tail: "_hdraw_" in _drawn_edge_between(dot_layout, tail, "st0")
        for tail in ("st8", "st6", "st4", "st10")
    }

    assert not expected_hdraw["st8"]
    assert expected_hdraw["st6"]
    assert expected_hdraw["st10"]
    for tail, has_hdraw in expected_hdraw.items():
        edge = _drawn_edge_between(layout, tail, "st0")
        assert ("_hdraw_" in edge) == has_hdraw


def test_concentrate_fanout_keeps_real_terminal_arrowheads():
    """Junction suppression does not hide arrows at real endpoint nodes."""

    source = """
        digraph {
          graph [concentrate=true]
          a -> c [minlen=2]
          a -> d [minlen=2]
        }
    """
    edges = _drawn_edges(source)

    assert len(edges) == 2
    assert all("_hdraw_" in edge for edge in edges)


def test_concentrate_backward_junction_arrows_follow_swapped_beziers():
    """Reversed concentrated splines keep endpoint arrow state with the points."""

    source = """
        digraph {
          graph [concentrate=true]
          a -> c [minlen=2]
          a -> d [minlen=2]
          c -> a [constraint=false minlen=2]
          d -> a [constraint=false minlen=2]
        }
    """
    edges = _drawn_edges(source)

    assert len(edges) == 2
    assert all(edge["pos"].startswith("s,") for edge in edges)
    assert all("_hdraw_" in edge and "_tdraw_" in edge for edge in edges)


def test_concentrate_compound_overlap_ignores_suppressed_junction_arrows():
    """Suppressed compound junction arrows do not collapse the retained route."""

    source = """
        digraph {
          graph [compound=true concentrate=true]
          node [shape=point width=0.05 height=0.05]
          subgraph cluster_tail {
            label=""
            margin=0
            t0
            t1
          }
          subgraph cluster_head {
            label=""
            margin=0
            h0
            h1
          }
          {rank=same; t0; h0}
          t0 -> h0 [ltail=cluster_tail lhead=cluster_head minlen=0 dir=both]
          h0 -> t0 [ltail=cluster_head lhead=cluster_tail minlen=0 dir=both]
        }
    """
    edges = _drawn_edges(source)
    assert len(edges) == 1
    points = _edge_bezier_points(edges[0])

    assert all(
        not (first == second == third)
        for first, second, third in zip(points, points[1:], points[2:])
    )


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


def test_concentrate_abstract_minimized_has_no_horizontal_bus():
    """Concentration should not create long flat bus segments."""

    layout = json.loads(dot("json", source=_CONCENTRATE_ABSTRACT_HORIZONTAL_BUS_MINIMIZED))
    assert _long_horizontal_edge_count(layout) < 3


def test_concentrate_rowe_minimized_spine_does_not_bend():
    """Concentration should not collapse a long spine into an S-bend."""

    layout = json.loads(dot("json", source=_CONCENTRATE_ROWE_SPINE_BEND_MINIMIZED))
    assert _normalized_longest_path_drift(layout) <= 0.733


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


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
@pytest.mark.parametrize(
    "cases",
    (
        _named_edge_count_cases(
            "distinct-endpoint-label-colors",
            _edge_count_case(
                4,
                "a -> b [label=x fontcolor=red]",
                "a -> b [label=x fontcolor=blue]",
                "b -> c [headlabel=x labelfontcolor=red]",
                "b -> c [headlabel=x labelfontcolor=blue]",
            ),
        ),
        _named_edge_count_cases(
            "ignore-unused-label-colors",
            _edge_count_case(
                4,
                """
                      a -> b [fontcolor=red]
                      a -> b [fontcolor=blue]
                      b -> c [labelfontcolor=red]
                      b -> c [labelfontcolor=blue]
                      c -> d [headlabel=x fontcolor=red labelfontcolor=black]
                      c -> d [headlabel=x fontcolor=blue labelfontcolor=black]
                      d -> e [fontsize=20 fontname=Courier labelfontsize=20
                              labelfontname=Courier labeldistance=2 labelangle=30
                              decorate=true]
                      d -> e
                    """,
            ),
        ),
        _named_edge_count_cases(
            "ignore-endpoint-label-decorate",
            _edge_count_case(
                1,
                "a -> b [headlabel=x decorate=false]",
                "a -> b [headlabel=x decorate=true]",
            ),
        ),
        _named_edge_count_cases(
            "labelaligned-plain-primary-label-only",
            _edge_count_case(
                1,
                "a -> b [labelaligned=true]",
                "a -> b",
            ),
        ),
        _named_edge_count_cases(
            "explicit-rendering-defaults",
            _edge_count_case(
                4,
                """
                      a -> b [style=solid penwidth=1 arrowsize=1]
                      a -> b
                      b -> c [labelfloat=false]
                      b -> c
                      c -> d [dir=none arrowsize=2]
                      c -> d [dir=none]
                      d -> e [dir=none fillcolor=red]
                      d -> e [dir=none fillcolor=blue]
                    """,
            ),
        ),
        _named_edge_count_cases(
            "tooltip-aliases-and-substitution",
            _edge_count_case(1, 'a -> b [tooltip="tip"]', 'a -> b [edgetooltip="tip"]'),
            _edge_count_case(
                1,
                'a -> b [tooltip="\\T"]',
                'b -> a [edgetooltip="\\T"]',
            ),
            _edge_count_case(
                1,
                'a -> b [headlabel=x headtooltip="\\T"]',
                'b -> a [taillabel=x tailtooltip="\\T"]',
            ),
        ),
        _named_edge_count_cases(
            "url-href-aliases",
            _edge_count_case(
                4,
                'a -> b [URL="u"]',
                'a -> b [href="u"]',
                'b -> c [edgeURL="u"]',
                'b -> c [edgehref="u"]',
                'c -> d [labelURL="u"]',
                'c -> d [labelhref="u"]',
                'd -> e [URL="u"]',
                'd -> e [edgeURL="u"]',
            ),
            _edge_count_case(1, 'a -> b [headURL="u"]', 'b -> a [tailhref="u"]'),
            _edge_count_case(2, 'a -> b [headURL="\\T"]', 'b -> a [tailhref="\\T"]'),
            _edge_count_case(2, "a -> b [URL=<\\T>]", "b -> a [href=<\\T>]"),
            _edge_count_case(2, 'a -> b [headURL="u"]', 'b -> a [headhref="u"]'),
        ),
        _named_edge_count_cases(
            "endpoint-url-is-not-edge-only-url",
            _edge_count_case(
                2,
                'a -> b [headlabel=x URL="u"]',
                'a -> b [headlabel=x edgeURL="u"]',
            ),
            _edge_count_case(
                2,
                'a -> b [headlabel=x URL="u" headtooltip="tip"]',
                'a -> b [headlabel=x edgeURL="u" headtooltip="tip"]',
            ),
            _edge_count_case(
                1,
                'a -> b [headlabel=x URL="u"]',
                'a -> b [headlabel=x href="u"]',
            ),
        ),
        _named_edge_count_cases(
            "target-fallbacks",
            _edge_count_case(
                1, "a -> b [URL=u target=t]", "a -> b [URL=u edgetarget=t]"
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x headURL=u target=t]",
                "a -> b [headlabel=x headURL=u headtarget=t]",
            ),
            _edge_count_case(
                2,
                "a -> b [headlabel=x URL=u headtarget=one]",
                "a -> b [headlabel=x URL=u headtarget=two]",
            ),
            _edge_count_case(
                2,
                "a -> b [URL=u target=<\\T>]",
                "b -> a [URL=u edgetarget=<\\T>]",
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x headtarget=one]",
                "a -> b [headlabel=x headtarget=two]",
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x headtarget=one]",
                "b -> a [taillabel=x tailtarget=two]",
            ),
        ),
        _named_edge_count_cases(
            "substituted-ids",
            _edge_count_case(1, 'a -> b [id="\\T"]', 'a -> b [id="a"]'),
            _edge_count_case(2, 'a -> b [id="\\T"]', 'b -> a [id="\\T"]'),
            _edge_count_case(2, "a -> b [id=<\\T>]", "b -> a [id=<\\T>]"),
        ),
        _named_edge_count_cases(
            "endpoint-label-font-fallbacks",
            _edge_count_case(
                1,
                "a -> b [headlabel=x fontsize=20]",
                "a -> b [headlabel=x fontsize=20 labelfontsize=20]",
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x fontname=Courier]",
                "a -> b [headlabel=x fontname=Courier labelfontname=Courier]",
            ),
        ),
        _named_edge_count_cases(
            "undeclared-endpoint-label-font-fallbacks",
            _edge_count_case(
                4,
                "a -> b [headlabel=x fontname=Courier]",
                "a -> b [headlabel=x fontname=Times]",
                "b -> c [headlabel=x fontsize=10]",
                "b -> c [headlabel=x fontsize=30]",
            ),
        ),
        _named_edge_count_cases(
            "samehead-sametail-physical-endpoint",
            _edge_count_case(1, "a -> b [samehead=x]", "b -> a [sametail=x]"),
            _edge_count_case(1, "a -> b [samehead=x]", "b -> a [samehead=x]"),
        ),
        _named_edge_count_cases(
            "lhead-ltail-physical-endpoint",
            _edge_count_case(
                1,
                "graph [compound=true]",
                "subgraph cluster_a { a }",
                "subgraph cluster_b { b }",
                "a -> b [ltail=cluster_a lhead=cluster_b]",
                "b -> a [ltail=cluster_b lhead=cluster_a]",
            ),
        ),
        _named_edge_count_cases(
            "gate-cluster-endpoints-on-compound",
            _edge_count_case(
                1,
                "a",
                "subgraph cluster_outer { subgraph cluster_inner { b } }",
                "a -> b [lhead=cluster_inner]",
                "b -> a [ltail=cluster_outer]",
            ),
            _edge_count_case(
                2,
                "graph [compound=true]",
                "a",
                "subgraph cluster_outer { subgraph cluster_inner { b } }",
                "a -> b [lhead=cluster_inner]",
                "b -> a [ltail=cluster_outer]",
            ),
        ),
        _named_edge_count_cases(
            # A shared-trunk join can leave the joining lhead edge with no
            # arrow of its own (the trunk accumulates the tip), and
            # makeCompoundEdge() must clip that arrowless compound spline
            # instead of asserting that an end arrow exists.
            "compound-trunk-arrowless-representative",
            _edge_count_case(
                2,
                "graph [compound=true]",
                "Andrus -> cluster_BE [minlen=4 lhead=cluster_BE weight=100]",
                "Alexei -> cluster_BE [minlen=4 lhead=cluster_BE]",
                "subgraph cluster_BE { cluster_BE -> AndrusBE [style=invis] }",
            ),
        ),
        _named_edge_count_cases(
            "endpoint-label-substitution",
            _edge_count_case(
                1,
                'a -> b [headlabel="\\H"]',
                'b -> a [taillabel="\\T"]',
            ),
            _edge_count_case(
                2,
                'a -> b [headlabel="\\T"]',
                'b -> a [taillabel="\\T"]',
            ),
            _edge_count_case(
                2,
                'a -> b [headlabel=<<TABLE HREF="\\T"><TR><TD>x</TD></TR></TABLE>>]',
                'b -> a [taillabel=<<TABLE HREF="\\T"><TR><TD>x</TD></TR></TABLE>>]',
            ),
        ),
        _named_edge_count_cases(
            "html-like-versus-plain-attribute",
            _edge_count_case(
                2,
                "a -> b [headlabel=<<B>x</B>>]",
                'a -> b [headlabel="<B>x</B>"]',
            ),
        ),
        _named_edge_count_cases(
            "dynamic-record-ports",
            _edge_count_case(
                2,
                "node [shape=record]",
                'a [label="<p> p"]',
                "b",
                "a:p -> b",
                "a:p:c -> b",
            ),
        ),
        _named_edge_count_cases(
            "dynamic-compass-ports",
            _edge_count_case(2, "a -> b:_", "a -> b"),
            _edge_count_case(2, "a:_ -> b", "a -> b"),
        ),
        _named_edge_count_cases(
            "ignore-layout-only-attributes",
            _edge_count_case(
                3,
                "a -> b [constraint=false]",
                "a -> b",
                "b -> c [weight=8]",
                "b -> c",
                "c -> d [minlen=2]",
                "c -> d",
            ),
        ),
        _named_edge_count_cases(
            "explicit-default-edge-attributes",
            _edge_count_case(
                3,
                "a -> b [dir=forward]",
                "a -> b",
                "b -> c [headclip=true]",
                "b -> c",
                "c -> d [tailclip=true]",
                "c -> d",
            ),
        ),
        _named_edge_count_cases(
            "invalid-numeric-defaults",
            _edge_count_case(1, "a -> b [penwidth=bogus]", "a -> b"),
            _edge_count_case(
                1,
                "b -> c [headlabel=x labeldistance=bogus]",
                "b -> c [headlabel=x]",
            ),
        ),
    ),
)
def test_concentrate_drawn_edge_counts(
    splines: str, cases: tuple[tuple[int, tuple[str, ...]], ...]
):
    """Structurally identical concentration scenarios keep readable case IDs."""

    _assert_concentrated_edge_counts(splines, cases)


def test_concentrate_main_label_bare_url_anchors_labeltarget():
    """Bare edge URL fallback makes main labeltarget visible to identity."""

    source = _concentrated_graph(
        "",
        "a -> b [label=x URL=u labeltarget=one]",
        "a -> b [label=x URL=u labeltarget=two]",
    )
    assert len(_drawn_edges(source)) == 2


def test_concentrate_html_table_anchor_identity_matches_emit_gate():
    """HTML table TARGET/ID are visible only on URL or tooltip anchors."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [headlabel=<<TABLE TARGET="one"><TR><TD>x</TD></TR></TABLE>>]',
                'b -> a [taillabel=<<TABLE TARGET="two"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                1,
                'c -> d [headlabel=<<TABLE ID="one"><TR><TD>x</TD></TR></TABLE>>]',
                'd -> c [taillabel=<<TABLE ID="two"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'e -> f [headlabel=<<TABLE HREF="u" TARGET="one" ID="one"><TR><TD>x</TD></TR></TABLE>>]',
                'f -> e [taillabel=<<TABLE HREF="u" TARGET="two" ID="two"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'g -> h [headlabel=<<TABLE HREF="u"><TR><TD TARGET="one">x</TD></TR></TABLE>>]',
                'h -> g [taillabel=<<TABLE HREF="u"><TR><TD TARGET="two">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'i -> j [label=<<TABLE TOOLTIP="tip"><TR><TD TARGET="one">x</TD></TR></TABLE>>]',
                'i -> j [label=<<TABLE TOOLTIP="tip"><TR><TD TARGET="two">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'k -> l [headlabel=<<TABLE BORDER="0"><TR><TD HREF="u">x</TD><TD>y</TD></TR></TABLE>>]',
                'k -> l [headlabel=<<TABLE BORDER="0"><TR><TD>x</TD><TD HREF="u">y</TD></TR></TABLE>>]',
            ),
        ),
    )


@pytest.mark.parametrize(
    ("splines", "cases"),
    (
        _fixed_edge_count_cases(
            "colorscheme-resolution",
            "",
            _edge_count_case(
                3,
                "a -> b [colorscheme=X11]",
                "a -> b",
                "b -> c [colorscheme=X11 color=green]",
                "b -> c [colorscheme=svg color=green]",
            ),
        ),
        _fixed_edge_count_cases(
            "colorscheme-color-list-resolution",
            "",
            _edge_count_case(
                3,
                'a -> b [colorscheme=accent3 color="1:2"]',
                'a -> b [colorscheme=accent3 color="1:2"]',
                'b -> c [colorscheme=accent3 color="1:2"]',
                'b -> c [colorscheme=paired3 color="1:2"]',
            ),
        ),
        _fixed_edge_count_cases(
            "main-label-decorate",
            "splines=ortho",
            _edge_count_case(
                1,
                "a -> b [label=x decorate=false]",
                "a -> b [label=x decorate=false]",
            ),
            _edge_count_case(
                2,
                "a -> b [label=x decorate=false]",
                "a -> b [label=x decorate=true]",
            ),
        ),
        _fixed_edge_count_cases(
            "labelaligned-plain-primary-label",
            "splines=ortho",
            _edge_count_case(
                4,
                "a -> b [label=<x> labelaligned=true]",
                "a -> b [label=<x>]",
                "b -> c [label=x labelaligned=true]",
                "b -> c [label=x]",
                "c -> d [xlabel=x labelaligned=true]",
                "c -> d [xlabel=x]",
            ),
        ),
        _fixed_edge_count_cases(
            "labelfloat-primary-label-gate",
            "splines=ortho",
            _edge_count_case(
                1,
                "a -> b [xlabel=x labelfloat=false]",
                "a -> b [xlabel=x labelfloat=true]",
            ),
            _edge_count_case(
                1,
                "a -> b [label=x labelfloat=false]",
                "a -> b [label=x labelfloat=false]",
            ),
            _edge_count_case(
                2,
                "a -> b [label=x labelfloat=false]",
                "a -> b [label=x labelfloat=true]",
            ),
        ),
        _fixed_edge_count_cases(
            "multicolor-arrow-fillcolor",
            "",
            _edge_count_case(
                3,
                'a -> b [color="red:blue" fillcolor=green]',
                'a -> b [color="red:blue" fillcolor=yellow]',
                "b -> c [color=red fillcolor=green]",
                "b -> c [color=red fillcolor=yellow]",
            ),
        ),
        _fixed_edge_count_cases(
            "label-tooltip-render-gate",
            "",
            _edge_count_case(
                3,
                "a -> b [labeltooltip=left]",
                "a -> b [labeltooltip=right]",
                "b -> c [label=x labeltooltip=left]",
                "b -> c [label=x labeltooltip=right]",
            ),
        ),
        _fixed_edge_count_cases(
            "implicit-endpoint-label-tooltip",
            "",
            _edge_count_case(
                3,
                "a -> b [headlabel=x headURL=u]",
                "a -> b [headlabel=x headURL=u headtooltip=x]",
                "b -> c [headlabel=x headURL=u]",
                "b -> c [headlabel=x headURL=u headtooltip=y]",
            ),
        ),
        _fixed_edge_count_cases(
            "preprocess-explicit-tooltip",
            "",
            _edge_count_case(
                3,
                'a -> b [tooltip="A&amp;B"]',
                'a -> b [tooltip="A&B"]',
                'b -> c [tooltip="A&amp;B"]',
                'b -> c [tooltip="A&C"]',
            ),
        ),
        _fixed_edge_count_cases(
            "overridden-endpoint-label-fonts",
            "",
            _edge_count_case(
                6,
                "a -> b [headlabel=x fontname=Courier labelfontname=Helvetica]",
                "a -> b [headlabel=x fontname=Times labelfontname=Helvetica]",
                "b -> c [headlabel=x fontsize=10 labelfontsize=20]",
                "b -> c [headlabel=x fontsize=30 labelfontsize=20]",
                "c -> d [headlabel=x fontname=Courier]",
                "c -> d [headlabel=x fontname=Times]",
                "d -> e [headlabel=x fontsize=10]",
                "d -> e [headlabel=x fontsize=30]",
            ),
        ),
    ),
)
def test_concentrate_fixed_spline_drawn_edge_counts(
    splines: str, cases: tuple[tuple[int, tuple[str, ...]], ...]
):
    """Count-only scenarios that intentionally use one spline mode."""

    _assert_concentrated_edge_counts(splines, cases)


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_numeric_identity_matches_late_double_prefixes(splines: str):
    """emit_begin_edge()/place_portlabel() consume late_double() prefixes."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, 'a -> b [penwidth="2pt"]', "a -> b [penwidth=2]"),
            _edge_count_case(
                1,
                'b -> c [headlabel=x labeldistance="1x"]',
                "b -> c [headlabel=x labeldistance=1]",
            ),
        ),
    )


def test_concentrate_color_list_identity_matches_parse_segs_fractions():
    """multicolor() consumes parseSegs() normalized segment fractions."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [dir=none color="red:blue"]',
                'a -> b [dir=none color="red;0.5:blue;0.5"]',
            ),
            _edge_count_case(
                1,
                'b -> c [dir=none color="red;1:blue"]',
                "b -> c [dir=none color=red]",
            ),
            _edge_count_case(
                2,
                'c -> d [dir=none color="red;0.5:blue;0.5"]',
                'd -> c [dir=none color="red;0.5:blue;0.5"]',
            ),
            _edge_count_case(
                2,
                'g -> h [dir=none color="red:blue"]',
                'h -> g [dir=none color="red:blue"]',
            ),
            _edge_count_case(
                1,
                'e -> f [dir=none color="red;0.5:blue;0.5"]',
                'f -> e [dir=none color="blue;0.5:red;0.5"]',
            ),
        ),
    )


def test_concentrate_color_list_identity_keeps_empty_lanes():
    """Empty color-list lanes change the stroke count, so they stay distinct.

    color="red::blue" renders three parallel-stroke lanes (raw colon
    count) while "red:blue" renders two; the identity keeps them
    separate."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [dir=none color="red::blue"]',
                'a -> b [dir=none color="red:blue"]',
            ),
        ),
    )


def test_concentrate_radius_identity_is_gated_on_ortho_edges():
    """emit_edge_graphics() consumes edge radius only for splines=ortho."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(1, "a -> b [radius=5]", "a -> b"),
            _edge_count_case(1, "b -> c [style=rounded]", "b -> c"),
        ),
    )
    _assert_concentrated_edge_counts(
        "splines=ortho",
        (
            _edge_count_case(2, "a -> b [radius=5]", "a -> b"),
            _edge_count_case(1, "c -> d [radius=bogus]", "c -> d"),
            _edge_count_case(1, "d -> e [radius=-5]", "d -> e"),
            _edge_count_case(1, "e -> f [radius=NaN]", "e -> f"),
            _edge_count_case(2, "b -> c [style=rounded]", "b -> c"),
        ),
    )


def test_concentrate_ortho_rounded_style_only_when_drawn():
    """emit_edge_graphics() ignores rounded on non-simple ortho edge branches."""

    _assert_concentrated_edge_counts(
        "splines=ortho",
        (
            _edge_count_case(
                1,
                'a -> b [color="red:blue" style=rounded]',
                'a -> b [color="red:blue"]',
            ),
            _edge_count_case(
                1,
                'b -> c [style="tapered,rounded"]',
                "b -> c [style=tapered]",
            ),
            _edge_count_case(2, "c -> d [style=rounded]", "c -> d"),
        ),
    )


def test_concentrate_layer_identity_requires_declared_graph_layers():
    """emit_edge() ignores edge layer when no graph layers are declared."""

    _assert_concentrated_edge_counts(
        "",
        (_edge_count_case(1, "a -> b [layer=x]", "a -> b"),),
    )
    visible_layer = """
        digraph {
          graph [concentrate=true layers="x:y" layerselect=y]
          a -> b [layer=x]
          a -> b [layer=y]
        }
    """
    assert len(_drawn_edges(visible_layer)) == 1


def test_compound_close_edge_preserves_ordinary_tail_arrow():
    """Short non-concentrated compound edges keep explicit dir=both arrows."""

    source = """
        digraph {
          graph [compound=true]
          subgraph cluster_a { a }
          subgraph cluster_b { b }
          a -> b [ltail=cluster_a lhead=cluster_b dir=both minlen=0]
        }
    """
    layout = json.loads(dot("json", source=source))
    assert "_tdraw_" in layout["edges"][0]
    assert "_hdraw_" in layout["edges"][0]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_setlinewidth_style_folds_into_penwidth(splines: str):
    """emit_begin_edge() renders style=setlinewidth(N) as penwidth=N."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(
                1,
                'a -> b [style="setlinewidth(2)"]',
                "a -> b [penwidth=2]",
            ),
            _edge_count_case(
                1,
                'b -> c [style="dashed,setlinewidth(2)"]',
                "b -> c [style=dashed penwidth=2]",
            ),
            _edge_count_case(
                2,
                'c -> d [style="setlinewidth(1)"]',
                'c -> d [style="setlinewidth(4)"]',
            ),
            _edge_count_case(
                2,
                'e -> f [style="setlinewidth(-1)"]',
                'e -> f [style="setlinewidth(0)"]',
            ),
            _edge_count_case(
                2,
                'g -> h [style="setlinewidth(1),setlinewidth(4)"]',
                'g -> h [style="setlinewidth(1)"]',
            ),
            _edge_count_case(
                1,
                'i -> j [style="setlinewidth(2)" penwidth=3]',
                "i -> j [penwidth=3]",
            ),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_setlinewidth_checks_this_edge_penwidth(splines: str):
    """style=setlinewidth(N) renders even when another edge declares penwidth."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(
                3,
                'a -> b [style="setlinewidth(4)"]',
                'a -> b [style="setlinewidth(2)"]',
                "c -> d [penwidth=1]",
            ),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_bold_style_folds_into_penwidth(splines: str):
    """gvrender_set_style() renders style=bold as PENWIDTH_BOLD."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, "a -> b [style=bold]", "a -> b [penwidth=2]"),
            _edge_count_case(2, "c -> d [style=bold]", "c -> d [penwidth=3]"),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_edge_style_identity_uses_final_pen_token(splines: str):
    """gvrender_set_style() applies the last pen-pattern style token."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, 'a -> b [style="dashed,solid"]', "a -> b"),
            _edge_count_case(1, 'b -> c [style="solid,dashed"]', 'b -> c [style=dashed]'),
            _edge_count_case(2, 'c -> d [style="dashed,solid"]', 'c -> d [style=dashed]'),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_edge_style_identity_keeps_invis_absorbing(splines: str):
    """emit_edge() skips an edge as soon as any style token is invis."""

    source = _concentrated_graph(
        splines,
        'a -> b [style="invis,solid"]',
        "a -> b",
    )
    assert len(_drawn_edges(source)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_edge_style_identity_treats_invisible_as_pen_token(
    splines: str,
):
    """The renderer treats `invis` as absorbing, but not `invisible`."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(
                2, 'a -> b [style="invisible,solid"]', "a -> b [style=solid]"
            ),
        ),
    )


def test_concentrate_plain_label_identity_uses_compiled_text():
    """Escaped and literal newlines render alike, but justification does not."""

    same_rendered_label = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", 'a -> b [headlabel="a\nb"]'
    ).replace("b -> a", 'b -> a [taillabel="a\\nb"]')
    assert len(_drawn_edges(same_rendered_label)) == 1

    different_justification = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", 'a -> b [headlabel="a\\lb"]'
    ).replace("b -> a", 'b -> a [taillabel="a\\nb"]')
    assert len(_drawn_edges(different_justification)) == 2


def test_concentrate_label_dedupe_keeps_distinct_rendered_label_links():
    """Equal label text/position cannot hide different label anchors."""

    source = _concentrated_graph(
        "",
        "a -> b [label=x labelURL=one]",
        "a -> b [label=x labelURL=two]",
    )
    edges = _drawn_edges(source)
    assert len(edges) == 2
    assert len([edge for edge in edges if edge.get("label") == "x"]) == 2


def test_concentrate_html_endpoint_label_identity_uses_substituted_text():
    """HTML endpoint labels emit edge substitutions, so identity must too."""

    same_direction_distinct = _concentrated_graph(
        "",
        'a -> b [headlabel=<<FONT COLOR="red">\\T</FONT>>]',
        'a -> b [headlabel=<<FONT COLOR="red">\\H</FONT>>]',
    )
    assert len(_drawn_edges(same_direction_distinct)) == 2

    opposite_direction_distinct = _concentrated_graph(
        "",
        'a -> b [headlabel=<<FONT COLOR="red">\\T</FONT>>]',
        'b -> a [taillabel=<<FONT COLOR="red">\\T</FONT>>]',
    )
    assert len(_drawn_edges(opposite_direction_distinct)) == 2


def test_concentrate_html_label_identity_uses_parsed_text_once():
    """make_html_label() already applies object substitutions inside HTML text."""

    tail = r"\H"
    double_substituted = _concentrated_graph(
        "",
        f'"{tail}" -> head [headlabel=<<FONT>\\T</FONT>>]',
        f'"{tail}" -> head [headlabel=<<FONT>head</FONT>>]',
    )
    assert len(_drawn_edges(double_substituted)) == 2


def test_concentrate_html_label_identity_uses_colorscheme():
    """Scheme-relative HTML-like label colors resolve at emit time."""

    _assert_concentrated_edge_counts(
        "splines=ortho",
        (
            _edge_count_case(
                2,
                'a -> b [colorscheme=accent3 label=<<FONT COLOR="1">x</FONT>>]',
                'a -> b [colorscheme=paired3 label=<<FONT COLOR="1">x</FONT>>]',
            ),
            _edge_count_case(
                2,
                'c -> d [colorscheme=accent3 label=<<font color="1">x</font>>]',
                'c -> d [colorscheme=paired3 label=<<font color="1">x</font>>]',
            ),
            _edge_count_case(
                2,
                "g -> h [colorscheme=accent3 label=<<FONT COLOR='1'>x</FONT>>]",
                "g -> h [colorscheme=paired3 label=<<FONT COLOR='1'>x</FONT>>]",
            ),
            _edge_count_case(
                1,
                'e -> f [colorscheme=accent3 label=<<FONT COLOR="red">x</FONT>>]',
                'e -> f [colorscheme=paired3 label=<<FONT COLOR="red">x</FONT>>]',
            ),
        ),
    )


def test_concentrate_html_font_color_identity_uses_rendered_color():
    """Parsed HTML font colors compare by rendered color, not spelling."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [headlabel=<<FONT COLOR="red">x</FONT>>]',
                'a -> b [headlabel=<<FONT COLOR="#ff0000">x</FONT>>]',
            ),
        ),
    )


def test_concentrate_html_table_bgcolor_identity_uses_rendered_color():
    """Parsed HTML cell backgrounds compare by rendered color."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'c -> d [headlabel=<<TABLE><TR><TD BGCOLOR="red">x</TD></TR></TABLE>>]',
                'c -> d [headlabel=<<TABLE><TR><TD BGCOLOR="#ff0000">x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_label_bgcolor_identity_uses_colorscheme():
    """Scheme-relative HTML-like label backgrounds resolve at emit time."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [colorscheme=accent3 label=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
                'a -> b [colorscheme=paired3 label=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'c -> d [colorscheme=accent3 headlabel=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
                'd -> c [colorscheme=paired3 taillabel=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_table_identity_uses_parsed_tree():
    """make_html_label() replaces table text, so identity uses the parse tree."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                "a -> b [label=<<TABLE><TR><TD>one</TD></TR></TABLE>>]",
                "a -> b [label=<<TABLE><TR><TD>two</TD></TR></TABLE>>]",
            ),
            _edge_count_case(
                2,
                "b -> c [label=<<TABLE><TR><TD>one</TD><TD>two</TD></TR></TABLE>>]",
                "b -> c [label=<<TABLE><TR><TD>one</TD></TR><TR><TD>two</TD></TR></TABLE>>]",
            ),
            _edge_count_case(
                2,
                "c -> d [label=<<TABLE><TR><TD ROWSPAN=\"2\">one</TD><TD>two</TD></TR><TR><TD>three</TD></TR></TABLE>>]",
                "c -> d [label=<<TABLE><TR><TD>one</TD><TD>two</TD></TR><TR><TD>three</TD></TR></TABLE>>]",
            ),
        ),
    )


def test_concentrate_html_table_identity_uses_layout_fields():
    """Table layout attributes change rendered HTML label geometry."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [label=<<TABLE CELLPADDING="2"><TR><TD>x</TD></TR></TABLE>>]',
                'a -> b [label=<<TABLE CELLPADDING="10"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'b -> c [label=<<TABLE CELLSPACING="2"><TR><TD>x</TD></TR></TABLE>>]',
                'b -> c [label=<<TABLE CELLSPACING="10"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'c -> d [label=<<TABLE WIDTH="20"><TR><TD>x</TD></TR></TABLE>>]',
                'c -> d [label=<<TABLE WIDTH="60"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'd -> e [label=<<TABLE FIXEDSIZE="false"><TR><TD>x</TD></TR></TABLE>>]',
                'd -> e [label=<<TABLE FIXEDSIZE="true" WIDTH="60" HEIGHT="40"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'e -> f [label=<<TABLE ALIGN="LEFT"><TR><TD>x</TD></TR></TABLE>>]',
                'e -> f [label=<<TABLE ALIGN="RIGHT"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'g -> h [headlabel=<<TABLE BGCOLOR="red:blue" GRADIENTANGLE="0"><TR><TD>x</TD></TR></TABLE>>]',
                'h -> g [taillabel=<<TABLE BGCOLOR="red:blue" GRADIENTANGLE="90"><TR><TD>x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_br_align_identity_uses_span_justification():
    """BR ALIGN changes multiline text placement."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [label=<left<BR ALIGN="LEFT"/>x>]',
                'a -> b [label=<left<BR ALIGN="RIGHT"/>x>]',
            ),
        ),
    )


def test_concentrate_html_table_border_identity_uses_pencolor():
    """HTML table borders inherit edge pencolor before color."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                "a -> b [pencolor=red label=<<TABLE><TR><TD>x</TD></TR></TABLE>>]",
                "a -> b [pencolor=blue label=<<TABLE><TR><TD>x</TD></TR></TABLE>>]",
            ),
            _edge_count_case(
                2,
                'c -> d [colorscheme=accent3 pencolor=1 headlabel=<<TABLE><TR><TD>x</TD></TR></TABLE>>]',
                'd -> c [colorscheme=paired3 pencolor=1 taillabel=<<TABLE><TR><TD>x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_table_identity_uses_rendered_border_and_gradient():
    """Borderless side flags and equivalent gradient colors do not render."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [headlabel=<<TABLE BORDER="0"><TR><TD SIDES="L">x</TD></TR></TABLE>>]',
                'a -> b [headlabel=<<TABLE BORDER="0"><TR><TD SIDES="R">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                1,
                'b -> c [headlabel=<<TABLE><TR><TD BGCOLOR="red:blue">x</TD></TR></TABLE>>]',
                'b -> c [headlabel=<<TABLE><TR><TD BGCOLOR="#ff0000:#0000ff">x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_img_identity_uses_effective_imagescale():
    """HTML IMG without SCALE uses the edge imagescale at emit time."""

    image = Path(__file__).parent / "../cmd/gvedit/images/save.png"
    assert image.exists(), "missing test data"
    source = _concentrated_graph(
        "",
        f'a -> b [imagescale=true label=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
        f'a -> b [imagescale=false label=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
    )
    assert len(_drawn_edges(source)) == 2


def test_concentrate_html_img_scale_identity_uses_rendered_mode():
    """HTML IMG scale spellings compare by rendered mode."""

    image = Path(__file__).parent / "../cmd/gvedit/images/save.png"
    assert image.exists(), "missing test data"
    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                f'a -> b [headlabel=<<TABLE><TR><TD><IMG SCALE="TRUE" SRC="{image}"/></TD></TR></TABLE>>]',
                f'a -> b [headlabel=<<TABLE><TR><TD><IMG SCALE="true" SRC="{image}"/></TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                1,
                f'c -> d [imagescale=TRUE headlabel=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
                f'c -> d [imagescale=true headlabel=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_self_contained_html_label_ignores_outer_font_slots():
    """Fully specified HTML text does not emit label-level font attributes."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [fontname=Courier fontsize=10 fontcolor=blue headlabel=<<FONT FACE="Helvetica" POINT-SIZE="12" COLOR="red">x</FONT>>]',
                'a -> b [fontname=Times fontsize=20 fontcolor=green headlabel=<<FONT FACE="Helvetica" POINT-SIZE="12" COLOR="red">x</FONT>>]',
            ),
        ),
    )


def test_concentrate_html_label_numeric_color_without_colorscheme_does_not_crash():
    """Missing colorscheme defaults must not dereference NULL in identity slots."""

    source = """
        digraph {
          graph [concentrate=true splines=ortho]
          a -> b [label=<<FONT COLOR="1">x</FONT>>]
          a -> b [label=<<FONT COLOR="1">x</FONT>>]
        }
    """
    assert len(_drawn_edges(source)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_singleton_sameport_groups_do_not_affect_identity(splines: str):
    """dot_sameports() moves samehead/sametail ports only with >1 members."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, "a -> b [samehead=x]", "a -> b"),
            _edge_count_case(1, "b -> c [sametail=x]", "b -> c"),
            _edge_count_case(1, "c -> d [samehead=x]", "d -> c [sametail=x]"),
        ),
    )


def test_tapered_multicolor_arrow_fillcolor_is_renderable():
    """emit_edge_graphics() can render a tapered color-list arrow fill."""

    tapered_multicolor = """
        digraph {
          a -> b [style=tapered color="red:blue" fillcolor=green]
        }
    """
    drawn_edges = _drawn_edges(tapered_multicolor)
    assert len(drawn_edges) == 1
    assert "_hdraw_" in drawn_edges[0]


def test_concentrate_arrow_fillcolor_identity_resolves_color_list_first_color():
    """Explicit arrow fillcolor color-lists render through the active colorscheme."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [colorscheme=accent3 fillcolor="1:2"]',
                'a -> b [colorscheme=paired3 fillcolor="1:2"]',
            ),
        ),
    )


def test_concentrate_retained_arrow_fillcolor_is_resolved_before_emit():
    """A retained arrow record must not emit a raw color-list fillcolor."""

    source = _concentrated_graph(
        "",
        'a -> b [fillcolor="red:blue"]',
        'a -> b [fillcolor="red:blue"]',
    )
    drawn_edges = _drawn_edges(source)
    assert len(drawn_edges) == 1
    assert _arrow_fill_color(drawn_edges[0], "h") == "#ff0000"


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_colors(splines: str):
    """Concentration preserves the colors and routes of visibly distinct edges."""

    distinct_parallel_colors = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    assert set(_drawn_edge_colors(distinct_parallel_colors)) == {
        "#ff0000",
        "#0000ff",
    }
    _assert_distinct_drawn_edge_routes(distinct_parallel_colors, 2)

    distinct_opposite_colors = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "b -> a [color=blue]",
    )
    assert set(_drawn_edge_colors(distinct_opposite_colors)) == {
        "#ff0000",
        "#0000ff",
    }
    _assert_distinct_drawn_edge_routes(distinct_opposite_colors, 2)


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


@pytest.mark.parametrize("edge_order", _SHARED_TRUNK_DISTINCT_ORDERS)
def test_concentrate_shared_trunk_keeps_distinct_colored_routes(
    edge_order: tuple[str, ...],
):
    """dot_concentrate() keeps each distinct tail-to-head route complete."""

    source = _shared_trunk_source(*edge_order)
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    assert len(edges) == 2
    assert {_drawn_edge_color(edge) for edge in edges} == {"#0000ff", "#ff0000"}
    assert all("_hdraw_" in edge for edge in edges)


def test_concentrate_shared_trunk_keeps_visible_lanes_for_distinct_edges():
    """Rendered-distinct members on a shared corridor separate visibly."""

    source = _shared_trunk_source(
        "a -> d [color=blue]",
        "b -> d [color=red]",
        "a -> d",
        "b -> d",
    )
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    colored = [
        edge
        for edge in edges
        if _drawn_edge_color(edge) in {"#0000ff", "#ff0000"}
    ]
    assert {_drawn_edge_color(edge) for edge in colored} == {
        "#0000ff",
        "#ff0000",
    }
    stroke_width = max(float(edge.get("penwidth", 1)) for edge in colored)
    assert _max_pointwise_route_distance(*colored) > stroke_width


def test_concentrate_shared_trunk_merges_equivalent_black_siblings():
    """Equivalent siblings can merge while colored siblings stay distinct."""

    source = _shared_trunk_source(
        "a -> d [color=blue]",
        "a -> d",
        "b -> d [color=red]",
        "b -> d",
    )
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    assert len(edges) == 4
    assert sorted(_drawn_edge_color(edge) for edge in edges) == [
        "#000000",
        "#000000",
        "#0000ff",
        "#ff0000",
    ]

    blue_red_edges = [
        edge for edge in edges if _drawn_edge_color(edge) in {"#0000ff", "#ff0000"}
    ]
    assert all("_hdraw_" in edge for edge in blue_red_edges)

    black_edges = [edge for edge in edges if _drawn_edge_color(edge) == "#000000"]
    assert sum("_hdraw_" in edge for edge in black_edges) == 1
    assert max(
        len([op for op in edge["_draw_"] if op["op"] == "b"]) for edge in black_edges
    ) == 2


def test_concentrate_shared_trunk_routes_meet_at_junction():
    """Concentrated route pieces meet at their shared virtual node."""

    source = _shared_trunk_source("a -> d", "b -> d")
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    short_edge = min(edges, key=_drawn_edge_spline_point_count)
    long_edge = max(edges, key=_drawn_edge_spline_point_count)
    short_bezier = next(
        operation for operation in short_edge["_draw_"] if operation["op"] == "b"
    )
    long_beziers = [
        operation for operation in long_edge["_draw_"] if operation["op"] == "b"
    ]

    assert len(long_beziers) == 2
    _assert_regular_g1_piece_join(long_beziers[0]["points"], long_beziers[1]["points"])
    assert math.dist(short_bezier["points"][-1], long_beziers[1]["points"][0]) <= 0.01


def test_concentrate_shared_trunk_repair_is_edge_order_deterministic():
    """Tagged-junction controls are independent of input edge iteration order."""

    first = _shared_trunk_source("a -> d", "b -> d")
    reversed_order = _shared_trunk_source("b -> d", "a -> d")
    assert _named_drawn_cubic_geometry(first, {"a", "b"}, "d") == (
        _named_drawn_cubic_geometry(reversed_order, {"a", "b"}, "d")
    )


def test_concentrate_trunk_alignment_preserves_better_piece_g1():
    """A topology-neutral trunk adjustment cannot worsen an existing G1 seam."""

    source = _concentrated_graph(
        "",
        'Diskless -> Inconsistent [label="ioctl_set_disk()"]',
        'Diskless -> Consistent [label="ioctl_set_disk()"]',
        'Diskless -> Outdated [label="ioctl_set_disk()"]',
        'Consistent -> Outdated [label="receive_param()"]',
        'Consistent -> UpToDate [label="receive_param()"]',
        'Consistent -> Inconsistent [label="start resync"]',
        'Outdated -> Inconsistent [label="start resync"]',
        'UpToDate -> Inconsistent [label="ioctl_replicate"]',
        'Inconsistent -> UpToDate [label="resync completed"]',
        'Consistent -> Failed [label="io completion error"]',
        'Outdated -> Failed [label="io completion error"]',
        'UpToDate -> Failed [label="io completion error"]',
        'Inconsistent -> Failed [label="io completion error"]',
        'Failed -> Diskless [label="sending notify to peer"]',
    )
    edge = _drawn_edges_between(source, {"Outdated"}, "Failed")[0]
    pieces = [
        operation["points"] for operation in edge["_draw_"] if operation["op"] == "b"
    ]
    assert len(pieces) == 2
    _assert_regular_g1_piece_join(pieces[0], pieces[1])


def test_concentrate_shared_trunk_still_merges_without_colored_siblings():
    """dot_concentrate() retains the ordinary asymmetric shared-trunk route."""

    source = _shared_trunk_source("a -> d", "b -> d")
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    counts = sorted(_drawn_edge_spline_point_count(edge) for edge in edges)
    assert counts[0] == 4
    assert counts[1] >= 8
    assert sum("_hdraw_" in edge for edge in edges) == 1


def test_concentrate_shared_trunk_reorders_past_foreign_identity():
    """A foreign candidate no longer blocks later equivalent siblings."""

    source = _shared_trunk_source(
        "a -> d",
        "a -> d [color=blue]",
        "b -> d",
    )
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    assert len(edges) == 3
    assert sorted(_drawn_edge_color(edge) for edge in edges) == [
        "#000000",
        "#000000",
        "#0000ff",
    ]

    black_edges = [edge for edge in edges if _drawn_edge_color(edge) == "#000000"]
    assert sum("_hdraw_" in edge for edge in black_edges) == 1


def test_concentrate_same_tail_fanout_routes_share_initial_trunk():
    """Same-endpoint fan-out gathers before splitting to distinct heads."""

    source = _graph_with_concentrate(
        True,
        "a -> b [minlen=2]",
        "a -> c [minlen=2]",
    )
    edges = _drawn_edges(source)
    assert len(edges) == 2
    first_segments = [
        operation["points"]
        for operation in edges[0]["_draw_"]
        if operation["op"] == "b"
    ]
    second_segments = [
        operation["points"]
        for operation in edges[1]["_draw_"]
        if operation["op"] == "b"
    ]
    assert len(first_segments) == 2
    assert len(second_segments) == 1
    _assert_regular_g1_piece_join(first_segments[0], first_segments[1])
    assert math.dist(first_segments[0][-1], second_segments[0][0]) <= 1.01


def test_concentrate_short_bidirectional_flat_edge_bows_to_three_arrows():
    """A short folded same-rank reverse edge has a visible middle shaft."""

    source = """
        strict digraph {
          graph [concentrate=true]
          { rank=same; a; b }
          a -> b
          b -> a
        }
    """
    drawn_edges = _drawn_edges(source)
    assert len(drawn_edges) == 1
    edge = drawn_edges[0]
    assert "_hdraw_" in edge
    assert "_tdraw_" in edge

    assert _drawn_edge_arc_length(edge) >= 30


def test_concentrate_multiedge_arrowheads_follow_shaft_tangents():
    """Arrow axes follow the terminal control arms of concentrated multiedges."""

    drawn_edges = _drawn_edges(_BACKWARD_PARALLEL_COLORS_FIXTURE)
    assert len(drawn_edges) == 2
    assert max(_arrowhead_shaft_angle(edge) for edge in drawn_edges) <= 0.1


def test_concentrate_single_route_arrowhead_follows_smoothed_tangent():
    """Post-route smoothing cannot leave a single route's arrow axis stale."""

    source = """
        digraph {
          graph [concentrate=true]
          subgraph cluster_a {
            n49 -> n53 [label=int]
            n55
          }
          subgraph cluster_b {
            n50 -> n61
            n49 -> n61
          }
          n55 -> n57 [label=exe, dir=back]
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "n55", "n57")

    assert _arrowhead_shaft_angle(edge, "_tdraw_") <= 2


def test_concentrate_smoothing_preserves_terminal_departure():
    """Interior smoothing does not turn a clipped terminal arm inward."""

    source = """
        digraph {
          graph [concentrate=true]
          node [shape=doublecircle]
          running
          lost
          node [shape=circle]
          { rank=min; running_rta [label="running;\\nreconnect\\ntimer\\nactive"] }
          running [label="running;\\nreconnect\\ntimer\\nstopped"]
          blocked
          failfast [label="fail I/O\\nfast"]
          running -> running_rta [label="fast_io_fail_tmo = off and\\ndev_loss_tmo = off;\\nsrp_start_tl_fail_timers()"]
          running_rta -> running [label="fast_io_fail_tmo = off and\\ndev_loss_tmo = off;\\nreconnecting succeeded"]
          running -> blocked [label="fast_io_fail_tmo >= 0 or\\ndev_loss_tmo >= 0;\\nsrp_start_tl_fail_timers()"]
          blocked -> failfast [label="fast_io_fail_tmo\\nexpired or\\nreconnecting\\nfailed"]
          blocked -> lost [label="dev_loss_tmo\\nexpired or\\nsrp_stop_rport_timers()"]
          failfast -> lost [label="dev_loss_tmo\\nexpired or\\nsrp_stop_rport_timers()"]
          failfast -> failfast [label="reconnecting\\nfailed"]
          running -> lost [label="srp_stop_rport_timers()"]
          running_rta -> lost [label="srp_stop_rport_timers()"]
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "running_rta", "lost")

    _assert_endpoint_departure(layout, edge, "tail")


def test_concentrate_multiedge_routes_clear_non_endpoint_nodes():
    """Separated route strokes remain outside every non-endpoint node."""

    layout = json.loads(dot("json", source=_BACKWARD_PARALLEL_COLORS_FIXTURE))
    nodes = {node["_gvid"]: node for node in layout["objects"]}
    for edge in (edge for edge in layout["edges"] if "_draw_" in edge):
        endpoints = {edge["tail"], edge["head"]}
        penwidth = float(edge.get("penwidth", 1))
        sampled_points = (
            point
            for operation in edge["_draw_"]
            if operation["op"] == "b"
            for point in _sample_bezier_points(operation["points"])
        )
        points = tuple(sampled_points)
        for node_id, node in nodes.items():
            if node_id in endpoints:
                continue
            ellipse = next(
                operation["rect"]
                for operation in node["_draw_"]
                if operation["op"] == "e"
            )
            center_x, center_y, radius_x, radius_y = ellipse
            assert radius_x == pytest.approx(radius_y)
            clearance = min(
                math.hypot(x - center_x, y - center_y) - radius_x - penwidth / 2
                for x, y in points
            )
            assert clearance >= 0.5, (edge["color"], node["name"], clearance)


def test_concentrate_same_rank_opposite_vertical_ports_use_clear_ortho_route():
    """The same-rank vertical-port detour must not cut through neighbor nodes."""

    source = """
        strict digraph {
          graph [concentrate=true, splines=ortho, nodesep=0.05]
          node [shape=circle, width=0.45, fixedsize=true]
          { rank=same; x; a; b }
          a:s -> b:n [penwidth=3]
          b:n -> a:s [penwidth=3]
        }
    """
    layout = json.loads(dot("json", source=source))
    neighbor = next(node for node in layout["objects"] if node["name"] == "x")
    ellipse = next(
        operation["rect"] for operation in neighbor["_draw_"] if operation["op"] == "e"
    )
    center_x, center_y, radius_x, radius_y = ellipse
    assert radius_x == pytest.approx(radius_y)
    edge = next(edge for edge in layout["edges"] if "_draw_" in edge)
    penwidth = float(edge.get("penwidth", 1))
    points = tuple(
        point
        for operation in edge["_draw_"]
        if operation["op"] == "b"
        for point in _sample_bezier_points(operation["points"])
    )
    clearance = min(
        math.hypot(x - center_x, y - center_y) - radius_x - penwidth / 2
        for x, y in points
    )
    assert clearance >= 0.5


def test_unconcentrated_single_edge_arrowhead_angle_is_unchanged():
    """Single-edge routing retains its straight arrowhead alignment."""

    edge = _drawn_edges("digraph { a -> b }")[0]
    assert _arrowhead_shaft_angle(edge) == pytest.approx(0.0, abs=0.01)


def test_vee_arrowhead_polygon_does_not_cross_itself_at_wide_penwidth():
    """Penwidth compensation must keep the two vee prongs disjoint."""

    edge = _drawn_edges(
        "digraph { edge [penwidth=3, arrowhead=vee]; a -> b }"
    )[0]
    polygon = next(
        operation["points"]
        for operation in edge["_hdraw_"]
        if operation["op"] == "P"
    )
    assert len(polygon) == 8
    assert _polygon_self_intersections(polygon) == []


def test_concentrate_edge_helpers_are_exported_to_windows_plugins():
    """edge_chains.c, conc.c, and ortho.c can import edge identity helpers."""

    header = (Path(__file__).parent.parent / "lib/common/edgeattr.h").read_text(
        encoding="utf-8"
    )
    assert "#define EDGEATTR_API __declspec(dllexport)" in header
    assert "#define EDGEATTR_API __declspec(dllimport)" in header
    for helper in (
        "gv_edge_attributes_are_equal",
        "gv_opposite_edge_attributes_are_equal",
        "gv_edge_ports_are_equal",
        "gv_opposite_edge_ports_are_equal",
    ):
        assert re.search(rf"EDGEATTR_API bool {helper}\s*\(", header)


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_styles(splines: str):
    """Concentration preserves visibly distinct line styles."""

    distinct_styles = _concentrated_graph(
        splines,
        "a -> b [style=dashed]",
        "a -> b [style=dotted]",
    )
    assert set(_drawn_edge_styles(distinct_styles)) == {"dashed", "dotted"}


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_labels(splines: str):
    """Concentration preserves visibly distinct edge labels."""

    distinct_labels = _concentrated_graph(
        splines,
        "a -> b [label=first]",
        "a -> b [label=second]",
    )
    label_edges = json.loads(dot("json", source=distinct_labels))["edges"]
    assert {edge["label"] for edge in label_edges} == {"first", "second"}


def test_concentrate_curved_route_reuse_merges_identical_xlabels_once():
    """EDGETYPE_CURVED route reuse keeps one visible label for duplicates."""

    identical_xlabels = _concentrated_graph(
        "splines=curved",
        "a -> b [minlen=3 xlabel=same]",
        "a -> b [minlen=3 xlabel=same]",
    )
    assert len(_drawn_edges(identical_xlabels)) == 1
    assert _edge_label_texts(identical_xlabels) == ["same"]


def test_concentrate_curved_route_reuse_preserves_distinct_xlabels():
    """EDGETYPE_CURVED route reuse splits visibly distinct labels."""

    distinct_xlabels = _concentrated_graph(
        "splines=curved",
        "a -> b [minlen=3 xlabel=first]",
        "a -> b [minlen=3 xlabel=second]",
    )
    assert len(_drawn_edges(distinct_xlabels)) == 2
    assert sorted(_edge_label_texts(distinct_xlabels)) == ["first", "second"]


def test_concentrate_ortho_suppression_merges_identical_labels_once():
    """Ortho suppression keeps one route while preserving duplicate labels."""

    identical_labels = _concentrated_graph(
        "splines=ortho",
        "a -> b [label=same]",
        "a -> b [label=same]",
    )
    assert len(_drawn_edges(identical_labels)) == 1
    assert _edge_label_texts(identical_labels) == ["same", "same"]


def test_concentrate_ortho_suppression_preserves_distinct_labels():
    """Ortho suppression splits and draws visibly distinct labels."""

    distinct_labels = _concentrated_graph(
        "splines=ortho",
        "a -> b [label=first]",
        "a -> b [label=second]",
    )
    assert len(_drawn_edges(distinct_labels)) == 2
    assert sorted(_edge_label_texts(distinct_labels)) == ["first", "second"]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_directions(splines: str):
    """Concentration preserves visibly distinct arrow directions."""

    distinct_directions = _concentrated_graph(
        splines,
        "a -> b [dir=forward]",
        "a -> b [dir=both]",
    )
    direction_edges = json.loads(dot("json", source=distinct_directions))["edges"]
    assert {
        (bool(edge.get("_tdraw_")), bool(edge.get("_hdraw_")))
        for edge in direction_edges
    } == {(False, True), (True, True)}


def test_concentrate_svg_preserves_distinct_edge_class_hooks():
    """svg_print_id_class() keeps each rendered edge CSS/DOM class hook."""

    def edge_classes(*attributes: str) -> list[str]:
        source = _concentrated_graph(
            "",
            *(
                f"a -> b [{attribute}]" if attribute else "a -> b"
                for attribute in attributes
            ),
        )
        root = ET.fromstring(dot("svg", source=source))
        return [
            element.attrib["class"]
            for element in root.iter()
            if element.tag.endswith("g")
            and "edge" in element.attrib.get("class", "").split()
        ]

    assert set(edge_classes("class=first", "class=second")) == {
        "edge first",
        "edge second",
    }
    assert edge_classes("class=shared", "class=shared") == ["edge shared"]
    assert edge_classes("", "") == ["edge"]


def test_concentrate_preserves_explicit_endpoint_tooltips_without_labels():
    """nodeIntersect() maps explicit endpoint tooltips without label geometry."""

    distinct_head_tooltips = _concentrated_graph(
        "",
        "a -> b [headtooltip=one]",
        "a -> b [headtooltip=two]",
    )
    assert len(_drawn_edges(distinct_head_tooltips)) == 2

    equal_head_tooltips = _concentrated_graph(
        "",
        "a -> b [headtooltip=same]",
        "a -> b [headtooltip=same]",
    )
    assert len(_drawn_edges(equal_head_tooltips)) == 1

    # emit_edge_label() still needs a main label before labeltooltip renders.
    unrendered_label_tooltips = _concentrated_graph(
        "",
        "a -> b [labeltooltip=one]",
        "a -> b [labeltooltip=two]",
    )
    assert len(_drawn_edges(unrendered_label_tooltips)) == 1

    rendered_label_tooltips = _concentrated_graph(
        "",
        "a -> b [label=x labeltooltip=one]",
        "a -> b [label=x labeltooltip=two]",
    )
    assert len(_drawn_edges(rendered_label_tooltips)) == 2


def test_concentrate_preserves_taper_direction_without_arrowheads():
    """taperfun() uses dir even when both arrow decorations are absent."""

    distinct_tapers = _concentrated_graph(
        "",
        "a -> b [style=tapered dir=none]",
        "a -> b [style=tapered dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(distinct_tapers)) == 2

    equal_tapers = _concentrated_graph(
        "",
        "a -> b [style=tapered dir=forward arrowhead=none]",
        "a -> b [style=tapered dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(equal_tapers)) == 1

    default_forward_tapers = _concentrated_graph(
        "",
        "a -> b [style=tapered arrowhead=none]",
        "a -> b [style=tapered dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(default_forward_tapers)) == 1

    arrowless_lines = _concentrated_graph(
        "",
        "a -> b [dir=none]",
        "a -> b [dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(arrowless_lines)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_merges_equivalent_parallel_edges(splines: str):
    """Equivalent edges share one route even when input order separates them."""

    adjacent_equivalent_edges = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=red]",
    )
    assert _drawn_edge_colors(adjacent_equivalent_edges) == ["#ff0000"]

    interleaved_equivalent_edges = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
    )
    assert sorted(_drawn_edge_colors(interleaved_equivalent_edges)) == [
        "#0000ff",
        "#ff0000",
    ]

    # The invisible path forces a->b to run against rank order. This exercises
    # the backward-edge classifier, which must apply the same equivalence rule.
    backward_interleaved_equivalent_edges = _concentrated_graph(
        splines,
        "b -> c [style=invis]",
        "c -> a [style=invis]",
        "a -> b [constraint=false color=red]",
        "a -> b [constraint=false color=blue]",
        "a -> b [constraint=false color=red]",
    )
    assert sorted(_drawn_edge_colors(backward_interleaved_equivalent_edges)) == [
        "#0000ff",
        "#ff0000",
    ]

    explicit_default_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=normal]",
        "a -> b",
        "a -> b [arrowtail=vee]",
    )
    assert len(_drawn_edges(explicit_default_arrows)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_equivalent_color_spellings(splines: str):
    """Color values compare by rendered color when both parse cleanly."""

    equivalent_color_spellings = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        'a -> b [color="#ff0000"]',
    )
    assert _drawn_edge_colors(equivalent_color_spellings) == ["#ff0000"]

    fillcolor_defaults_to_color = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=red fillcolor=red]",
    )
    assert _drawn_edge_colors(fillcolor_defaults_to_color) == ["#ff0000"]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_non_segmented_color_lists_preserve_empty_lanes(splines: str):
    """Empty color-list lanes shift parallel strokes, so they stay distinct.

    emit_edge_graphics() counts raw colons into numc and offsets the
    parallel strokes, so color="red:" renders shifted relative to
    color=red; the identity must keep them apart.  ":red" and "red:"
    share the same lane count and may merge with each other."""

    empty_lane_spellings = _concentrated_graph(
        splines,
        'a -> b [dir=none color=":red"]',
        'a -> b [dir=none color="red:"]',
        "a -> b [dir=none color=red]",
    )
    assert _drawn_edge_colors(empty_lane_spellings) == ["#ff0000", "#ff0000"]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_arrow_fillcolor_only_gates_filled_shapes(splines: str):
    """arrow_gen() consumes fillcolor only for shapes that render filled marks."""

    unfilled_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=onormal fillcolor=red]",
        "a -> b [arrowhead=onormal fillcolor=blue]",
        "b -> c [arrowhead=curve fillcolor=red]",
        "b -> c [arrowhead=curve fillcolor=blue]",
    )
    assert len(_drawn_edges(unfilled_arrows)) == 2

    filled_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=normal fillcolor=red]",
        "a -> b [arrowhead=normal fillcolor=blue]",
        "b -> c [arrowhead=tee fillcolor=red]",
        "b -> c [arrowhead=tee fillcolor=blue]",
    )
    assert len(_drawn_edges(filled_arrows)) == 4


@pytest.mark.parametrize(
    ("attribute", "first_value", "second_value"),
    (
        ("labelangle", "10", "20"),
        ("labeldistance", "1", "2"),
        ("labelfontcolor", "red", "blue"),
        ("labelfontname", "Helvetica", "Courier"),
        ("labelfontsize", "10", "20"),
    ),
)
def test_concentrate_gates_endpoint_label_attributes(
    attribute: str, first_value: str, second_value: str
):
    """place_portlabel()/initFontLabelEdgeAttr() consume endpoint-label rows."""

    main_label_only = _concentrated_graph(
        "splines=ortho",
        f"a -> b [xlabel=x {attribute}={first_value}]",
        f"a -> b [xlabel=x {attribute}={second_value}]",
    )
    assert len(_drawn_edges(main_label_only)) == 1

    same_endpoint_attribute = _concentrated_graph(
        "splines=ortho",
        f"a -> b [headlabel=x {attribute}={first_value}]",
        f"a -> b [headlabel=x {attribute}={first_value}]",
    )
    assert len(_drawn_edges(same_endpoint_attribute)) == 1

    endpoint_label = _concentrated_graph(
        "splines=ortho",
        f"a -> b [headlabel=x {attribute}={first_value}]",
        f"a -> b [headlabel=x {attribute}={second_value}]",
    )
    assert len(_drawn_edges(endpoint_label)) == 2


@pytest.mark.parametrize(
    ("attribute", "default_value"),
    (("labeldistance", "1"),),
)
def test_concentrate_preserves_endpoint_label_placement_attribute_presence(
    attribute: str, default_value: str
):
    """place_portlabel() uses late_double() defaults for explicit placement."""

    placement_trigger = _concentrated_graph(
        "splines=ortho",
        "a -> b [headlabel=x]",
        f"a -> b [headlabel=x {attribute}={default_value}]",
    )
    assert len(_drawn_edges(placement_trigger)) == 1

    repeated_explicit_default = _concentrated_graph(
        "splines=ortho",
        f"a -> b [headlabel=x {attribute}={default_value}]",
        f"a -> b [headlabel=x {attribute}={default_value}]",
    )
    assert len(_drawn_edges(repeated_explicit_default)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_resolved_port_spellings(splines: str):
    """Raw headport/tailport spelling does not override resolved ports."""

    equivalent_port_spellings = _concentrated_graph(
        splines,
        "node [shape=record]",
        'a [label="<p>p"]',
        "a:p:c -> b [color=red]",
        'a -> b [tailport="p:c" color=red]',
    )
    assert len(_drawn_edges(equivalent_port_spellings)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_rank_parallel_edges_find_prior_equivalent(splines: str):
    """Same-rank duplicates still concentrate when separated by distinct edges."""

    separated_flat_duplicates = _concentrated_graph(
        splines,
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    assert sorted(_drawn_edge_colors(separated_flat_duplicates)) == [
        "#0000ff",
        "#ff0000",
    ]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_backward_edges_find_prior_equivalent(splines: str):
    """Backward edges still suppress earlier same-direction duplicates."""

    separated_backward_duplicates = _concentrated_graph(
        splines,
        "b",
        "a -> b [color=blue]",
        "b -> a [constraint=false color=red]",
        "b -> a [constraint=false color=red]",
    )
    assert sorted(_drawn_edge_colors(separated_backward_duplicates)) == [
        "#0000ff",
        "#ff0000",
    ]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_edge_arrow_shapes(splines: str):
    """Each retained edge borrows arrows from its exact reverse mate."""

    opposite_equivalent_pairs = _concentrated_graph(
        splines,
        "a -> b [color=red arrowhead=normal]",
        "a -> b [color=blue arrowhead=vee]",
        "b -> a [color=red arrowhead=normal]",
        "b -> a [color=blue arrowhead=vee]",
    )
    drawn_edges_by_color = _drawn_edges_by_color(opposite_equivalent_pairs)
    # JSON names the head and tail arrow streams `_hdraw_` and `_tdraw_`.
    head_endpoint = "h"
    tail_endpoint = "t"
    actual_arrow_polygon_points = {
        color: (
            _arrow_polygon_point_count(edge, head_endpoint),
            _arrow_polygon_point_count(edge, tail_endpoint),
        )
        for color, edge in drawn_edges_by_color.items()
    }
    expected_arrow_polygon_points = {"#ff0000": (3, 3), "#0000ff": (8, 8)}
    assert actual_arrow_polygon_points == expected_arrow_polygon_points


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_reverse_arrow_after_no_arrow_duplicate(splines: str):
    """A later reverse duplicate without arrows does not erase saved arrows."""

    reverse_arrow_then_no_arrow = _concentrated_graph(
        splines,
        "a -> b [dir=none]",
        "b -> a [arrowhead=normal]",
        "b -> a [dir=none]",
    )
    drawn_edges = _drawn_edges(reverse_arrow_then_no_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert "_hdraw_" not in drawn_edges[0]
    assert _arrow_polygon_point_count(drawn_edges[0], "t") == 3


def test_neato_concentrate_straight_opposite_edge_keeps_borrowed_arrow():
    """Straight neato folds compare opposite physical endpoints."""

    source = """
        digraph {
          graph [concentrate=true splines=false]
          node [shape=circle]
          a [pos="0,0!"]
          b [pos="1,0!"]
          a -> b [dir=none color=red]
          b -> a [arrowhead=normal color=red]
        }
    """
    layout = json.loads(run("dot", "-Kneato", "-Tjson", input=source))
    positioned_edges = [edge for edge in layout["edges"] if edge.get("pos")]
    assert len(positioned_edges) == 1
    assert "_tdraw_" in positioned_edges[0]
    assert "_hdraw_" not in positioned_edges[0]


@pytest.mark.parametrize(
    ("retained_attributes", "candidate_attributes"),
    (
        ("", "fillcolor=1"),
        ('color="#a6cee3"', "color=1"),
    ),
)
def test_concentrate_borrowed_arrow_uses_candidate_colorscheme(
    retained_attributes: str, candidate_attributes: str
):
    """emit_edge_graphics() receives the candidate-resolved borrowed fill."""

    borrowed_scheme_relative_arrow = _concentrated_graph(
        "",
        f"a -> b [dir=none colorscheme=accent3 {retained_attributes}]",
        f"b -> a [arrowhead=normal colorscheme=paired3 {candidate_attributes}]",
    )
    drawn_edges = _drawn_edges(borrowed_scheme_relative_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "t") == "#a6cee3"


def test_concentrate_borrowed_reverse_arrow_uses_endpoint_segment_color():
    """A borrowed color-list arrow keeps the candidate endpoint's segment color."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        'a -> b [dir=none color="red:blue"]',
        'b -> a [dir=forward color="blue:red"]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "t") == "#0000ff"


def test_concentrate_color_list_arrow_endpoint_overrides_explicit_fillcolor():
    """Non-tapered color-list arrows use their endpoint segment color."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        'a -> b [dir=none color="red:blue" fillcolor=green]',
        'b -> a [dir=forward color="blue:red" fillcolor=green]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "t") == "#0000ff"


def test_concentrate_color_list_arrow_endpoint_strips_segment_fractions():
    """Borrowed endpoint arrow colors resolve normalized color-list segments."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        'a -> b [dir=none color="red;0.5:blue;0.5"]',
        'b -> a [dir=back color="blue;0.5:red;0.5"]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_hdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "h") == "#ff0000"


def test_concentrate_color_list_arrow_endpoint_uses_normalized_segments():
    """Endpoint arrow colors follow the positive segments multicolor() draws."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        "a -> b [dir=none color=red]",
        'b -> a [dir=back color="red;1:blue"]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_hdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "h") == "#ff0000"


def test_endpoint_label_default_position_uses_clearance_anchor():
    """Endpoint labels default from the endpoint anchor, not their lower-left box."""

    layout = json.loads(
        dot(
            "json",
            source='digraph { a -> b [headlabel="x"] }',
        )
    )
    edge = layout["edges"][0]
    node_by_id = {node["_gvid"]: node for node in layout["objects"]}
    node_center = tuple(
        float(coordinate) for coordinate in node_by_id[edge["head"]]["pos"].split(",")
    )
    endpoint = _edge_physical_endpoint(edge, "head")
    label_point = next(
        tuple(operation["pt"])
        for operation in edge["_hldraw_"]
        if operation["op"] == "T"
    )

    assert math.dist(label_point, endpoint) <= math.dist(node_center, endpoint)


def test_arrowless_endpoint_labels_follow_spline_direction():
    """Labels without arrow clip points still seed from nearby spline geometry."""

    for label_attribute, draw_stream, endpoint_name, comparison in (
        ("taillabel", "_tldraw_", "tail", operator.lt),
        ("headlabel", "_hldraw_", "head", operator.gt),
    ):
        layout = json.loads(
            dot(
                "json",
                source=f'digraph {{ a -> b [dir=none {label_attribute}="x"] }}',
            )
        )
        edge = layout["edges"][0]
        endpoint = _edge_physical_endpoint(edge, endpoint_name)
        label_point = next(
            tuple(operation["pt"])
            for operation in edge[draw_stream]
            if operation["op"] == "T"
        )
        assert comparison(label_point[1], endpoint[1])


def test_concentrate_endpoint_labels_keep_distinct_flat_routes():
    """Endpoint labels on distinct flat concentrated lanes stay attached."""

    source = """
        digraph {
          graph [concentrate=true nodesep=0.2 rankdir=LR ranksep=0.2]
          node [fontsize=10 height=0 width=0]
          edge [arrowsize=0.9 dir=none fontsize=8 labelangle=-30
                labeldistance=0.8 labelfontsize=8]

          n003 -> n002 [arrowhead=dot headlabel=":s:"]
          n003 -> n002 [arrowtail=inv samearrowhead=1 samehead=m000]
          n005 -> n002 [arrowhead=dot arrowtail=inv headlabel=":u:"
                        samearrowhead=1 samehead=m000]
        }
    """
    layout = json.loads(dot("json", source=source))

    assert _endpoint_label_detach_honda_score(layout) == (0, 0)


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


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_implicit_tooltip_fallback_uses_textlabel_text(tmp_path: Path):
    """emit_begin_edge() falls back from tooltip to parsed obj->label text."""

    exe, env = _compile_concentrate_edge_identity_tooltip_test(tmp_path)
    subprocess.run(
        (exe, "parsed-label-fallback"), capture_output=True, env=env, check=True
    )


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_xlabel_does_not_supply_edge_tooltip_fallback(tmp_path: Path):
    """emit_begin_edge() never assigns obj->label from ED_xlabel()."""

    exe, env = _compile_concentrate_edge_identity_tooltip_test(tmp_path)
    subprocess.run((exe, "xlabel-no-fallback"), capture_output=True, env=env, check=True)


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_ortho_duplicate_edges_are_ignored(tmp_path: Path):
    """ortho concentration marks suppressed duplicates before repeated layout."""

    exe, env = _compile_concentrate_edge_identity_tooltip_test(tmp_path)
    subprocess.run(
        (exe, "ortho-duplicate-ignored"), capture_output=True, env=env, check=True
    )


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_repeated_layout_discards_accumulated_arrows(tmp_path: Path):
    """``gv_cleanup_edge`` drops the private arrow fold before a second layout."""

    exe, env = _compile_concentrate_c_test(
        tmp_path, "concentrate_repeat_layout.c", "concentrate-repeat-layout"
    )
    result = subprocess.run(exe, capture_output=True, env=env, check=True, text=True)
    rendered = json.loads(result.stdout)
    assert len([edge for edge in rendered["edges"] if "_draw_" in edge]) == 1


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_sums_retained_virtual_segment_weight(tmp_path: Path):
    """mergevirtual_pair() adds deleted segment weight to an existing carrier."""

    build_root = which("dot").resolve().parents[2]
    if not (build_root / "config.h").exists():
        pytest.skip("private dotgen headers require a configured build tree")

    exe, env = _compile_concentrate_c_test(
        tmp_path,
        "concentrate_segment_weight.c",
        "concentrate-segment-weight",
        extra_includes=(build_root, Path(__file__).parent.parent / "lib" / "dotgen"),
    )
    subprocess.run((exe,), capture_output=True, env=env, check=True)


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_direction_arrows_merge_rendered_identical_duplicates(
    splines: str,
):
    """Same-direction candidates compare against accumulated rendered arrows."""

    rendered_identical_duplicate = _concentrated_graph(
        splines,
        "a -> b [color=red arrowhead=normal]",
        "b -> a [color=red arrowhead=vee]",
        "a -> b [color=red dir=both arrowhead=normal arrowtail=vee]",
    )
    assert len(_drawn_edges(rendered_identical_duplicate)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_direction_arrows_keep_arrowless_candidate_distinct(
    splines: str,
):
    """Borrowed reverse arrows still protect an arrow-less later candidate."""

    arrowless_candidate = _concentrated_graph(
        splines,
        "a -> b [color=red arrowhead=normal]",
        "b -> a [color=red arrowhead=vee]",
        "a -> b [color=red dir=none]",
    )
    assert len(_drawn_edges(arrowless_candidate)) == 2


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_unconcentrated_parallel_edges_keep_separate_routes(splines: str):
    """The attribute guard does not alter ordinary multi-edge routing."""

    parallel_edges_without_concentration = f"""
        digraph {{
          graph [concentrate=false {splines}]
          a -> b [color=red]
          a -> b [color=blue]
        }}
    """
    parallel_edges = _drawn_edges(parallel_edges_without_concentration)
    assert len({edge["pos"] for edge in parallel_edges}) == 2


def test_unconcentrated_opposite_edges_share_multi_edge_routing():
    """Opposite edges remain distinct, non-overlapping members of one group."""

    opposite_edges_without_concentration = """
        digraph {
          graph [concentrate=false]
          a -> b [color=red]
          b -> a [color=blue]
        }
    """
    # A missed merge_chain() call routes both edges down the same centerline.
    # Comparing their Bezier x coordinates catches that overlap without tying
    # the test to exact node positions.
    _assert_distinct_drawn_edge_routes(opposite_edges_without_concentration, 2)


def test_unconcentrated_opposite_port_edges_share_multi_edge_routing():
    """Reverse edges compare ports at physical endpoints (GitLab #1039)."""

    opposite_edges_with_a_shared_port = """
        digraph {
          node [shape=box]
          bonn:s -> berlin
          berlin -> bonn:s
        }
    """
    # Sharing one virtual chain lets the regular multi-edge router separate the
    # two visible splines. Two independent chains collapse onto the centerline.
    _assert_distinct_drawn_edge_routes(opposite_edges_with_a_shared_port, 2)

    nonmatching_physical_ports = _concentrated_graph(
        "",
        "node [shape=box]",
        "bonn:n -> berlin",
        "berlin -> bonn:s",
    )
    assert len(_drawn_edges(nonmatching_physical_ports)) == 2


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_ports_by_physical_endpoint(splines: str):
    """Reverse ports exchange endpoint roles before matching (GitLab #1039)."""

    equivalent_reverse_ports = _concentrated_graph(
        splines,
        "node [shape=box]",
        "bonn:s -> berlin",
        "berlin -> bonn:s",
    )
    assert len(_drawn_edges(equivalent_reverse_ports)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_clipping_by_physical_endpoint(splines: str):
    """Reverse clipping exchanges head/tail roles at endpoints (GitLab #448)."""

    same_grammar_role = _concentrated_graph(
        splines,
        "a -> b [tailclip=true, headclip=false]",
        "b -> a [tailclip=true, headclip=false]",
    )
    assert len(_drawn_edges(same_grammar_role)) == 2

    same_physical_endpoint = _concentrated_graph(
        splines,
        "a -> b [tailclip=true, headclip=false]",
        "b -> a [tailclip=false, headclip=true]",
    )
    assert len(_drawn_edges(same_physical_endpoint)) == 1


def test_concentrate_does_not_materialize_unmatched_opposite_chains():
    """An unmatched backward edge does not pre-classify later parallel edges."""

    separated_equivalent_edges = _concentrated_graph(
        "",
        "// Creating b first makes this backward edge run before a's outgoing",
        "// edges. constraint=false leaves a -> b to determine their ranks.",
        "b -> a [color=green constraint=false]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
    )
    assert sorted(_drawn_edge_colors(separated_equivalent_edges)) == [
        "#0000ff",
        "#00ff00",
        "#ff0000",
    ]


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


def test_concentrate_same_rank_equivalent_reverse_edges():
    """Equivalent same-rank reverse edges concentrate together (GitLab #150)."""

    concentrated_reverse_edges = _drawn_edges(_SAME_RANK_REVERSE_EDGES)
    assert len(concentrated_reverse_edges) == 1
    assert "_hdraw_" in concentrated_reverse_edges[0]
    assert "_tdraw_" in concentrated_reverse_edges[0]

    concentration_disabled = _SAME_RANK_REVERSE_EDGES.replace(
        "concentrate=true", "concentrate=false"
    )
    assert len(_drawn_edges(concentration_disabled)) == 2


def test_concentrate_same_rank_preserves_distinct_edge_colors():
    """Distinct same-rank reverse colors stay separate (GitLab #150)."""

    distinct_reverse_edges = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [color=red]"
    ).replace("b -> a", "b -> a [color=blue]")
    assert set(_drawn_edge_colors(distinct_reverse_edges)) == {
        "#ff0000",
        "#0000ff",
    }


def test_concentrate_same_rank_matches_ports_by_physical_endpoint():
    """Same-rank reverse ports match by physical endpoint (GitLab #150)."""

    equivalent_reverse_ports = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a:s -> b"
    ).replace("b -> a", "b -> a:s")
    assert len(_drawn_edges(equivalent_reverse_ports)) == 1

    nonmatching_reverse_ports = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a:n -> b"
    ).replace("b -> a", "b -> a:s")
    assert len(_drawn_edges(nonmatching_reverse_ports)) == 2


def test_concentrate_same_rank_compares_labels_by_rendering_role():
    """Same-rank labels compare by their rendered endpoint role (GitLab #150)."""

    same_grammar_endpoint_labels = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [headlabel=x]"
    ).replace("b -> a", "b -> a [headlabel=x]")
    assert len(_drawn_edges(same_grammar_endpoint_labels)) == 2

    same_physical_endpoint_labels = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [headlabel=x]"
    ).replace("b -> a", "b -> a [taillabel=x]")
    assert len(_drawn_edges(same_physical_endpoint_labels)) == 1

    same_rank_xlabels = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [xlabel=x]"
    ).replace("b -> a", "b -> a [xlabel=x]")
    assert len(_drawn_edges(same_rank_xlabels)) == 2


def test_concentrate_same_rank_compares_arrows_by_physical_endpoint():
    """Same-rank reverse arrows combine only at matching endpoints (GitLab #150)."""

    compatible_reverse_arrows = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [arrowhead=normal]"
    ).replace("b -> a", "b -> a [arrowhead=vee]")
    drawn_compatible_reverse_arrows = _drawn_edges(compatible_reverse_arrows)
    assert len(drawn_compatible_reverse_arrows) == 1
    assert _arrow_polygon_point_count(drawn_compatible_reverse_arrows[0], "h") == 3
    assert _arrow_polygon_point_count(drawn_compatible_reverse_arrows[0], "t") == 8

    conflicting_physical_endpoint_arrows = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [dir=both arrowhead=normal arrowtail=dot]"
    ).replace("b -> a", "b -> a [dir=both arrowhead=vee arrowtail=box]")
    assert len(_drawn_edges(conflicting_physical_endpoint_arrows)) == 2


def test_concentrate_same_rank_reverse_edges_across_rank_spans():
    """Same-rank reverse concentration works across rank spans (GitLab #150)."""

    rank_span_one = """
        strict digraph {
          concentrate=true
          subgraph same_rank {
            rank=same
            a
            b
          }
          c -> a [color=green]
          a -> b [color=red]
          b -> a [color=red]
        }
    """
    assert sorted(_drawn_edge_colors(rank_span_one)) == [
        "#00ff00",
        "#ff0000",
    ]

    rank_span_two = """
        strict digraph {
          concentrate=true
          subgraph same_rank {
            rank=same
            a
            b
          }
          z -> y [color=green]
          y -> a [color=green]
          a -> b [color=red]
          b -> a [color=red]
        }
    """
    assert sorted(_drawn_edge_colors(rank_span_two)) == [
        "#00ff00",
        "#00ff00",
        "#ff0000",
    ]


def test_concentrate_rank_spanning_reverse_edges_keep_xlabels():
    """emit_end_edge() emits xlabels per edge, so neither edge may disappear."""

    labeled_reverse_edges = """
        strict digraph {
          concentrate=true
          subgraph same_rank {
            rank=same
            a
            b
          }
          c -> a
          a -> b [xlabel=x]
          b -> a [xlabel=x]
        }
    """
    assert len(_drawn_edges(labeled_reverse_edges)) == 3


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_rank_edges_compare_their_actual_direction(splines: str):
    """Same-rank peers compare endpoint attributes in their actual direction."""

    same_direction_distinct_ports = _concentrated_graph(
        splines,
        "subgraph same_rank { rank=same; a; b }",
        "a:n -> b:s [color=red]",
        "a:s -> b:n [color=red]",
    )
    assert len(_drawn_edges(same_direction_distinct_ports)) == 2

    one_sided_clipping = _concentrated_graph(
        splines,
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [headclip=false]",
        "b -> a",
    )
    assert len(_drawn_edges(one_sided_clipping)) == 2


@pytest.mark.parametrize(
    "source",
    (
        """
        digraph {
          graph [concentrate=true]
          { rank=same; a; b; z; }
          a -> z [samehead=x]
          b -> z [samehead=x]
        }
        """,
        """
        digraph {
          graph [concentrate=false]
          { rank=same; a; b; z; }
          a -> z [samehead=x]
          b -> z [samehead=x]
        }
        """,
        """
        digraph {
          graph [concentrate=true]
          { rank=same; z; a; b; }
          z -> a [sametail=x]
          z -> b [sametail=x]
        }
        """,
        """
        digraph {
          graph [concentrate=false]
          { rank=same; z; a; b; }
          z -> a [sametail=x]
          z -> b [sametail=x]
        }
        """,
    ),
    ids=("samehead-concentrate", "samehead", "sametail-concentrate", "sametail"),
)
def test_flat_grouped_route_arrowheads_follow_terminal_tangents(source: str):
    """Grouped flat-route arrows follow their terminal Bezier control arms."""

    drawn_edges = _drawn_edges(source)
    assert len(drawn_edges) == 2
    assert max(_arrowhead_shaft_angle(edge) for edge in drawn_edges) <= 2


@pytest.mark.parametrize(
    "source",
    (
        _concentrated_graph(
            "",
            "subgraph same_rank { rank=same; a; b }",
            "a:n -> b:s [color=red]",
            "a:s -> b:n [color=red]",
        ),
        _concentrated_graph(
            "",
            "subgraph same_rank { rank=same; a; b }",
            "a:s -> b",
            "b -> a:s",
        ),
        _concentrated_graph(
            "",
            "subgraph same_rank { rank=same; a; b }",
            "a:n -> b",
            "b -> a:s",
        ),
        _concentrated_graph(
            "splines=ortho",
            "subgraph same_rank { rank=same; a; b }",
            "a:n -> b:s [color=red]",
            "a:s -> b:n [color=red]",
        ),
    ),
    ids=("opposite-port-pairs", "merged-south-port", "north-south", "ortho"),
)
def test_concentrate_flat_port_routes_respect_endpoint_geometry(source: str):
    """Flat port routes attach, depart outward, and never re-enter nodes."""

    layout = json.loads(dot("json", source=source))
    drawn_edges = [edge for edge in layout["edges"] if "_draw_" in edge]
    assert drawn_edges
    for edge in drawn_edges:
        for endpoint in ("tail", "head"):
            _assert_compass_attachment(layout, edge, endpoint)
            _assert_endpoint_departure(layout, edge, endpoint)
        assert _arrowhead_shaft_angle(edge) <= 0.1


def test_concentrate_ungrouped_flat_port_route_preserves_tail_direction():
    """Straightening a multi-segment flat route preserves terminal port arms."""

    source = """
        digraph {
          graph [concentrate=true]
          TOP -> {rank=same a f} -> BOTTOM
          a:w -> f:e
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "a", "f")
    points = _edge_bezier_points(edge)

    for endpoint in ("tail", "head"):
        node_id = edge["tail" if endpoint == "tail" else "head"]
        node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
        center_x, center_y, _, _ = _ellipse(node)
        route = min(
            (points, list(reversed(points))),
            key=lambda candidate: math.dist(candidate[0], (center_x, center_y)),
        )
        anchor = route[0]
        outward_point = next(
            point for point in route[1:] if math.dist(point, anchor) > 0.001
        )
        outward_normal = (anchor[0] - center_x, anchor[1] - center_y)
        departure = (outward_point[0] - anchor[0], outward_point[1] - anchor[1])
        assert sum(a * b for a, b in zip(outward_normal, departure)) > 0
    assert _arrowhead_shaft_angle(edge) <= 0.1


@pytest.mark.parametrize("concentrate", (False, True))
def test_flat_grouped_routes_depart_outward(concentrate: bool):
    """Restoring a sametail anchor also translates its terminal control arm."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; z; a; b; }}
          z -> a [sametail=x]
          z -> b [sametail=x]
        }}
    """
    layout = json.loads(dot("json", source=source))
    for edge in (edge for edge in layout["edges"] if "_draw_" in edge):
        _assert_endpoint_departure(layout, edge, "tail")


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_flat_mixed_sametail_route_does_not_reenter_tail(
    concentrate: bool,
):
    """A restored flat member of a mixed sametail group clears the tail node."""

    layout = json.loads(dot("json", source=_sametail_mixed_route_fixture(concentrate)))
    edge = _drawn_edge_between(layout, "A", "flat")
    _assert_endpoint_departure(layout, edge, "tail")


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_mixed_samehead_backward_route_approaches_outward(
    concentrate: bool,
):
    """A backward member of a mixed samehead group clears the head node."""

    layout = json.loads(dot("json", source=_samehead_mixed_route_fixture(concentrate)))
    edge = _drawn_edge_between(layout, "back_anchor", "A")
    _assert_endpoint_departure(layout, edge, "head")


@pytest.mark.parametrize("concentrate", (False, True))
def test_flat_grouped_routes_preserve_head_direction_under_rankdir_flip(
    concentrate: bool,
):
    """restore_flat_edge_ports() sees physical tails after rankdir=LR swapping."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()} rankdir=LR]
          {{ rank=same; z; a; b; }}
          z -> a [samehead=x]
          z -> b [samehead=x]
        }}
    """
    layout = json.loads(dot("json", source=source))
    for edge in (edge for edge in layout["edges"] if "_draw_" in edge):
        _assert_endpoint_departure(layout, edge, "head")


def test_concentrate_flat_bidirectional_arrows_use_distinct_clip_ends():
    """A short merged flat route arcs enough for both endpoint arrows."""

    source = _concentrated_graph(
        "",
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [headlabel=x]",
        "b -> a [taillabel=x]",
    )
    layout = json.loads(dot("json", source=source))
    edge = next(edge for edge in layout["edges"] if "_draw_" in edge)
    assert len([edge for edge in layout["edges"] if "_draw_" in edge]) == 1

    head_box = _point_box(_edge_arrow_polygon(edge, "_hdraw_"))
    tail_box = _point_box(_edge_arrow_polygon(edge, "_tdraw_"))
    assert _boxes_are_disjoint(head_box, tail_box)

    for endpoint in ("tail", "head"):
        node_id = edge[endpoint]
        node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
        center_x, center_y, radius_x, radius_y = _ellipse(node)
        x, y = _edge_physical_endpoint(edge, endpoint)
        boundary = ((x - center_x) / radius_x) ** 2 + (
            (y - center_y) / radius_y
        ) ** 2
        assert boundary == pytest.approx(1, abs=0.05)


def test_concentrate_short_compound_arrows_do_not_overlap():
    """Short cluster-clipped reverse edges must not draw overlapping arrows."""

    source = """
        digraph {
          graph [concentrate=true compound=true]
          subgraph cluster_a { a }
          subgraph cluster_b { b }
          a -> b [ltail=cluster_a lhead=cluster_b]
          b -> a [ltail=cluster_b lhead=cluster_a]
        }
    """
    edge = _drawn_edges(source)[0]
    arrows = [
        operation["points"]
        for stream in ("_hdraw_", "_tdraw_")
        for operation in edge.get(stream, [])
        if operation["op"] == "P"
    ]
    boxes = [_point_box(arrow) for arrow in arrows]
    for first, second in itertools.combinations(boxes, 2):
        assert _boxes_are_disjoint(first, second)


def test_concentrate_compound_clipping_keeps_suppressed_junction_arrows_hidden():
    """Cluster clipping must preserve concentrated junction suppression bits."""

    source = """
        digraph {
          graph [concentrate=true compound=true]
          subgraph cluster_a { a }
          subgraph cluster_b { b }
          a -> b [ltail=cluster_a lhead=cluster_b]
          b -> a [ltail=cluster_b lhead=cluster_a]
        }
    """
    edge = _drawn_edges(source)[0]
    arrow_polygons = [
        operation
        for stream in ("_hdraw_", "_tdraw_")
        for operation in edge.get(stream, [])
        if operation["op"] == "P"
    ]
    assert len(arrow_polygons) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_arrowheads_by_physical_endpoint(
    splines: str,
):
    """Opposite arrowheads render at opposite physical endpoints."""

    compatible_reverse_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=normal]",
        "b -> a [arrowhead=vee]",
    )
    drawn_edges = _drawn_edges(compatible_reverse_arrows)
    assert len(drawn_edges) == 1
    assert _arrow_polygon_point_count(drawn_edges[0], "h") == 3
    assert _arrow_polygon_point_count(drawn_edges[0], "t") == 8


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_rejects_conflicting_physical_endpoint_arrows(splines: str):
    """Packed arrow flags cannot hold two different shapes at one endpoint."""

    conflicting_reverse_arrows = _concentrated_graph(
        splines,
        "a -> b [dir=both arrowhead=normal arrowtail=dot]",
        "b -> a [dir=both arrowhead=vee arrowtail=box]",
    )
    assert len(_drawn_edges(conflicting_reverse_arrows)) == 2


def test_concentrate_flat_cycle_keeps_virtual_representatives_private():
    """Flat cycle reversal keeps non-Cgraph representative edges private."""

    flat_cycle = _concentrated_graph(
        "",
        "subgraph same_rank { rank=same; a; b; c }",
        "a -> b",
        "b -> c",
        "c -> a",
        "a -> c",
    )
    assert len(_drawn_edges(flat_cycle)) == 3


def test_concentrate_flat_aux_routes_keep_suppressed_junction_arrows_hidden():
    """Aux-graph flat route copies must preserve concentrated suppression bits."""

    layout = json.loads(
        dot(
            "json",
            source="""
                digraph {
                  graph [concentrate=true]
                  { rank=same; a; b }
                  a:e -> b:w
                  b:w -> a:e
                }
            """,
        )
    )
    arrow_polygons = [
        operation
        for edge in layout["edges"]
        for stream in ("_hdraw_", "_tdraw_")
        for operation in edge.get(stream, [])
        if operation["op"] == "P"
    ]
    assert len(arrow_polygons) == 1


def test_concentrate_ortho_ignores_suppressed_representatives():
    """Suppressed edges never become ortho concentration group heads."""

    ignored_reverse_first = _concentrated_graph(
        "splines=ortho",
        "b",
        "a -> b [color=red]",
        "b -> a [constraint=false color=red]",
        "a -> b [color=blue]",
    )
    assert sorted(_drawn_edge_colors(ignored_reverse_first)) == [
        "#0000ff",
        "#ff0000",
    ]


@pytest.mark.parametrize("direction", ("down", "up"))
@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_record_port_continuations(
    direction: str, splines: str
):
    """Concentration retains distinct record-port continuations (GitLab #449)."""

    if direction == "down":
        rank_constraint = "subgraph { rank=source; source }"
        distinct_edges = """
          some -> problem:p1
          source -> problem:p2
          source -> problem:p3
        """
    else:
        rank_constraint = "subgraph { rank=sink; sink }"
        distinct_edges = """
          problem:p1 -> some
          problem:p2 -> sink
          problem:p3 -> sink
        """

    distinct_ports = _concentrated_graph(
        splines,
        'problem [shape=record, label="<p1>p1|<p2>p2|<p3>p3"]',
        rank_constraint,
        distinct_edges,
    )
    assert len(_drawn_edges(distinct_ports)) == 3

    same_port_duplicates = distinct_ports.replace("problem:p3", "problem:p2")
    assert len(_drawn_edges(same_port_duplicates)) == 2


@pytest.mark.parametrize("direction", ("down", "up"))
def test_concentrate_record_port_routes_clip_at_field_boundaries(direction: str):
    """Distinct record continuations attach at their own field rectangles."""

    if direction == "down":
        rank_constraint = "subgraph { rank=source; source }"
        distinct_edges = """
          some -> problem:p1
          source -> problem:p2
          source -> problem:p3
        """
        endpoint = "head"
    else:
        rank_constraint = "subgraph { rank=sink; sink }"
        distinct_edges = """
          problem:p1 -> some
          problem:p2 -> sink
          problem:p3 -> sink
        """
        endpoint = "tail"

    source = _concentrated_graph(
        "",
        'problem [shape=record, label="<p1>p1|<p2>p2|<p3>p3"]',
        rank_constraint,
        distinct_edges,
    )
    layout = json.loads(dot("json", source=source))
    problem = next(node for node in layout["objects"] if node["name"] == "problem")
    rectangles = tuple(
        tuple(map(float, rectangle.split(","))) for rectangle in problem["rects"].split()
    )
    problem_id = problem["_gvid"]
    edges = [
        edge
        for edge in layout["edges"]
        if "_draw_" in edge and edge[endpoint] == problem_id
    ]
    assert len(edges) == 3
    for edge in edges:
        port = edge[f"{endpoint}port"]
        rectangle = rectangles[int(port[1:]) - 1]
        x, y = _edge_physical_endpoint(edge, endpoint)
        left, bottom, right, top = rectangle
        # `rects` describes the record-field interior; clipping happens at
        # the outside of the half-point outline stroke.
        assert left - 0.6 <= x <= right + 0.6
        assert bottom - 0.6 <= y <= top + 0.6
        assert min(abs(x - left), abs(x - right), abs(y - bottom), abs(y - top)) <= 0.6


def _assert_public_concentrate_crash_repro_renders(issue: int):
    input = Path(__file__).parent / f"{issue}.dot"
    assert input.exists(), "unexpectedly missing test case"

    proc = subprocess.run(
        ["dot", "-Kdot", "-Tdot", input],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    for token in ("AddressSanitizer", "DEADLYSIGNAL", "SEGV"):
        assert token not in proc.stderr
    assert any("->" in line and "pos=" in line for line in proc.stdout.splitlines())


def test_concentrate_issue_2764_public_repro_renders_edge_splines():
    """GitLab #2764: raw public conc_slope crash repro still renders edges."""

    _assert_public_concentrate_crash_repro_renders(2764)


def test_concentrate_issue_2765_public_repro_renders_edge_splines():
    """GitLab #2765: raw public straight_len crash repro still renders edges."""

    _assert_public_concentrate_crash_repro_renders(2765)
