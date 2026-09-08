#!/usr/bin/env python3
"""Verify concentrate does not add too many drawn edge detours."""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FIXTURES = (
    Path("tests/graphs/concentrate-demo/nonlocal-arm-detour.dot"),
    Path("tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"),
)
DETOUR_RATIO = 1.5
DETOUR_MARGIN = 2
RENDER_LIMIT_KIB = 2_097_152


@dataclass(frozen=True)
class EdgeRatio:
    edge_index: int
    path_length: float
    straight_length: float
    ratio: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, default=Path("build/cmd/dot/dot_builtins"))
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


def cap_address_space() -> None:
    limit = RENDER_LIMIT_KIB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def render(dot: Path, graph: Path, *, concentrate: bool, timeout: float) -> dict[str, Any]:
    args = [
        str(dot),
        "-Kdot",
        f"-Gconcentrate={str(concentrate).lower()}",
        "-Tjson",
        str(graph),
    ]
    with tempfile.NamedTemporaryFile(prefix="edge-detours-", suffix=".json") as out:
        proc = subprocess.run(
            args,
            stdout=out,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            preexec_fn=cap_address_space,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"dot failed rc={proc.returncode} for {graph}: {stderr}")
        out.seek(0)
        return json.load(out)


def parse_pos(value: str) -> tuple[float, float]:
    x, y = value.split(",", 1)
    return float(x), float(y)


def control_path_length(points: list[list[float]]) -> float:
    return sum(math.dist(left, right) for left, right in zip(points, points[1:]))


def edge_path_length(edge: dict[str, Any]) -> float:
    length = 0.0
    for op in edge.get("_draw_", []):
        if op.get("op") in {"b", "B", "L"} and len(op.get("points", [])) >= 2:
            length += control_path_length(op["points"])
    return length


def edge_ratios(layout: dict[str, Any]) -> list[EdgeRatio]:
    objects = {int(obj["_gvid"]): obj for obj in layout.get("objects", [])}
    ratios: list[EdgeRatio] = []
    for index, edge in enumerate(layout.get("edges", [])):
        if "_draw_" not in edge or edge.get("style") == "invis":
            continue
        tail = objects[int(edge["tail"])]
        head = objects[int(edge["head"])]
        path_length = edge_path_length(edge)
        straight_length = math.dist(parse_pos(tail["pos"]), parse_pos(head["pos"]))
        ratio = path_length / straight_length if straight_length > 0.0 else math.inf
        ratios.append(EdgeRatio(index, path_length, straight_length, ratio))
    return ratios


def print_ratios(graph: Path, mode: str, ratios: list[EdgeRatio]) -> None:
    detours = sum(ratio.ratio > DETOUR_RATIO for ratio in ratios)
    print(f"# {graph} concentrate={mode} detours={detours}/{len(ratios)}")
    print("edge\tpath_length\tstraight_length\tratio")
    for ratio in ratios:
        print(
            f"{ratio.edge_index}\t{ratio.path_length:.6f}\t"
            f"{ratio.straight_length:.6f}\t{ratio.ratio:.6f}"
        )


def main() -> int:
    args = parse_args()
    if not args.dot.exists():
        raise SystemExit(f"dot binary not found: {args.dot}")

    failures: list[str] = []
    for graph in FIXTURES:
        if not graph.exists():
            failures.append(f"missing fixture {graph}")
            continue
        off = edge_ratios(render(args.dot, graph, concentrate=False, timeout=args.timeout))
        on = edge_ratios(render(args.dot, graph, concentrate=True, timeout=args.timeout))
        print_ratios(graph, "false", off)
        print_ratios(graph, "true", on)
        off_detours = sum(ratio.ratio > DETOUR_RATIO for ratio in off)
        on_detours = sum(ratio.ratio > DETOUR_RATIO for ratio in on)
        if on_detours > off_detours + DETOUR_MARGIN:
            failures.append(
                f"{graph}: concentrate detours grew by more than {DETOUR_MARGIN}: "
                f"{off_detours}->{on_detours}"
            )

    if failures:
        print(json.dumps({"failures": failures}, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
