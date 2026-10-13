"""Concentrate tests: Compiled C harnesses, cleanup/rollback behavior, and unconcentrated routing guards."""

import pytest

from concentrate_helpers import (
    Path,
    _assert_distinct_drawn_edge_routes,
    _compile_concentrate_c_test,
    _compile_concentrate_edge_identity_tooltip_test,
    _concentrated_graph,
    _drawn_edge_colors,
    _drawn_edges,
    is_static_build,
    json,
    subprocess,
    which,
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
