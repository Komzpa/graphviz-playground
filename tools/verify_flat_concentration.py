#!/usr/bin/env python3
"""Verify concentrated same-rank opposite edges stay flat between endpoints."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


CONCENTRATE_RE = re.compile(r"\bconcentrate\s*=\s*\"?true\"?", re.IGNORECASE)
RANKERS = (("default", ()), ("newrank=true", ("-Gnewrank=true",)))
POINT_EPSILON = 0.02


def tracked_graphs() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "*.dot", "*.gv"], text=True
    )
    return [
        Path(line)
        for line in output.splitlines()
        if CONCENTRATE_RE.search(
            Path(line).read_text(encoding="utf-8", errors="replace")
        )
    ]


def render(dot: str, path: Path, extra_args: tuple[str, ...]) -> dict | None:
    proc = subprocess.run(
        [dot, "-Kdot", "-Tjson", *extra_args, path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        diagnostic = proc.stderr.decode("utf-8", errors="replace").splitlines()
        print(
            f"{path} [{' '.join(extra_args) or 'default'}] skipped: "
            f"dot exited {proc.returncode}: {diagnostic[0] if diagnostic else ''}"
        )
        return None
    output = proc.stdout.decode("utf-8", errors="replace")
    return json.loads(output)


def node_box(node: dict) -> tuple[float, float, float, float]:
    x, y = (float(value) for value in node["pos"].split(","))
    half_width = float(node["width"]) * 72 / 2
    half_height = float(node["height"]) * 72 / 2
    return x - half_width, y - half_height, x + half_width, y + half_height


def arrow_count(edge: dict, stream: str) -> int:
    return sum(1 for op in edge.get(stream, []) if op.get("op") in {"P", "p"})


def bezier_points(edge: dict) -> list[list[float]]:
    return [
        point
        for op in edge.get("_draw_", [])
        if op.get("op") in {"b", "B"}
        for point in op["points"]
    ]


def same_rank_pairs(layout: dict) -> list[tuple[str, str, dict]]:
    objects = {node["_gvid"]: node for node in layout["objects"] if "pos" in node}
    pair_edges: dict[frozenset[int], list[dict]] = {}
    for edge in layout["edges"]:
        tail = objects.get(edge["tail"])
        head = objects.get(edge["head"])
        if tail is None or head is None or tail["_gvid"] == head["_gvid"]:
            continue
        if (
            abs(float(tail["pos"].split(",")[1]) - float(head["pos"].split(",")[1]))
            > POINT_EPSILON
        ):
            continue
        key = frozenset((tail["_gvid"], head["_gvid"]))
        pair_edges.setdefault(key, []).append(edge)

    pairs = []
    for key, edges in pair_edges.items():
        directions = {(edge["tail"], edge["head"]) for edge in edges}
        if len(directions) < 2:
            continue
        drawn = [edge for edge in edges if "_draw_" in edge]
        if len(drawn) != 1:
            continue
        left_id, right_id = sorted(key)
        pairs.append((objects[left_id]["name"], objects[right_id]["name"], drawn[0]))
    return pairs


def verify_pair(
    path: Path, ranker: str, layout: dict, left: str, right: str, edge: dict
) -> None:
    objects = {node["name"]: node for node in layout["objects"] if "pos" in node}
    left_box = node_box(objects[left])
    right_box = node_box(objects[right])
    low = max(left_box[1], right_box[1])
    high = min(left_box[3], right_box[3])
    points = bezier_points(edge)

    print(f"{path} [{ranker}] {left}<->{right}")
    print(f"  {left} box: {left_box}")
    print(f"  {right} box: {right_box}")
    print(f"  spline control points: {points}")
    print(
        "  arrows: "
        f"head={arrow_count(edge, '_hdraw_')} tail={arrow_count(edge, '_tdraw_')}"
    )

    if not points:
        raise AssertionError(f"{path} [{ranker}] {left}<->{right}: no spline")
    if arrow_count(edge, "_hdraw_") != 1 or arrow_count(edge, "_tdraw_") != 1:
        raise AssertionError(
            f"{path} [{ranker}] {left}<->{right}: expected one head and tail arrow"
        )
    for point in points:
        if point[1] < low - POINT_EPSILON or point[1] > high + POINT_EPSILON:
            raise AssertionError(
                f"{path} [{ranker}] {left}<->{right}: point {point} outside "
                f"vertical band [{low}, {high}]"
            )


def main() -> None:
    dot = shutil.which("dot")
    if dot is None:
        raise SystemExit("dot not found on PATH")

    checked = 0
    skipped = 0
    for path in tracked_graphs():
        for ranker, args in RANKERS:
            layout = render(dot, path, args)
            if layout is None:
                skipped += 1
                continue
            for left, right, edge in same_rank_pairs(layout):
                verify_pair(path, ranker, layout, left, right, edge)
                checked += 1

    print(f"checked concentrated same-rank pairs: {checked}")
    print(f"skipped non-renderable concentrated graphs/rankers: {skipped}")
    if checked == 0:
        raise AssertionError("no concentrated same-rank pairs found")


if __name__ == "__main__":
    main()
