"""Concentrate tests: Same-rank reverse edges, flat-route arrowheads, physical endpoints, record ports, and suppressed representatives."""

import pytest

from concentrate_helpers import (
    _SAME_RANK_REVERSE_EDGES,
    _arrow_polygon_point_count,
    _arrowhead_shaft_angle,
    _assert_compass_attachment,
    _assert_endpoint_departure,
    _boxes_are_disjoint,
    _concentrated_graph,
    _drawn_edge_between,
    _drawn_edge_colors,
    _drawn_edges,
    _edge_arrow_polygon,
    _edge_bezier_points,
    _edge_physical_endpoint,
    _ellipse,
    _point_box,
    _samehead_mixed_route_fixture,
    _sametail_mixed_route_fixture,
    dot,
    itertools,
    json,
    math,
)

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


def test_concentrate_flat_bidirectional_arrows_stay_between_nodes():
    """A short merged flat route stays between endpoints with both arrows."""

    source = _concentrated_graph(
        "",
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [headlabel=x]",
        "b -> a [taillabel=x]",
    )
    layout = json.loads(dot("json", source=source))
    edge = next(edge for edge in layout["edges"] if "_draw_" in edge)
    assert len([edge for edge in layout["edges"] if "_draw_" in edge]) == 1

    objects = {node["_gvid"]: node for node in layout["objects"] if "pos" in node}
    tail = objects[edge["tail"]]
    head = objects[edge["head"]]
    tail_bottom = float(tail["pos"].split(",")[1]) - float(tail["height"]) * 36
    tail_top = float(tail["pos"].split(",")[1]) + float(tail["height"]) * 36
    head_bottom = float(head["pos"].split(",")[1]) - float(head["height"]) * 36
    head_top = float(head["pos"].split(",")[1]) + float(head["height"]) * 36
    low = max(tail_bottom, head_bottom)
    high = min(tail_top, head_top)
    assert all(low <= point[1] <= high for point in _edge_bezier_points(edge))

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
