#!/usr/bin/env python3
"""Verify concentrate junctions stay inside their incident endpoint rank band."""

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
EPSILON = 0.01

PUBLIC_GRAPHS = (
    "graphs/directed/awilliams.gv",
    "tests/junction-fanin.dot",
    "tests/junction-fanout.dot",
    "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot",
    "tests/graphs/concentrate-demo/nonlocal-arm-detour.dot",
    "tests/graphs/concentrate-demo/junction-rank-band-escape.dot",
)


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class BandRow:
    graph: str
    junction: str
    endpoint_count: int
    y: float
    band_min: float
    band_max: float
    ok: bool


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


def parse_pos(value: str) -> Point:
    x, y = value.split(",", 1)
    return Point(float(x), float(y))


def is_junction_name(name: str) -> bool:
    return name.startswith("_concentrate_junction_") or name.startswith("__junction")


def is_junction_node(node: dict) -> bool:
    return (
        is_junction_name(node.get("name", ""))
        or node.get("_concentrate_junction_node") == "true"
    )


def render_json(dot: Path, graph: Path, root: Path) -> dict:
    cp = run([str(dot), "-Gconcentrate=true", "-Tjson", str(graph)], root)
    return json.loads(cp.stdout)


def endpoint_names(layout: dict, junction_name: str) -> set[str]:
    names = {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}
    endpoints: set[str] = set()
    for edge in layout.get("edges", []):
        if edge.get("_concentrate_junction_internal") != "true":
            continue
        tail = names[int(edge["tail"])]
        head = names[int(edge["head"])]
        if tail == junction_name and not is_junction_name(head):
            endpoints.add(head)
        if head == junction_name and not is_junction_name(tail):
            endpoints.add(tail)
    return endpoints


def band_rows(layout: dict, graph_name: str) -> list[BandRow]:
    nodes = {obj["name"]: obj for obj in layout.get("objects", []) if "name" in obj}
    rows: list[BandRow] = []
    for node in layout.get("objects", []):
        if "pos" not in node or not is_junction_node(node):
            continue
        endpoints = endpoint_names(layout, node["name"])
        if not endpoints:
            rows.append(
                BandRow(graph_name, node["name"], 0, parse_pos(node["pos"]).y, 0.0, 0.0, False)
            )
            continue
        ys = [parse_pos(nodes[name]["pos"]).y for name in endpoints]
        y = parse_pos(node["pos"]).y
        band_min = min(ys)
        band_max = max(ys)
        ok = band_min - EPSILON <= y <= band_max + EPSILON
        rows.append(BandRow(graph_name, node["name"], len(endpoints), y, band_min, band_max, ok))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, help="dot binary to test")
    parser.add_argument("graphs", nargs="*", help="graph paths relative to the repo root")
    args = parser.parse_args()

    root = repo_root()
    dot = args.dot or current_dot(root)
    graphs = args.graphs or list(PUBLIC_GRAPHS)
    all_rows: list[BandRow] = []

    print("| graph | junction | endpoints | y | band_min | band_max | status |")
    print("| --- | --- | ---: | ---: | ---: | ---: | --- |")
    for graph_name in graphs:
        graph = root / graph_name
        layout = render_json(dot, graph, root)
        rows = band_rows(layout, graph_name)
        if not rows:
            print(f"| {graph_name} | <none> | 0 | n/a | n/a | n/a | no-junction |")
            continue
        all_rows.extend(rows)
        for row in rows:
            status = "ok" if row.ok else "FAIL"
            print(
                f"| {row.graph} | {row.junction} | {row.endpoint_count} | "
                f"{row.y:.2f} | {row.band_min:.2f} | {row.band_max:.2f} | {status} |"
            )

    failures = [row for row in all_rows if not row.ok]
    if failures:
        print(f"FAIL verify_junction_rank_band: failures={len(failures)}")
        return 1
    if not all_rows:
        print("FAIL verify_junction_rank_band: no junction rows")
        return 1
    print("OK verify_junction_rank_band")
    return 0


if __name__ == "__main__":
    sys.exit(main())
