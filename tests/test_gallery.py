"""
Smoke-test the checked-in sample graph gallery.

These tests exercise the graph sources shipped from `graphs/` so CI notices
when one of the examples stops rendering.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.dirname(__file__))
from gvtest import run_raw  # pylint: disable=wrong-import-position


GALLERY_ROOT = Path(__file__).resolve().parent.parent / "graphs"


def gallery_cases() -> list[object]:
    """collect graph gallery render cases"""

    directed = sorted((GALLERY_ROOT / "directed").glob("*.gv"))
    undirected = sorted((GALLERY_ROOT / "undirected").glob("*.gv"))

    return [
        pytest.param("dot", graph, id=f"directed/{graph.name}") for graph in directed
    ] + [
        pytest.param("neato", graph, id=f"undirected/{graph.name}")
        for graph in undirected
    ]


@pytest.mark.parametrize("engine,graph", gallery_cases())
def test_gallery_graph_renders_to_svg(engine: str, graph: Path):
    """
    sample gallery graphs should render without errors
    https://gitlab.com/graphviz/graphviz/-/issues/1766
    """

    svg = run_raw("dot", f"-K{engine}", "-Tsvg", graph)

    assert svg.startswith(b"<?xml"), "SVG output is missing its XML declaration"
    assert b"<svg " in svg, "SVG output is missing its root element"
