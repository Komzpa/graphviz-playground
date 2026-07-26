"""Tests for dot's opt-in edgejunction graph attribute."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from gvtest import dot, run


def _layout(source):
    return json.loads(dot("json", source=source))


def _svg(source):
    return dot("svg", source=source)


def _edge_objects(layout, **attrs):
    edges = layout["edges"]
    for key, value in attrs.items():
        edges = [edge for edge in edges if edge.get(key) == value]
    return edges


def _without_echoed_edgejunction_attr(xdot):
    return re.sub(r"\n\t\tedgejunction=none,", "", xdot)


@dataclass(frozen=True)
class _Point:
    x: float
    y: float


@dataclass(frozen=True)
class _Segment:
    index: int
    edge_index: int
    tail: int
    head: int
    start: _Point
    end: _Point
    first_for_edge: bool
    last_for_edge: bool
    has_head_arrow: bool


def _distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def _bezier_point(points, t):
    omt = 1.0 - t
    return _Point(
        omt**3 * points[0].x
        + 3.0 * omt**2 * t * points[1].x
        + 3.0 * omt * t**2 * points[2].x
        + t**3 * points[3].x,
        omt**3 * points[0].y
        + 3.0 * omt**2 * t * points[1].y
        + 3.0 * omt * t**2 * points[2].y
        + t**3 * points[3].y,
    )


def _label_points(edge):
    return [
        _Point(float(op["pt"][0]), float(op["pt"][1]))
        for op in edge.get("_ldraw_", [])
        if op.get("op") == "T"
    ]


def _label_height(edge):
    for op in edge.get("_ldraw_", []):
        if op.get("op") == "F":
            return float(op["size"])
    return 14.0


def _distance_to_spline(edge, point):
    best = math.inf
    for op in edge.get("_draw_", []):
        if op.get("op") != "b":
            continue
        points = [_Point(float(x), float(y)) for x, y in op.get("points", [])]
        for i in range(0, len(points) - 3, 3):
            curve = points[i : i + 4]
            for step in range(41):
                best = min(best, _distance(point, _bezier_point(curve, step / 40)))
    return best


def _parse_point(value):
    x, y = value.split(",", 1)
    return _Point(float(x), float(y))


def _node_boundary_distance(node, point):
    center = _parse_point(node["pos"])
    rx = max(float(node.get("width", 0.0)) * 36.0, 0.01)
    ry = max(float(node.get("height", 0.0)) * 36.0, 0.01)
    dx = point.x - center.x
    dy = point.y - center.y

    if node.get("shape") == "point":
        return max(0.0, math.hypot(dx, dy) - max(rx, ry))

    norm = math.hypot(dx / rx, dy / ry)
    if norm == 0.0:
        return min(rx, ry)
    boundary = _Point(center.x + dx / norm, center.y + dy / norm)
    return _distance(point, boundary)


def _on_boundary(node, point, eps):
    return _node_boundary_distance(node, point) <= eps


def _rendered_segments(layout):
    segments = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        ops = [op for op in edge.get("_draw_", []) if op.get("op") == "b"]
        if not ops:
            continue
        last_op_index = len(ops) - 1
        local_index = 0
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            points = [_Point(float(x), float(y)) for x, y in op.get("points", [])]
            if len(points) < 2:
                continue
            segments.append(
                _Segment(
                    index=len(segments),
                    edge_index=edge_index,
                    tail=int(edge["tail"]),
                    head=int(edge["head"]),
                    start=points[0],
                    end=points[-1],
                    first_for_edge=local_index == 0,
                    last_for_edge=local_index == last_op_index,
                    has_head_arrow=bool(edge.get("_hdraw_")),
                )
            )
            local_index += 1
    return segments


def _shared_endpoint(segments, point, own_index, eps):
    return any(
        segment.index != own_index
        and (_distance(point, segment.start) <= eps or _distance(point, segment.end) <= eps)
        for segment in segments
    )


def _empty_spline_endpoints(layout, eps=2.0):
    nodes = {int(obj["_gvid"]): obj for obj in layout.get("objects", [])}
    segments = _rendered_segments(layout)
    empty = []
    for segment in segments:
        for side, point in (("start", segment.start), ("end", segment.end)):
            if side == "start" and not segment.first_for_edge:
                continue
            if side == "end" and not segment.last_for_edge:
                continue
            if (
                not _on_boundary(nodes[segment.tail], point, eps)
                and not _on_boundary(nodes[segment.head], point, eps)
                and not (side == "end" and segment.has_head_arrow)
                and not _shared_endpoint(segments, point, segment.index, eps)
            ):
                empty.append((segment.edge_index, side, point))
    return empty


def test_edgejunction_absent_keeps_output_unchanged():
    source = """
        digraph {
          edge [label=shared, minlen=2]
          amber -> target
          violet -> target
        }
    """
    explicit_none = source.replace("digraph {", "digraph { graph [edgejunction=none]")

    assert dot("xdot", source=source) == _without_echoed_edgejunction_attr(
        dot("xdot", source=explicit_none)
    )
    layout = _layout(source)
    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]


def test_edgejunction_fanin_draws_one_labelled_trunk_with_one_arrowhead():
    source = """
        digraph {
          graph [edgejunction=fanin]
          edge [label=shared, minlen=2]
          amber -> target
          violet -> target
        }
    """
    layout = _layout(source)
    originals = _edge_objects(layout, label="shared")
    junctions = [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]
    svg = _svg(source)

    assert len(originals) == 2
    assert all(";" in edge["pos"] for edge in originals)
    assert sum("_hdraw_" in edge for edge in originals) == 1
    assert sum("_ldraw_" in edge for edge in originals) == 1
    assert len(re.findall(r"<polygon fill=\"black\" stroke=\"black\"", svg)) == 1
    assert len(junctions) == 1
    assert junctions[0]["shape"] == "point"
    assert float(junctions[0]["width"]) == pytest.approx(0.02)
    assert float(junctions[0]["height"]) == pytest.approx(0.02)


def test_edgejunction_fanout_draws_one_labelled_trunk_with_head_arrowheads():
    source = """
        digraph {
          graph [edgejunction=fanout]
          edge [label=shared, minlen=2]
          source -> amber
          source -> violet
        }
    """
    layout = _layout(source)
    originals = _edge_objects(layout, label="shared")
    junctions = [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]
    svg = _svg(source)

    assert len(originals) == 2
    assert all(";" in edge["pos"] for edge in originals)
    assert sum("_hdraw_" in edge for edge in originals) == 2
    assert sum("_ldraw_" in edge for edge in originals) == 1
    assert len(re.findall(r"<polygon fill=\"black\" stroke=\"black\"", svg)) == 2
    assert len(junctions) == 1
    assert junctions[0]["shape"] == "point"
    assert float(junctions[0]["width"]) == pytest.approx(0.02)
    assert float(junctions[0]["height"]) == pytest.approx(0.02)


def test_edgejunction_both_handles_fanin_and_fanout():
    source = """
        digraph {
          graph [edgejunction=both]
          edge [label=in, minlen=2]
          amber -> target
          violet -> target
          edge [label=out, minlen=2]
          source -> cyan
          source -> magenta
        }
    """
    layout = _layout(source)

    assert len(_edge_objects(layout, label="in")) == 2
    assert len(_edge_objects(layout, label="out")) == 2
    assert sum("_ldraw_" in edge for edge in _edge_objects(layout, label="in")) == 1
    assert sum("_ldraw_" in edge for edge in _edge_objects(layout, label="out")) == 1
    assert (
        len(
            [
                obj
                for obj in layout["objects"]
                if obj.get("name", "").startswith("_edgejunction_")
            ]
        )
        == 2
    )


def test_edgejunction_both_keeps_fanout_trunk_label_inside_bbox_and_near_spline():
    source = """
        digraph disk_states {
          graph [edgejunction=both]
          node [shape=ellipse]
          Diskless -> Inconsistent [label="ioctl_set_disk()"]
          Diskless -> Consistent [label="ioctl_set_disk()"]
          Diskless -> Outdated [label="ioctl_set_disk()"]
          Consistent -> Outdated [label="receive_param()"]
          Consistent -> UpToDate [label="receive_param()"]
          Consistent -> Inconsistent [label="start resync"]
          Outdated -> Inconsistent [label="start resync"]
          UpToDate -> Inconsistent [label="ioctl_replicate"]
          Inconsistent -> UpToDate [label="resync completed"]
          Consistent -> Failed [label="io completion error"]
          Outdated -> Failed [label="io completion error"]
          UpToDate -> Failed [label="io completion error"]
          Inconsistent -> Failed [label="io completion error"]
          Failed -> Diskless [label="sending notify to peer"]
        }
    """
    layout = _layout(source)
    xmin, ymin, xmax, ymax = [float(v) for v in layout["bb"].split(",")]
    labelled = [
        edge
        for edge in _edge_objects(layout, label="receive_param()")
        if edge.get("_ldraw_")
    ]

    assert len(labelled) == 1
    point = _label_points(labelled[0])[0]
    assert xmin <= point.x <= xmax
    assert ymin <= point.y <= ymax
    assert _distance_to_spline(labelled[0], point) <= 4.0 * _label_height(labelled[0])


def test_edgejunction_true_means_both():
    source = """
        digraph {
          graph [edgejunction=true]
          edge [label=in, minlen=2]
          amber -> target
          violet -> target
          edge [label=out, minlen=2]
          source -> cyan
          source -> magenta
        }
    """
    layout = _layout(source)

    assert sum("_ldraw_" in edge for edge in _edge_objects(layout, label="in")) == 1
    assert sum("_ldraw_" in edge for edge in _edge_objects(layout, label="out")) == 1


def test_edgejunction_fanin_places_default_minlen_label():
    source = """
        digraph {
          graph [edgejunction=fanin]
          edge [label=shared]
          amber -> target
          violet -> target
        }
    """
    layout = _layout(source)
    originals = _edge_objects(layout, label="shared")

    assert len(originals) == 2
    assert sum("_ldraw_" in edge for edge in originals) == 1


def test_edgejunction_fanin_has_no_empty_spline_endpoints():
    source = """
        digraph {
          graph [edgejunction=fanin]
          edge [label=shared, minlen=2]
          alpha -> target
          beta -> target
          gamma -> target
          delta -> target
        }
    """

    assert _empty_spline_endpoints(_layout(source)) == []


def test_edgejunction_fanout_has_no_empty_spline_endpoints():
    source = """
        digraph {
          graph [edgejunction=fanout]
          edge [label=shared, minlen=2]
          source -> alpha
          source -> beta
          source -> gamma
          source -> delta
        }
    """

    assert _empty_spline_endpoints(_layout(source)) == []


def test_edgejunction_fanin_keeps_distinct_colours_separate():
    source = """
        digraph {
          graph [concentrate=true, edgejunction=fanin]
          edge [penwidth=3]
          { rank=min; north; south }
          north -> waypoint
          waypoint -> relay
          relay -> sink
          north -> sink [color=blue]
          south -> sink [color=red]
          north -> sink
          south -> sink
        }
    """
    layout = _layout(source)
    coloured = [
        edge for edge in layout["edges"] if edge.get("color") in ("blue", "red")
    ]

    assert {edge["color"] for edge in coloured} == {"blue", "red"}
    assert all("_draw_" in edge for edge in coloured)
    assert all("_hdraw_" in edge for edge in coloured)


def test_edgejunction_fanin_fuses_rank_adjacent_shared_trunk_siblings():
    fixture = (
        Path(__file__).parent
        / "graphs"
        / "concentrate-demo"
        / "distinct-shared-trunk-siblings-separate.dot"
    )
    layout = json.loads(
        run("dot", "-Gedgejunction=fanin", "-Tjson", fixture, timeout=10)
    )
    names = {int(obj["_gvid"]): obj["name"] for obj in layout["objects"]}
    junctions = [
        obj for obj in layout["objects"] if obj.get("_edgejunction_node") == "true"
    ]
    black_originals = [
        edge
        for edge in layout["edges"]
        if edge.get("_edgejunction_original") == "true"
        and names[int(edge["head"])] == "d"
        and edge.get("color", "black") == "black"
    ]
    coloured = [
        edge
        for edge in layout["edges"]
        if names[int(edge["head"])] == "d" and edge.get("color") in ("blue", "red")
    ]

    assert len(junctions) == 1
    assert {names[int(edge["tail"])] for edge in black_originals} == {"a", "b", "e"}
    assert sum("_hdraw_" in edge for edge in black_originals) == 1
    assert {edge["color"] for edge in coloured} == {"blue", "red"}
    assert sum("_hdraw_" in edge for edge in coloured) == 2
    assert (
        sum(
            "_hdraw_" in edge
            for edge in layout["edges"]
            if names[int(edge["head"])] == "d"
        )
        == 3
    )


def test_edgejunction_fanout_keeps_distinct_colours_separate():
    source = """
        digraph {
          graph [concentrate=true, edgejunction=fanout]
          edge [penwidth=3]
          { rank=max; north; south }
          source -> relay
          relay -> waypoint
          waypoint -> north
          source -> north [color=blue]
          source -> south [color=red]
          source -> north
          source -> south
        }
    """
    layout = _layout(source)
    coloured = [
        edge for edge in layout["edges"] if edge.get("color") in ("blue", "red")
    ]

    assert {edge["color"] for edge in coloured} == {"blue", "red"}
    assert all("_draw_" in edge for edge in coloured)
    assert all("_hdraw_" in edge for edge in coloured)


def test_edgejunction_fanin_preserves_user_node_name_collision():
    source = """
        digraph {
          graph [edgejunction=fanin]
          _edgejunction_0 [label=user]
          edge [label=shared, minlen=2]
          amber -> target
          violet -> target
        }
    """
    layout = _layout(source)
    objects = layout["objects"]
    user = [obj for obj in objects if obj.get("name") == "_edgejunction_0"]
    helpers = [
        obj
        for obj in objects
        if obj.get("name", "").startswith("_edgejunction_")
        and obj.get("_edgejunction_node") == "true"
    ]

    assert len(user) == 1
    assert user[0]["label"] == "user"
    assert user[0].get("shape") != "point"
    assert len(helpers) == 1
    assert helpers[0]["name"] != "_edgejunction_0"


def test_edgejunction_fanout_preserves_user_node_name_collision():
    source = """
        digraph {
          graph [edgejunction=fanout]
          _edgejunction_0 [label=user]
          edge [label=shared, minlen=2]
          source -> amber
          source -> violet
        }
    """
    layout = _layout(source)
    objects = layout["objects"]
    user = [obj for obj in objects if obj.get("name") == "_edgejunction_0"]
    helpers = [
        obj
        for obj in objects
        if obj.get("name", "").startswith("_edgejunction_")
        and obj.get("_edgejunction_node") == "true"
    ]

    assert len(user) == 1
    assert user[0]["label"] == "user"
    assert user[0].get("shape") != "point"
    assert len(helpers) == 1
    assert helpers[0]["name"] != "_edgejunction_0"


def test_edgejunction_user_sentinel_attrs_do_not_affect_plain_output():
    source = """
        digraph {
          _edgejunction_0 [_edgejunction_node=true, label=user]
          a -> b [
            label=x,
            _edgejunction_internal=true,
            _edgejunction_draw_trunk=false,
            _edgejunction_arm_splines=0
          ]
        }
    """
    svg = _svg(source)

    assert ">user</text>" in svg
    assert ">x</text>" in svg
    assert svg.count("<path") == 1
    assert len(re.findall(r"<polygon fill=\"black\" stroke=\"black\"", svg)) == 1


def test_edgejunction_fanin_keeps_different_map_metadata_separate():
    source = """
        digraph {
          graph [edgejunction=fanin]
          edge [label=shared, minlen=2]
          amber -> target [URL="amber"]
          violet -> target [URL="violet"]
        }
    """
    layout = _layout(source)

    assert len(_edge_objects(layout, label="shared")) == 2
    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]


def test_edgejunction_fanin_keeps_different_layers_separate():
    source = """
        digraph {
          graph [edgejunction=fanin, layers="one:two"]
          edge [label=shared, minlen=2]
          amber -> target [layer=one]
          violet -> target [layer=two]
        }
    """
    layout = _layout(source)

    assert len(_edge_objects(layout, label="shared")) == 2
    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]


def test_edgejunction_fanin_caps_multicolor_hidden_trunk():
    source = """
        digraph {
          graph [edgejunction=fanin]
          edge [label=shared, minlen=2, color="red:blue", penwidth=3]
          amber -> target
          violet -> target
        }
    """
    svg = _svg(source)
    layout = _layout(source)

    assert len(_edge_objects(layout, label="shared")) == 2
    assert sum("_ldraw_" in edge for edge in layout["edges"]) == 1
    assert svg.count("<path") == 10


def test_edgejunction_fanin_clustered_graph_falls_back():
    source = """
        digraph {
          graph [edgejunction=fanin]
          subgraph cluster_one {
            amber
            violet
          }
          edge [label=shared, minlen=2]
          amber -> target
          violet -> target
        }
    """

    assert run("dot", "-Tsvg", input=source, timeout=10) is not None

    layout = _layout(source)
    assert len(_edge_objects(layout, label="shared")) == 2
    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]


def test_edgejunction_fanout_clustered_graph_falls_back():
    source = """
        digraph {
          graph [edgejunction=fanout]
          subgraph cluster_one {
            amber
            violet
          }
          edge [label=shared, minlen=2]
          source -> amber
          source -> violet
        }
    """

    assert run("dot", "-Tsvg", input=source, timeout=10) is not None

    layout = _layout(source)
    assert len(_edge_objects(layout, label="shared")) == 2
    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_edgejunction_")
    ]
