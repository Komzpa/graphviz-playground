"""Concentrate tests: Duplicate self-edge label placement and deduped loop geometry."""

import pytest

from concentrate_helpers import (
    Path,
    _box_gap,
    _boxes_overlap,
    _concentrated_graph,
    _drawn_edge_polyline,
    _edge_label_boxes,
    _edge_label_boxes_from_layout,
    _graph_width,
    _json_layout,
    _node_boxes,
    _point_segment_distance,
    itertools,
)

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
