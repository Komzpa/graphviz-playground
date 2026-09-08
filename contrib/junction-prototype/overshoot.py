#!/usr/bin/env python3
"""Report dot splines whose control points leave their endpoint rank interval."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    y: float
    height: float


@dataclass(frozen=True)
class Edge:
    tail: str
    head: str
    points: list[tuple[float, float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count edge splines that overshoot the y-span of their endpoint nodes."
    )
    parser.add_argument("input", metavar="IN.gv")
    parser.add_argument("--dot", default="dot")
    parser.add_argument("--format", choices=("plain", "xdot"), default="plain")
    parser.add_argument("--list", action="store_true", help="print overshooting edges after the count")
    return parser.parse_args()


def dot_plain(path: str, dot: str) -> str:
    text = open(path, encoding="utf-8").read()
    for line in text.splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4 and fields[0] == "graph":
            return text
        if fields:
            break
    return subprocess.run(
        [dot, "-Tplain", path],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def parse_plain(text: str) -> tuple[dict[str, Node], list[Edge]]:
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    for line in text.splitlines():
        fields = shlex.split(line)
        if len(fields) >= 6 and fields[0] == "node":
            nodes[fields[1]] = Node(y=float(fields[3]), height=float(fields[5]))
        elif len(fields) >= 5 and fields[0] == "edge":
            count = int(fields[3])
            coords = fields[4 : 4 + count * 2]
            points = [(float(coords[i]), float(coords[i + 1])) for i in range(0, len(coords), 2)]
            edges.append(Edge(fields[1], fields[2], points))
    return nodes, edges


def overshoots(nodes: dict[str, Node], edges: list[Edge]) -> list[tuple[Edge, float, float]]:
    result: list[tuple[Edge, float, float]] = []
    for edge in edges:
        tail = nodes.get(edge.tail)
        head = nodes.get(edge.head)
        if tail is None or head is None:
            continue
        low = min(tail.y - tail.height / 2.0, head.y - head.height / 2.0)
        high = max(tail.y + tail.height / 2.0, head.y + head.height / 2.0)
        excess = max((low - y for _, y in edge.points), default=0.0)
        excess = max(excess, max((y - high for _, y in edge.points), default=0.0))
        if excess > 1e-9:
            result.append((edge, low, high))
    return result


def main() -> int:
    args = parse_args()
    if args.format == "xdot":
        print("warning: xdot input requested; using dot -Tplain geometry for the predicate", file=sys.stderr)
    try:
        nodes, edges = parse_plain(dot_plain(args.input, args.dot))
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"overshoot: failed to run dot: {exc}", file=sys.stderr)
        return 1

    bad = overshoots(nodes, edges)
    print(len(bad))
    if args.list:
        for edge, low, high in bad:
            ys = ",".join(f"{y:g}" for _, y in edge.points if y < low or y > high)
            print(f"{edge.tail} -> {edge.head}\tallowed_y=[{low:g},{high:g}]\tbad_y={ys}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
