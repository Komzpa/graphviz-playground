"""Tests for dot's concentrate junction behavior."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import subprocess

from concentrate_helpers import _compile_concentrate_c_test, is_static_build
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


def _node_name(layout, node_id):
    return layout["objects"][int(node_id)]["name"]


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
    assert any(op.get("op") == "E" for op in junctions[0]["_draw_"])
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
    assert any(op.get("op") == "E" for op in junctions[0]["_draw_"])
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
        digraph {
          graph [concentrate=true]
          node [shape=ellipse]
          edge [label=out, minlen=2]
          source -> alpha
          source -> beta
          source -> gamma
          edge [label=in, minlen=2]
          delta -> sink
          epsilon -> sink
          zeta -> sink
        }
    """
    layout = _layout(source)
    xmin, ymin, xmax, ymax = [float(v) for v in layout["bb"].split(",")]
    labelled = [
        edge
        for edge in _edge_objects(layout, label="out")
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
    source = """
        digraph {
          graph [concentrate=true]
          a -> c:port
          b -> c:port
        }
    """
    layout = _layout(source)

    assert not [
        obj
        for obj in layout.get("objects", [])
        if obj.get("_concentrate_junction_node") == "true"
    ]


def test_concentrate_junction_fuses_samehead_groups_on_honda_tokoro():
    fixture = Path(__file__).parents[1] / "graphs" / "directed" / "honda-tokoro.gv"
    layout = _fixture_json(fixture, "-Gconcentrate=true")
    names = {int(obj["_gvid"]): obj["name"] for obj in layout["objects"]}
    junctions = [
        obj for obj in layout["objects"] if obj.get("_concentrate_junction_node") == "true"
    ]
    p1_group = [
        edge
        for edge in layout["edges"]
        if edge.get("_concentrate_junction_original") == "true"
        and edge.get("samehead") == "m000"
    ]
    p2_group = [
        edge
        for edge in layout["edges"]
        if edge.get("_concentrate_junction_original") == "true"
        and edge.get("samehead") == "m001"
    ]

    assert len(junctions) >= 2
    assert {names[int(edge["tail"])] for edge in p1_group} == {"n003", "n005"}
    assert {names[int(edge["tail"])] for edge in p2_group} == {"n007", "n009", "n010"}
    assert sum("_hldraw_" in edge for edge in p1_group) == 1
    assert sum("_hldraw_" in edge for edge in p2_group) == 1


def test_concentrate_junction_allows_labelled_concentrated_fans():
    source = """
        digraph {
          graph [concentrate=true]
          edge [label=shared, minlen=2]
          alpha -> merge
          beta -> merge
          gamma -> merge
          delta -> merge
          edge [label=shared_out, minlen=2]
          split -> one
          split -> two
          split -> three
          split -> four
        }
    """
    layout = _layout(source)
    junctions = [
        obj for obj in layout["objects"] if obj.get("_concentrate_junction_node") == "true"
    ]
    required = {"shared", "shared_out"}
    counts = {
        label: sum(
            1
            for edge in layout["edges"]
            for op in edge.get("_ldraw_", [])
            if op.get("op") == "T" and op.get("text") == label
        )
        for label in required
    }

    assert len(junctions) == 2
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


def test_concentrate_junction_oldarrows_samearrow_fan_renders():
    path = Path(__file__).parents[1] / "graphs" / "directed" / "oldarrows.gv"

    on = _fixture_json(path, "-Gconcentrate=true")
    off = _fixture_json(path, "-Gconcentrate=false")

    def z_fan(layout):
        return [
            edge
            for edge in layout["edges"]
            if _node_name(layout, edge["tail"]) == "Z"
            and _node_name(layout, edge["head"]) in "ABCDEFGHIJKLMNOPQ"
        ]

    on_fan = z_fan(on)
    off_fan = z_fan(off)

    assert len(on_fan) == len(off_fan) == 17
    assert sum("_draw_" in edge for edge in on_fan) == 17
    assert sum("_hdraw_" in edge for edge in on_fan) == sum(
        "_hdraw_" in edge for edge in off_fan
    )
    assert [
        _node_name(on, edge["head"])
        for edge in on_fan
        if "_hdraw_" in edge
    ] == [
        _node_name(off, edge["head"])
        for edge in off_fan
        if "_hdraw_" in edge
    ]


# --- review-driven regressions (PR 251) ----------------------------------


def _plain(source):
    out = dot("plain", source=source)
    return (out.decode() if isinstance(out, bytes) else out).splitlines()


def _junction_nodes(layout):
    return [
        obj
        for obj in layout["objects"]
        if obj.get("_concentrate_junction_node") == "true"
    ]


def _fan(attrs_a="", attrs_b="", attrs_c="", head="z"):
    return (
        "digraph { concentrate=true;"
        f" a -> {head} [{attrs_a}]; b -> {head} [{attrs_b}]; c -> {head} [{attrs_c}]; }}"
    )


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_junction_helpers_are_removed_by_gvfreelayout(tmp_path):
    exe, env = _compile_concentrate_c_test(
        tmp_path, "concentrate_junction_relayout.c", "concentrate-junction-relayout"
    )
    subprocess.run((exe,), capture_output=True, env=env, check=True)


def test_concentrate_junction_keeps_requested_minlen():
    def rank_gap(concentrate):
        source = (
            f"digraph {{ concentrate={concentrate}; "
            "a -> z [minlen=3]; b -> z [minlen=3]; }"
        )
        ys = {}
        for line in _plain(source):
            fields = line.split()
            if fields[:1] == ["node"] and fields[1] in ("a", "z"):
                ys[fields[1]] = float(fields[3])
        return ys["a"] - ys["z"]

    # Without the fix the split edge spans only two ranks (about 1.3in here,
    # against 2.0in for three ranks).
    assert rank_gap("true") >= rank_gap("false") - 0.1


@pytest.mark.parametrize(
    "attr_a,attr_b",
    [
        ("headclip=false", "headclip=true"),
        ('colorscheme=blues9 color=3', 'colorscheme=reds9 color=3'),
        ("fillcolor=red", "fillcolor=blue"),
        ("class=one", "class=two"),
        ('label="\\T"', 'label="\\T"'),
    ],
)
def test_concentrate_junction_does_not_merge_distinct_edge_identity(attr_a, attr_b):
    source = f"digraph {{ concentrate=true; a -> z [{attr_a}]; b -> z [{attr_b}]; }}"
    layout = _layout(source)
    assert not _junction_nodes(layout)


def test_concentrate_junction_refuses_tapered_edges():
    layout = _layout(_fan("style=tapered", "style=tapered", "style=tapered"))
    assert not _junction_nodes(layout)


def test_concentrate_junction_marker_follows_invis_style():
    svg = _svg(_fan("style=invis", "style=invis", "style=invis"))
    assert "<title>_concentrate_junction_" not in svg
    # Negative control: the same fan with visible edges does draw the marker.
    assert "<title>_concentrate_junction_" in _svg(_fan())


def test_concentrate_junction_marker_follows_layer():
    source = (
        'digraph { concentrate=true; layers="one:two";'
        " a -> z [layer=one]; b -> z [layer=one]; }"
    )
    junctions = _junction_nodes(_layout(source))
    assert junctions
    assert all(obj.get("layer") == "one" for obj in junctions)


def test_concentrate_junction_marker_follows_colorscheme():
    source = _fan("colorscheme=blues9 color=3", "colorscheme=blues9 color=3",
                  "colorscheme=blues9 color=3")
    junctions = _junction_nodes(_layout(source))
    assert junctions
    assert all(obj.get("colorscheme") == "blues9" for obj in junctions)


def test_concentrate_junction_user_node_named_like_junction_survives_pack():
    source = (
        "digraph { concentrate=true; pack=true; _concentrate_junction_0;"
        " a -> z; b -> z; }"
    )
    layout = _layout(source)
    names = {int(obj["_gvid"]): obj["name"] for obj in layout["objects"]}
    user = [gvid for gvid, name in names.items() if name == "_concentrate_junction_0"]
    assert len(user) == 1
    assert not [
        edge
        for edge in layout["edges"]
        if user[0] in (int(edge["tail"]), int(edge["head"]))
    ]


def test_concentrate_junction_does_not_stop_legacy_concentration_elsewhere():
    source = "digraph { concentrate=true; a -> z; b -> z; c -> z; p -> q; p -> q; }"
    plain = _plain(source)
    assert any(line.startswith("node _concentrate_junction_") for line in plain)
    assert sum(1 for line in plain if line.startswith("edge p q ")) == 1


def test_concentrate_junction_leaves_explicit_labeldistance_on_lone_edge():
    def head_label_pos(concentrate):
        source = (
            f"digraph {{ concentrate={concentrate}; a -> b [headlabel=h labeldistance=2]; }}"
        )
        edge = _layout(source)["edges"][0]
        return edge["head_lp"]

    assert head_label_pos("true") == head_label_pos("false")


def test_concentrate_junction_sameport_dir_none_helpers_carry_no_arrow():
    source = (
        "digraph { concentrate=true; edge [dir=none samearrowhead=1];"
        ' a -> z [samehead=m]; b -> z [samehead=m]; }'
    )
    layout = _layout(source)
    assert _junction_nodes(layout)
    assert not [edge for edge in layout["edges"] if "_hdraw_" in edge]


def test_concentrate_junction_sameport_outer_label_is_not_fused():
    source = (
        "digraph { concentrate=true; samehead=m;"
        ' a -> z [samehead=m samearrowhead=1 taillabel=t];'
        ' b -> z [samehead=m samearrowhead=1 taillabel=u]; }'
    )
    assert not _junction_nodes(_layout(source))


def _arrow_tips_near(source, target_of_node, node="e"):
    """Distance from each arrowhead polygon's nearest vertex to a node-relative target."""
    layout = _layout(source)
    obj = next(o for o in layout["objects"] if o["name"] == node)
    px, py = (float(v) for v in obj["pos"].split(","))
    width = float(obj["width"]) * 72.0
    height = float(obj["height"]) * 72.0
    tx, ty = target_of_node(px, py, width, height)
    distances = []
    for edge in layout["edges"]:
        points = [
            pt
            for op in edge.get("_hdraw_", [])
            if op.get("op") == "P"
            for pt in op["points"]
        ]
        assert points
        distances.append(min(math.hypot(x - tx, y - ty) for x, y in points))
    return distances


def test_arrow_spread_keeps_explicit_compass_port_tips_fixed():
    source = "digraph { e [shape=ellipse]; a -> e:w; b -> e:w; c -> e:w; }"
    distances = _arrow_tips_near(source, lambda x, y, w, h: (x - w / 2.0, y))
    # Spreading would pull the three tips apart by several points.
    assert max(distances) - min(distances) < 1.0


def test_arrow_spread_keeps_unclipped_tips_inside_the_node():
    source = (
        "digraph { e [shape=ellipse];"
        " a -> e [headclip=false]; b -> e [headclip=false]; c -> e [headclip=false]; }"
    )
    distances = _arrow_tips_near(source, lambda x, y, w, h: (x, y))
    assert max(distances) - min(distances) < 1.0


def test_arrow_spread_on_box_lands_on_the_outline():
    source = "digraph { e [shape=box]; a -> e; b -> e; c -> e; d -> e; }"
    layout = _layout(source)
    obj = next(o for o in layout["objects"] if o["name"] == "e")
    px, py = (float(v) for v in obj["pos"].split(","))
    half_w = float(obj["width"]) * 36.0
    half_h = float(obj["height"]) * 36.0
    for edge in layout["edges"]:
        points = [
            pt
            for op in edge.get("_hdraw_", [])
            if op.get("op") == "P"
            for pt in op["points"]
        ]
        tip = min(
            points,
            key=lambda p: min(abs(abs(p[0] - px) - half_w), abs(abs(p[1] - py) - half_h)),
        )
        on_vertical = abs(abs(tip[0] - px) - half_w) < 2.0 and abs(tip[1] - py) <= half_h + 2.0
        on_horizontal = abs(abs(tip[1] - py) - half_h) < 2.0 and abs(tip[0] - px) <= half_w + 2.0
        assert on_vertical or on_horizontal


def test_arrow_spread_tolerates_epsf_nodes(tmp_path):
    eps = tmp_path / "shape.eps"
    eps.write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 20 20\n"
        "newpath 0 0 moveto 20 20 lineto stroke\n"
    )
    source = (
        f'digraph {{ e [shape=epsf shapefile="{eps}"]; a -> e; b -> e; c -> e; }}'
    )
    assert sum(line.startswith("edge ") for line in _plain(source)) == 3


def test_arrow_spread_leaves_ortho_routes_axis_aligned():
    source = (
        "digraph { splines=ortho; e [shape=box]; a -> e; b -> e; c -> e; d -> e; }"
    )
    for edge in _layout(source)["edges"]:
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            points = op["points"]
            for p, q in zip(points, points[1:]):
                assert abs(p[0] - q[0]) < 1e-3 or abs(p[1] - q[1]) < 1e-3


def test_ordinary_overlapping_endpoint_labels_are_still_separated():
    source = (
        "digraph { a -> b [headlabel=XXXXXXXX]; a -> b [headlabel=YYYYYYYY]; }"
    )
    labels = [edge["head_lp"] for edge in _layout(source)["edges"]]
    assert labels[0] != labels[1]


def test_thick_record_port_edge_still_routes():
    source = (
        "digraph { node [shape=record]; a [label=\"<p>x\"]; b [label=\"<p>y\"];"
        " a:p:s -> b:p:s [penwidth=60]; }"
    )
    assert len(_layout(source)["edges"]) == 1
