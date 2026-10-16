"""Demonstrative concentrate regression tests backed by synthetic fixtures."""

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.append(os.path.dirname(__file__))
from gvtest import dot, which  # pylint: disable=wrong-import-position


FIXTURE_DIR = Path(__file__).parent / "graphs" / "concentrate-demo"


def _fixture(name: str) -> Path:
    path = FIXTURE_DIR / name
    assert path.exists(), f"missing concentrate demo fixture {path}"
    return path


def _render_xdot_json(path: Path) -> dict:
    """Render a fixture once and parse Graphviz's xdot JSON draw streams."""

    return json.loads(dot("json", source_file=path))


def _render_json_with_args(path: Path, extra_args: list[str]) -> dict:
    proc = subprocess.run(
        ["dot", "-Kdot", "-Tjson", *extra_args, path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _run_xdot(path: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [which("dot"), "-Kdot", "-Txdot", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
    )


def _node_names(layout: dict) -> dict[int, str]:
    return {node["_gvid"]: node["name"] for node in layout["objects"]}


def _drawn_edges(layout: dict) -> list[dict]:
    return [edge for edge in layout["edges"] if "_draw_" in edge]


def _drawn_edges_between(
    layout: dict, tails: set[str], head: str | None = None
) -> list[dict]:
    names = _node_names(layout)
    return [
        edge
        for edge in _drawn_edges(layout)
        if names[edge["tail"]] in tails
        and (head is None or names[edge["head"]] == head)
    ]


def _drawn_edge_color(edge: dict) -> str:
    return next(op["color"] for op in edge["_draw_"] if op["op"] == "c")


def _bezier_pieces(edge: dict) -> list[list[list[float]]]:
    return [op["points"] for op in edge["_draw_"] if op["op"] in {"b", "B"}]


def _bezier_signature(edge: dict) -> tuple:
    return tuple(
        tuple(tuple(point) for point in piece) for piece in _bezier_pieces(edge)
    )


def _arrow_polygons(edge: dict, stream: str) -> list[dict]:
    return [op for op in edge.get(stream, []) if op["op"] in {"P", "p"}]


def _edge_label_ops(edge: dict) -> list[dict]:
    return [op for op in edge.get("_ldraw_", []) if op["op"] == "T"]


def _visible_edge_label_count(layout: dict) -> int:
    return sum(
        len(_edge_label_ops(edge))
        for edge in _drawn_edges(layout)
        if edge.get("_concentrate_junction_internal") != "true"
    )


def _graph_width(layout: dict) -> float:
    left, _bottom, right, _top = (float(value) for value in layout["bb"].split(","))
    return right - left


def _route_bbox(edge: dict) -> tuple[float, float, float, float]:
    points = [point for piece in _bezier_pieces(edge) for point in piece]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _node_box(node: dict) -> tuple[float, float, float, float]:
    x, y = (float(value) for value in node["pos"].split(","))
    half_width = float(node["width"]) * 72 / 2
    half_height = float(node["height"]) * 72 / 2
    return x - half_width, y - half_height, x + half_width, y + half_height


def _route_distance(left: dict, right: dict) -> float:
    left_points = [point for piece in _bezier_pieces(left) for point in piece]
    right_points = [point for piece in _bezier_pieces(right) for point in piece]
    return max(
        math.dist(a, b)
        for a, b in zip(left_points, right_points)
    )


def _sample_cubic(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1.0 - t
    return (
        u * u * u * p0[0]
        + 3 * u * u * t * p1[0]
        + 3 * u * t * t * p2[0]
        + t * t * t * p3[0],
        u * u * u * p0[1]
        + 3 * u * u * t * p1[1]
        + 3 * u * t * t * p2[1]
        + t * t * t * p3[1],
    )


def _sampled_drawn_turns(layout: dict) -> tuple[int, float]:
    turns = 0
    worst = 0.0
    for edge in _drawn_edges(layout):
        polyline = []
        for piece in _bezier_pieces(edge):
            for index in range(0, len(piece) - 3, 3):
                segment = [
                    _sample_cubic(
                        piece[index],
                        piece[index + 1],
                        piece[index + 2],
                        piece[index + 3],
                        step / 24,
                    )
                    for step in range(25)
                ]
                if polyline:
                    segment = segment[1:]
                polyline.extend(segment)
        for index in range(1, len(polyline) - 1):
            incoming = (
                polyline[index][0] - polyline[index - 1][0],
                polyline[index][1] - polyline[index - 1][1],
            )
            outgoing = (
                polyline[index + 1][0] - polyline[index][0],
                polyline[index + 1][1] - polyline[index][1],
            )
            incoming_len = math.hypot(*incoming)
            outgoing_len = math.hypot(*outgoing)
            if incoming_len < 1e-6 or outgoing_len < 1e-6:
                continue
            cosine = max(
                -1.0,
                min(
                    1.0,
                    (
                        incoming[0] * outgoing[0]
                        + incoming[1] * outgoing[1]
                    )
                    / (incoming_len * outgoing_len),
                ),
            )
            angle = math.degrees(math.acos(cosine))
            worst = max(worst, angle)
            turns += angle > 35.0
    return turns, worst


def test_distinct_parallel_colors_stay_distinct():
    """Two rendered-distinct parallel edges remain two visible colored routes."""

    layout = _render_xdot_json(_fixture("distinct-parallel-colors-stay-distinct.dot"))
    edges = _drawn_edges_between(layout, {"a"}, "b")

    assert len(edges) == 2
    assert {_drawn_edge_color(edge) for edge in edges} == {"#ff0000", "#0000ff"}
    assert len({_bezier_signature(edge) for edge in edges}) == 2


def test_reverse_arrowhead_preserved():
    """A retained reverse edge keeps its own arrow polygon at its tail end."""

    layout = _render_xdot_json(_fixture("reverse-arrowhead-preserved.dot"))
    names = _node_names(layout)
    reverse = [
        edge
        for edge in _drawn_edges(layout)
        if names[edge["tail"]] == "b" and names[edge["head"]] == "a"
    ]

    assert len(reverse) == 1
    assert _drawn_edge_color(reverse[0]) == "#0000ff"
    assert len(_arrow_polygons(reverse[0], "_tdraw_")) == 1
    assert not _arrow_polygons(reverse[0], "_hdraw_")


def test_distinct_shared_trunk_siblings_separate():
    """
    Distinct sibling routes beside a would-be shared trunk render as separate lanes.

    The plain {a,b}->d Y-junction is not the feature regression: upstream base
    already draws that. The branch guarantee is that rendered-distinct siblings
    are promoted out of the concentrated trunk into complete, separated lanes.
    """

    layout = _render_xdot_json(_fixture("distinct-shared-trunk-siblings-separate.dot"))
    colored = [
        edge
        for edge in _drawn_edges_between(layout, {"a", "b"}, "d")
        if _drawn_edge_color(edge) in {"#0000ff", "#ff0000"}
    ]

    assert len(colored) == 2
    assert {_drawn_edge_color(edge) for edge in colored} == {"#0000ff", "#ff0000"}
    assert all(len(_bezier_pieces(edge)) == 1 for edge in colored)
    assert all(len(_arrow_polygons(edge, "_hdraw_")) == 1 for edge in colored)
    assert _route_distance(colored[0], colored[1]) > 3


def test_same_rank_equivalent_edges_still_concentrate():
    """Equivalent same-rank reverse edges collapse to one route with two arrows."""

    layout = _render_xdot_json(_fixture("same-rank-equivalent-edges-concentrate.dot"))
    edges = _drawn_edges(layout)

    assert len(edges) == 1
    assert len(_arrow_polygons(edges[0], "_hdraw_")) == 1
    assert len(_arrow_polygons(edges[0], "_tdraw_")) == 1
    assert len(_bezier_pieces(edges[0])) == 1


def test_same_rank_equivalent_edges_route_between_endpoints():
    """A concentrated same-rank reverse pair remains a flat between-node edge."""

    fixture = _fixture("same-rank-equivalent-edges-concentrate.dot")
    for extra_args in ([], ["-Gnewrank=true"]):
        layout = _render_json_with_args(fixture, extra_args)
        objects = {node["name"]: node for node in layout["objects"] if "pos" in node}
        low = max(_node_box(objects["a"])[1], _node_box(objects["b"])[1])
        high = min(_node_box(objects["a"])[3], _node_box(objects["b"])[3])
        edges = _drawn_edges(layout)
        points = [point for piece in _bezier_pieces(edges[0]) for point in piece]

        assert len(edges) == 1
        assert len(_arrow_polygons(edges[0], "_hdraw_")) == 1
        assert len(_arrow_polygons(edges[0], "_tdraw_")) == 1
        assert points
        assert all(low <= point[1] <= high for point in points)


def test_malformed_nodesep_rejected_without_oversized_canvas():
    """Out-of-range layout separation is rejected with a small diagnostic xdot."""

    proc = _run_xdot(_fixture("malformed-nodesep-rejected-2758.dot"))

    assert proc.returncode == 1
    assert "nodesep" in proc.stderr
    assert "AddressSanitizer" not in proc.stderr
    match = re.search(r'\bbb="([^"]+)"', proc.stdout)
    if match is not None:
        left, bottom, right, top = (
            float(value) for value in match.group(1).split(",")
        )
        assert right - left < 10000
        assert top - bottom < 10000


def test_record_port_concentrate_crash_repro_renders():
    """Malformed record-port concentrate input renders instead of segfaulting."""

    proc = _run_xdot(_fixture("crash-record-port-concentrate-2764.dot"))

    assert proc.returncode == 0
    assert "SEGV" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr
    assert re.search(r"\w+\s*->\s*\w+\s+\[", proc.stdout) is not None
    assert "pos=" in proc.stdout


def test_concentrated_trunk_corner_fixture_is_smooth():
    """A concentrated trunk route has no sampled drawn turn above 35 degrees."""

    layout = _render_xdot_json(_fixture("trunk-corner-anonymous-state.dot"))
    turns, worst = _sampled_drawn_turns(layout)

    assert turns == 0
    assert worst < 35.0


def test_rollback_probe_exercises_successful_shared_transaction_path():
    """Opt-in rollback probing validates a successful concentration candidate."""

    env = os.environ.copy()
    env["GV_CONCENTRATE_ROLLBACK"] = "1"
    proc = _run_xdot(_fixture("distinct-parallel-colors-stay-distinct.dot"), env=env)

    assert proc.returncode == 0
    assert "SEGV" not in proc.stderr
    assert "pos=" in proc.stdout


def test_cluster_rank_fallback_renders():
    """Cluster/rank rebuild fallback keeps rendering edge splines."""

    proc = _run_xdot(_fixture("cluster-rank-fallback-renders-2825.dot"))

    assert proc.returncode == 0
    assert "SEGV" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr
    assert "degenerate concentrated rank" in proc.stderr
    assert "pos=" in proc.stdout


def test_labelled_cyclic_fans_keep_label_space():
    """Cyclic labelled fans do not collapse into one narrow junction stack."""

    fixture = _fixture("labelled-cyclic-fans-keep-label-space.dot")
    off = _render_json_with_args(fixture, ["-Gconcentrate=false"])
    on = _render_json_with_args(fixture, ["-Gconcentrate=true"])

    assert _visible_edge_label_count(on) >= _visible_edge_label_count(off)
    assert _graph_width(on) >= 0.9 * _graph_width(off)


def test_self_loop_label_sits_beside_loop():
    """A deduped self-loop label sits beside the retained loop, not on the line."""

    layout = _render_xdot_json(_fixture("self-loop-label-beside-loop.dot"))
    edges = _drawn_edges(layout)

    assert len(edges) == 1
    labels = _edge_label_ops(edges[0])
    assert len(labels) == 1
    left, bottom, right, top = _route_bbox(edges[0])
    label = labels[0]
    label_x, label_y = label["pt"]
    label_width = float(label["width"])
    label_height = 14.0

    assert label_x - label_width / 2 >= right + 10
    assert label_y + label_height / 2 >= bottom
    assert label_y - label_height / 2 <= top
    assert label_x > right
