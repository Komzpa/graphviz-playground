"""Concentrate tests: Rendered edge identity: colors, styles, labels, HTML labels, URLs, tooltips, classes, and xlabels."""

import pytest

from concentrate_helpers import (
    ET,
    Path,
    _SAME_RANK_REVERSE_EDGES,
    _arrow_fill_color,
    _arrow_polygon_point_count,
    _assert_concentrated_edge_counts,
    _assert_distinct_drawn_edge_routes,
    _concentrated_graph,
    _drawn_edge_colors,
    _drawn_edge_styles,
    _drawn_edges,
    _drawn_edges_by_color,
    _edge_count_case,
    _edge_label_texts,
    _edge_physical_endpoint,
    _endpoint_label_detach_honda_score,
    _fixed_edge_count_cases,
    _named_edge_count_cases,
    dot,
    json,
    math,
    operator,
    run,
)

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
