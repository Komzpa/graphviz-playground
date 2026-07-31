#!/usr/bin/env python3
"""Verify concentrate=true still draws the recorded public fixture fuses."""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 60


@dataclass(frozen=True)
class Baseline:
    graph: str
    junctions: int
    arrowheads: int


BASELINES = (
    Baseline("graphs/directed/fig6.gv", 19, 42),
    Baseline("graphs/directed/awilliams.gv", 23, 97),
    Baseline("tests/junction-fanin.dot", 1, 1),
    Baseline("tests/junction-fanout.dot", 1, 4),
    Baseline(
        "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot",
        1,
        5,
    ),
    Baseline("tests/graphs/concentrate-demo/nonlocal-arm-detour.dot", 6, 4),
    # A recorded refusal. nhg.gv is a labelled cyclic fan: fusing its arms left
    # the labels nowhere to go and the drawing collapsed, so the transform now
    # declines it and 0 is the correct, deliberate count. It is listed here for
    # the same reason the others are -- so that a later change cannot quietly
    # move it, in either direction, without this file being edited on purpose.
    Baseline("tests/graphs/nhg.gv", 0, 6),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        timeout=TIMEOUT,
        preexec_fn=cap_memory,
    )


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        subprocess.run(
            ["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"],
            cwd=root,
            check=True,
            timeout=TIMEOUT,
        )
    return dot


def is_junction_node(node: dict) -> bool:
    return (
        node.get("_concentrate_junction_node") == "true"
        or node.get("name", "").startswith("_concentrate_junction_")
    )


def arrowhead_count(edge: dict) -> int:
    return sum(
        1
        for stream in ("_hdraw_", "_tdraw_")
        for op in edge.get(stream, [])
        if op.get("op") in ("P", "p")
    )


def render(dot: Path, root: Path, graph: str) -> dict:
    proc = run([str(dot), "-Gconcentrate=true", "-Tjson", graph], root)
    return json.loads(proc.stdout)


def measure(layout: dict) -> tuple[int, int]:
    junctions = sum(1 for node in layout.get("objects", []) if is_junction_node(node))
    arrowheads = sum(arrowhead_count(edge) for edge in layout.get("edges", []))
    return junctions, arrowheads


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, help="dot binary to test")
    args = parser.parse_args()

    root = repo_root()
    dot = args.dot or current_dot(root)
    failures = 0

    print("| graph | junctions | baseline | arrowheads | baseline | status |")
    print("| --- | ---: | ---: | ---: | ---: | --- |")
    for baseline in BASELINES:
        layout = render(dot, root, baseline.graph)
        junctions, arrowheads = measure(layout)
        ok = junctions >= baseline.junctions and arrowheads >= baseline.arrowheads
        status = "ok" if ok else "FAIL"
        print(
            f"| {baseline.graph} | {junctions} | {baseline.junctions} | "
            f"{arrowheads} | {baseline.arrowheads} | {status} |"
        )
        if not ok:
            failures += 1

    if failures:
        print(f"FAIL verify_concentration_still_concentrates: failures={failures}")
        return 1
    print("OK verify_concentration_still_concentrates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
