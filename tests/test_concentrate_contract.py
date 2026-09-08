"""Concentrate tests: Contract, metamorphic invariants, oracle trace, and public crash repros."""

import pytest

from concentrate_helpers import (
    Path,
    _assert_concentrate_route_gain,
    _assert_public_concentrate_crash_repro_renders,
    _concentrated_graph,
    _drawn_edges,
    _drawn_route_topology,
    _graph_with_concentrate,
    _set_graph_concentrate,
    dot,
    json,
    os,
    subprocess,
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
        [which("dot"), "-Txdot", source],
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


def test_concentration_plan_reports_compatible_subgroups(tmp_path: Path):
    """Diagnostics should keep later compatible edges visible after a mismatch."""

    source = tmp_path / "plan-subgroups.dot"
    source.write_text(
        """
        digraph {
          graph [concentrate=true]
          a -> b [color=red]
          a -> b [color=blue]
          a -> b [color=blue]
        }
        """,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["GV_CONCENTRATION_PLAN_DIAGNOSTICS"] = "1"
    completed = subprocess.run(
        [which("dot"), "-Txdot", source],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert "\trepresentative=0:a->b\tmembers=1:same,2:same" in completed.stderr
    assert "\tverdict=share-route-only" in completed.stderr
    assert "\trepresentative=1:a->b\tmembers=2:same\tverdict=suppressible" in completed.stderr


def test_concentrate_issue_2764_public_repro_renders_edge_splines():
    """GitLab #2764: raw public conc_slope crash repro still renders edges."""

    _assert_public_concentrate_crash_repro_renders(2764)


def test_concentrate_issue_2765_public_repro_renders_edge_splines():
    """GitLab #2765: raw public straight_len crash repro still renders edges."""

    _assert_public_concentrate_crash_repro_renders(2765)
