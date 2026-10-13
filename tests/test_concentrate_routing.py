"""Concentrate tests: Samehead/sametail anchors, route-quality geometry, shared-trunk reuse, and exported edge-helper surface."""

import pytest

from concentrate_helpers import (
    Path,
    _BACKWARD_PARALLEL_COLORS_FIXTURE,
    _CONCENTRATE_ABSTRACT_HORIZONTAL_BUS_MINIMIZED,
    _CONCENTRATE_ROWE_SPINE_BEND_MINIMIZED,
    _SHARED_TRUNK_DISTINCT_ORDERS,
    _arrowhead_shaft_angle,
    _assert_concentrate_keeps_zero_crossings,
    _assert_endpoint_departure,
    _assert_regular_g1_piece_join,
    _concentrated_graph,
    _crossing_angle,
    _drawn_edge_arc_length,
    _drawn_edge_between,
    _drawn_edge_color,
    _drawn_edge_piece_end_gaps,
    _drawn_edge_spline_point_count,
    _drawn_edges,
    _drawn_edges_between,
    _edge_bezier_points,
    _graph_with_concentrate,
    _group_anchor_points,
    _long_horizontal_edge_count,
    _max_pointwise_route_distance,
    _named_drawn_cubic_geometry,
    _normalized_longest_path_drift,
    _polygon_self_intersections,
    _samehead_mixed_route_fixture,
    _sametail_mixed_route_fixture,
    _sample_bezier_points,
    _sampled_edge_crossing_count,
    _shared_trunk_source,
    _tangential_touching_pair_count,
    dot,
    json,
    math,
    re,
    run,
    which,
)

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


def test_concentrate_abstract_minimized_has_no_horizontal_bus():
    """Concentration should not create long flat bus segments."""

    layout = json.loads(dot("json", source=_CONCENTRATE_ABSTRACT_HORIZONTAL_BUS_MINIMIZED))
    assert _long_horizontal_edge_count(layout) < 3


def test_concentrate_rowe_minimized_spine_does_not_bend():
    """Concentration should not collapse a long spine into an S-bend."""

    layout = json.loads(dot("json", source=_CONCENTRATE_ROWE_SPINE_BEND_MINIMIZED))
    assert _normalized_longest_path_drift(layout) <= 0.733


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
