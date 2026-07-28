"""Tests for dot's concentrate junction behavior."""

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


def _strip_echoed_concentrate_false(xdot):
    return xdot.replace("\n\t\tconcentrate=false,", "")


def _fixture_svg(path, *args):
    return run("dot", *args, "-Tsvg", path, timeout=10)


def _fixture_json(path, *args):
    return json.loads(run("dot", *args, "-Tjson", path, timeout=10))


def _assert_fixture_refuses_concentrate_junction(path):
    layout = _fixture_json(path, "-Gconcentrate=true")
    assert not [
        obj
        for obj in layout.get("objects", [])
        if obj.get("_concentrate_junction_node") == "true"
    ]
    assert not [
        edge
        for edge in layout.get("edges", [])
        if edge.get("_concentrate_junction_original") == "true"
    ]


def _edge_objects(layout, **attrs):
    edges = layout["edges"]
    for key, value in attrs.items():
        edges = [edge for edge in edges if edge.get(key) == value]
    return edges


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
    junction_nodes = [
        obj
        for obj in layout.get("objects", [])
        if obj.get("_concentrate_junction_node") == "true"
    ]
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
                and not any(_on_boundary(junction, point, eps) for junction in junction_nodes)
                and not (side == "end" and segment.has_head_arrow)
                and not _shared_endpoint(segments, point, segment.index, eps)
            ):
                empty.append((segment.edge_index, side, point))
    return empty


def test_concentrate_junction_absent_keeps_output_unchanged():
    source = """
        digraph {
          edge [label=shared, minlen=2]
          amber -> target
          violet -> target
        }
    """
    explicit_false = source.replace("digraph {", "digraph { graph [concentrate=false]")

    assert dot("xdot", source=source) == _strip_echoed_concentrate_false(
        dot("xdot", source=explicit_false)
    )
    layout = _layout(source)
    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("name", "").startswith("_concentrate_junction_")
    ]


def test_concentrate_junction_fanin_same_rank_cycle_renders():
    dot(
        "dot",
        source_file=Path(__file__).parent
        / "graphs"
        / "concentrate-junction-fanin-same-rank-cycle.dot",
    )


def test_concentrate_junction_fanin_draws_one_labelled_trunk_with_one_arrowhead():
    source = """
        digraph {
          graph [concentrate=true]
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
        if obj.get("name", "").startswith("_concentrate_junction_")
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


def test_concentrate_junction_fanout_draws_one_labelled_trunk_with_head_arrowheads():
    source = """
        digraph {
          graph [concentrate=true]
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
        if obj.get("name", "").startswith("_concentrate_junction_")
    ]
    svg = _svg(source)

    assert len(originals) == 2
    assert any(";" in edge["pos"] for edge in originals)
    assert sum("_hdraw_" in edge for edge in originals) == 2
    assert sum("_ldraw_" in edge for edge in originals) == 1
    assert len(re.findall(r"<polygon fill=\"black\" stroke=\"black\"", svg)) == 2
    assert len(junctions) == 1
    assert junctions[0]["shape"] == "point"
    assert float(junctions[0]["width"]) == pytest.approx(0.02)
    assert float(junctions[0]["height"]) == pytest.approx(0.02)


def test_concentrate_junction_both_handles_fanin_and_fanout():
    source = """
        digraph {
          graph [concentrate=true]
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
                if obj.get("name", "").startswith("_concentrate_junction_")
            ]
        )
        == 2
    )


def test_concentrate_junction_both_keeps_fanout_trunk_label_inside_bbox():
    source = """
        digraph disk_states {
          graph [concentrate=true]
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


def test_concentrate_true_junctions_fanin_and_fanout():
    source = """
        digraph {
          graph [concentrate=true]
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


def test_concentrate_junction_fanin_places_default_minlen_label():
    source = """
        digraph {
          graph [concentrate=true]
          edge [label=shared]
          amber -> target
          violet -> target
        }
    """
    layout = _layout(source)
    originals = _edge_objects(layout, label="shared")

    assert len(originals) == 2
    assert sum("_ldraw_" in edge for edge in originals) == 1


def test_concentrate_junction_fanin_has_no_empty_spline_endpoints():
    source = """
        digraph {
          graph [concentrate=true]
          edge [label=shared, minlen=2]
          alpha -> target
          beta -> target
          gamma -> target
          delta -> target
        }
    """

    assert _empty_spline_endpoints(_layout(source)) == []


def test_concentrate_junction_fanout_has_no_empty_spline_endpoints():
    source = """
        digraph {
          graph [concentrate=true]
          edge [label=shared, minlen=2]
          source -> alpha
          source -> beta
          source -> gamma
          source -> delta
        }
    """

    assert _empty_spline_endpoints(_layout(source)) == []


def test_concentrate_junction_fanin_keeps_distinct_colours_separate():
    source = """
        digraph {
          graph [concentrate=true]
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


def test_concentrate_junction_refuses_curved_attributed_concentrated_edges():
    fixture = (
        Path(__file__).parent
        / "graphs"
        / "concentrate-demo"
        / "curved-concentrated-attributed-chain.dot"
    )

    _assert_fixture_refuses_concentrate_junction(fixture)


def test_concentrate_junction_refuses_flat_same_rank_edges():
    fixture = Path(__file__).parents[1] / "graphs" / "directed" / "longflat.gv"

    _assert_fixture_refuses_concentrate_junction(fixture)


def test_concentrate_junction_refuses_self_loops():
    fixture = (
        Path(__file__).parent
        / "graphs"
        / "concentrate-demo"
        / "self-loop-label-beside-loop.dot"
    )

    _assert_fixture_refuses_concentrate_junction(fixture)


def test_concentrate_junction_refuses_record_endpoints():
    fixture = Path(__file__).parents[1] / "graphs" / "directed" / "record2.gv"

    _assert_fixture_refuses_concentrate_junction(fixture)


def test_concentrate_junction_refuses_port_endpoints():
    fixture = (
        Path(__file__).parents[1] / "graphs" / "directed" / "honda-tokoro.gv"
    )

    _assert_fixture_refuses_concentrate_junction(fixture)


def test_concentrate_junction_allows_labelled_concentrated_fans():
    fixture = Path(__file__).parent / "drbd-anchor.dot"
    layout = json.loads(run("dot", "-Gconcentrate=true", "-Tjson", fixture, timeout=10))
    junctions = [
        obj for obj in layout["objects"] if obj.get("_concentrate_junction_node") == "true"
    ]
    required = {
        "ioctl_set_disk()",
        "receive_param()",
        "io completion error",
        "start resync",
    }
    counts = {
        label: sum(
            1
            for edge in layout["edges"]
            for op in edge.get("_ldraw_", [])
            if op.get("op") == "T" and op.get("text") == label
        )
        for label in required
    }

    assert len(junctions) == 4
    assert counts == {label: 1 for label in required}


def test_concentrate_junction_refusal_does_not_disable_safe_fan():
    source = """
        digraph {
          graph [concentrate=true]
          edge [label=shared, minlen=2]
          a -> d
          b -> d
          unsafe -> unsafe
        }
    """
    layout = _layout(source)
    junctions = [
        obj
        for obj in layout["objects"]
        if obj.get("_concentrate_junction_node") == "true"
    ]

    assert len(junctions) == 1


def test_concentrate_junction_refuses_true_multiedges_inside_mixed_fan():
    source = """
        digraph {
          graph [concentrate=true]
          a -> b
          a -> b
          a -> c
        }
    """
    layout = _layout(source)

    assert not [
        obj
        for obj in layout["objects"]
        if obj.get("_concentrate_junction_node") == "true"
    ]
    assert not [
        edge
        for edge in layout["edges"]
        if edge.get("_concentrate_junction_original") == "true"
    ]


def test_concentrate_junction_fanin_fuses_rank_adjacent_shared_trunk_siblings():
    fixture = (
        Path(__file__).parent
        / "graphs"
        / "concentrate-demo"
        / "distinct-shared-trunk-siblings-separate.dot"
    )
    layout = json.loads(
        run("dot", "-Gconcentrate=true", "-Tjson", fixture, timeout=10)
    )
    names = {int(obj["_gvid"]): obj["name"] for obj in layout["objects"]}
    junctions = [
        obj for obj in layout["objects"] if obj.get("_concentrate_junction_node") == "true"
    ]
    black_originals = [
        edge
        for edge in layout["edges"]
        if edge.get("_concentrate_junction_original") == "true"
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


def test_concentrate_junction_fanout_keeps_distinct_colours_separate():
    source = """
        digraph {
          graph [concentrate=true]
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


def test_concentrate_junction_fanin_preserves_user_node_name_collision():
    source = """
        digraph {
          graph [concentrate=true]
          _concentrate_junction_0 [label=user]
          edge [label=shared, minlen=2]
          amber -> target
          violet -> target
        }
    """
    layout = _layout(source)
    objects = layout["objects"]
    user = [obj for obj in objects if obj.get("name") == "_concentrate_junction_0"]
    helpers = [
        obj
        for obj in objects
        if obj.get("name", "").startswith("_concentrate_junction_")
        and obj.get("_concentrate_junction_node") == "true"
    ]

    assert len(user) == 1
    assert user[0]["label"] == "user"
    assert user[0].get("shape") != "point"
    assert len(helpers) == 1
    assert helpers[0]["name"] != "_concentrate_junction_0"


def test_concentrate_junction_fanout_preserves_user_node_name_collision():
    source = """
        digraph {
          graph [concentrate=true]
          _concentrate_junction_0 [label=user]
          edge [label=shared, minlen=2]
          source -> amber
          source -> violet
        }
    """
    layout = _layout(source)
    objects = layout["objects"]
    user = [obj for obj in objects if obj.get("name") == "_concentrate_junction_0"]
    helpers = [
        obj
        for obj in objects
        if obj.get("name", "").startswith("_concentrate_junction_")
        and obj.get("_concentrate_junction_node") == "true"
    ]

    assert len(user) == 1
    assert user[0]["label"] == "user"
    assert user[0].get("shape") != "point"
    assert len(helpers) == 1
    assert helpers[0]["name"] != "_concentrate_junction_0"


def test_concentrate_junction_user_sentinel_attrs_do_not_affect_plain_output():
    source = """
        digraph {
          _concentrate_junction_0 [_concentrate_junction_node=true, label=user]
          a -> b [
            label=x,
            _concentrate_junction_internal=true,
            _concentrate_junction_draw_trunk=false,
            _concentrate_junction_arm_splines=0
          ]
        }
    """
    svg = _svg(source)

    assert ">user</text>" in svg
    assert ">x</text>" in svg
    assert svg.count("<path") == 1
    assert len(re.findall(r"<polygon fill=\"black\" stroke=\"black\"", svg)) == 1


def test_concentrate_junction_fanin_keeps_different_map_metadata_separate():
    source = """
        digraph {
          graph [concentrate=true]
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
        if obj.get("name", "").startswith("_concentrate_junction_")
    ]


def test_concentrate_junction_fanin_keeps_different_layers_separate():
    source = """
        digraph {
          graph [concentrate=true, layers="one:two"]
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
        if obj.get("name", "").startswith("_concentrate_junction_")
    ]


def test_concentrate_junction_fanin_caps_multicolor_hidden_trunk():
    source = """
        digraph {
          graph [concentrate=true]
          edge [label=shared, minlen=2, color="red:blue", penwidth=3]
          amber -> target
          violet -> target
        }
    """
    svg = _svg(source)
    layout = _layout(source)

    assert len(_edge_objects(layout, label="shared")) == 2
    assert sum("_ldraw_" in edge for edge in layout["edges"]) == 1
    assert svg.count("<path") == 6


def test_concentrate_junction_fanin_clustered_graph_falls_back():
    source = """
        digraph {
          graph [concentrate=true]
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
        if obj.get("name", "").startswith("_concentrate_junction_")
    ]


def test_concentrate_junction_fanout_clustered_graph_falls_back():
    source = """
        digraph {
          graph [concentrate=true]
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
        if obj.get("name", "").startswith("_concentrate_junction_")
    ]
