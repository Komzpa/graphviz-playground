#!/usr/bin/env python3
"""Smoke checks for the junction prototype."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "contrib" / "junction-prototype" / "junctionize.py"


def run_junctionize(path: Path, *extra: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(TOOL), str(path), *extra],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def assert_passthrough(path: Path, *extra: str) -> None:
    original = path.read_bytes()
    proc = run_junctionize(path, *extra)
    if proc.stdout != original:
        raise AssertionError(f"refusal was not byte-exact for {path}: {proc.stderr!r}")
    if not proc.stderr.startswith(b"refused:"):
        raise AssertionError(f"expected refusal for {path}, got stderr={proc.stderr!r}")


def main() -> int:
    assert_passthrough(ROOT / "graphs" / "directed" / "train11.gv")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixtures = {
            "undirected": "graph G {\n  a -- b;\n}\n",
            "strict": "strict digraph G {\n  a -> b;\n}\n",
            "self-loop": "digraph G {\n  a -> a;\n}\n",
            "port": 'digraph G {\n  a [shape=record,label="<p> p"];\n  a:p -> b;\n}\n',
            "constraint-false": "digraph G {\n  a -> b [constraint=false];\n}\n",
            "different-clusters": (
                "digraph G {\n"
                "  subgraph cluster_0 { a; }\n"
                "  subgraph cluster_1 { b; }\n"
                "  a -> b;\n"
                "}\n"
            ),
            "true-multiedge": "digraph G {\n  a -> b;\n  a -> b;\n}\n",
            "rank-straddles-anchor": (
                "digraph G {\n"
                "  { rank=same; a; b; }\n"
                "  a -> b;\n"
                "  a -> c;\n"
                "}\n"
            ),
            "overlaps-selected-group": (
                "digraph G {\n"
                "  { rank=same; a; d; }\n"
                "  { rank=same; b; c; }\n"
                "  a -> b;\n"
                "  a -> c;\n"
                "  d -> b;\n"
                "}\n"
            ),
        }
        for name, text in fixtures.items():
            path = root / f"{name}.gv"
            path.write_text(text, encoding="utf-8")
            assert_passthrough(path)

        rank_read = root / "rank-read-failed.gv"
        rank_read.write_text("digraph G {\n  a -> b;\n  a -> c;\n}\n", encoding="utf-8")
        assert_passthrough(rank_read, "--dot", str(root / "missing-dot"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
