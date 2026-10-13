"""Concentrate tests: pinned answers to review findings on the series."""

import json
import os
import subprocess
import sys

import pytest

sys.path.append(os.path.dirname(__file__))
from concentrate_helpers import (  # pylint: disable=wrong-import-position
    Path,
    _drawn_edges,
    _json_layout,
)
from gvtest import which  # pylint: disable=wrong-import-position

FIXTURE = (
    Path(__file__).parent
    / "graphs"
    / "concentrate-demo"
    / "cluster-rank-fallback-renders-2825.dot"
)


def _bezier_points(edge: dict) -> list[list[float]]:
    return [
        point
        for operation in edge.get("_draw_", [])
        if operation["op"] in {"b", "B"}
        for point in operation["points"]
    ]


def test_exact_duplicate_after_borrowed_reverse_arrow_adds_no_lane():
    """b precedes a, so b->a folds first; a later a->b is still a duplicate."""

    edges = _drawn_edges("digraph { concentrate=true; b; a->b; b->a; a->b }")
    assert len(edges) == 1


def test_exact_duplicate_without_reverse_edge_adds_no_lane():
    """Neighbouring case: plain duplicates merge as before."""

    assert len(_drawn_edges("digraph { concentrate=true; a->b; a->b }")) == 1


def test_visibly_different_parallel_edge_keeps_its_own_lane():
    """Negative control: a differently colored parallel edge stays drawn."""

    source = "digraph { concentrate=true; b; a->b [color=red]; a->b [color=blue]; a->b [color=red] }"
    assert len(_drawn_edges(source)) == 2


@pytest.mark.skipif(which("neato") is None, reason="neato not available")
def test_neato_concentrate_draws_every_retained_parallel_edge():
    """Identity filtering keeps red and blue; both must be installed."""

    source = "digraph { concentrate=true; a->b [color=red]; a->b [color=blue] }"
    proc = subprocess.run(
        [which("dot"), "-Kneato", "-Tjson"],
        input=source,
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    layout = json.loads(proc.stdout)
    positioned = [edge for edge in layout["edges"] if edge.get("pos")]
    assert len(positioned) == 2
    assert len({edge["pos"] for edge in positioned}) == 2


def test_flat_both_arrows_outward_ports_keep_the_detour():
    """A west tail port to an east head port must not become a straight chord."""

    source = """
        digraph {
          concentrate=true
          { rank=same; a -> b [dir=both tailport=w headport=e] }
        }
    """
    points = _bezier_points(_drawn_edges(source)[0])
    ys = [point[1] for point in points]
    assert max(ys) - min(ys) > 10


def test_ortho_opposite_vertical_ports_turn_outside_tall_nodes():
    """The first turn is beyond the endpoint box, not a fixed step off the port."""

    source = """
        digraph {
          splines=ortho concentrate=true node [shape=record]
          { rank=same
            a [label="{<t>top|x\\nx\\nx\\nx\\nx\\nx|<b>bot}"]
            b [label="{<t>top|x\\nx\\nx\\nx\\nx\\nx|<b>bot}"]
            a:t -> b:b }
        }
    """
    layout = _json_layout(source)
    nodes = {obj["name"]: obj for obj in layout["objects"] if "pos" in obj}
    top = max(
        float(node["pos"].split(",")[1]) + float(node["height"]) * 72 / 2
        for node in nodes.values()
    )
    points = _bezier_points(_drawn_edges(source)[0])
    assert max(point[1] for point in points) >= top


def test_rollback_probe_failure_matches_default_unconcentrated_fallback():
    """A failed probe must not leave earlier concentration edits committed."""

    def render(env_extra: dict) -> str:
        env = os.environ.copy()
        env.update(env_extra)
        proc = subprocess.run(
            [which("dot"), "-Kdot", "-Txdot", FIXTURE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            env=env,
        )
        assert "fell back to an unconcentrated layout" in proc.stderr
        return proc.stdout

    assert render({"GV_CONCENTRATE_ROLLBACK": "1"}) == render({})


def test_many_parallel_long_edges_do_not_blow_up_rollback_snapshots():
    """Undo snapshots are deduplicated per region (edges x rank span bound)."""

    edges = "\n".join("a -> b [minlen=40]" for _ in range(1500))
    env = os.environ.copy()
    env["GV_CONCENTRATE_ROLLBACK"] = "1"
    proc = subprocess.run(
        [which("dot"), "-Kdot", "-Tdot"],
        input=f"digraph {{ concentrate=true\n{edges}\n}}",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        env=env,
        timeout=120,
    )
    assert "pos=" in proc.stdout
