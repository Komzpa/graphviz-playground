#!/usr/bin/env python3
"""Measure drawn spline crowding between adjacent dot ranks."""

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


RENDER_LIMIT_KIB = 2_097_152
DEFAULT_TIMEOUT = 60.0
CURVE_STEPS = 40
SCANLINES = 31
EPS = 1e-7


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Strand:
    edge: int
    op: int
    curve: int
    points: tuple[Point, ...]


@dataclass(frozen=True)
class BandDensity:
    band: str
    upper_rank_y: float
    lower_rank_y: float
    segment_count: int
    min_neighbor_gap: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a graph with dot -Tjson and measure how many drawn spline "
            "strands occupy each band between adjacent ranks."
        )
    )
    parser.add_argument("graph", nargs="?", type=Path, help="DOT graph to render")
    parser.add_argument(
        "--dot",
        type=Path,
        default=Path("build/cmd/dot/dot_builtins"),
        help="dot or dot_builtins binary",
    )
    parser.add_argument(
        "--concentrate",
        action="store_true",
        help="add -Gconcentrate=true to the render command",
    )
    parser.add_argument(
        "--newrank",
        action="store_true",
        help="add -Gnewrank=true to the render command",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="render timeout in seconds",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "tsv"),
        default="table",
        help="output format",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the built-in synthetic geometry self-test",
    )
    return parser.parse_args()


def cap_address_space() -> None:
    limit = RENDER_LIMIT_KIB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def render_json(
    dot: Path,
    graph: Path,
    *,
    concentrate: bool,
    newrank: bool,
    timeout: float,
) -> dict:
    args = [str(dot), "-Kdot", "-Tjson"]
    if concentrate:
        args.append("-Gconcentrate=true")
    if newrank:
        args.append("-Gnewrank=true")
    args.append(str(graph))

    with tempfile.NamedTemporaryFile(prefix="rank-density-", suffix=".json") as out:
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
            raise RuntimeError(
                f"dot failed rc={proc.returncode} for {graph.name}: {stderr}"
            )
        out.seek(0)
        return json.load(out)


def parse_pos(value: str) -> Point:
    x, y = value.split(",", 1)
    return Point(float(x), float(y))


def rank_centers(layout: dict) -> list[float]:
    centers: list[float] = []
    for obj in layout.get("objects", []):
        if "pos" not in obj:
            continue
        try:
            centers.append(parse_pos(obj["pos"]).y)
        except (TypeError, ValueError):
            continue
    unique = sorted({round(y, 6) for y in centers}, reverse=True)
    return [float(y) for y in unique]


def bezier(points: list[Point], t: float) -> Point:
    omt = 1.0 - t
    return Point(
        omt**3 * points[0].x
        + 3.0 * omt**2 * t * points[1].x
        + 3.0 * omt * t**2 * points[2].x
        + t**3 * points[3].x,
        omt**3 * points[0].y
        + 3.0 * omt**2 * t * points[1].y
        + 3.0 * omt * t**2 * points[2].y
        + t**3 * points[3].y,
    )


def flatten_curve(points: list[Point]) -> tuple[Point, ...]:
    if len(points) == 2:
        return tuple(points)
    samples = [bezier(points, step / CURVE_STEPS) for step in range(CURVE_STEPS + 1)]
    return tuple(samples)


def drawn_strands(layout: dict) -> list[Strand]:
    strands: list[Strand] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        op_index = 0
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            raw_points = [Point(float(x), float(y)) for x, y in op.get("points", [])]
            samples: list[Point] = []
            if len(raw_points) == 2:
                samples.extend(flatten_curve(raw_points))
            else:
                for offset in range(0, len(raw_points) - 3, 3):
                    curve = raw_points[offset : offset + 4]
                    curve_samples = list(flatten_curve(curve))
                    if samples and curve_samples:
                        samples.extend(curve_samples[1:])
                    else:
                        samples.extend(curve_samples)
            if samples:
                strands.append(Strand(edge_index, op_index, 0, tuple(samples)))
            op_index += 1
    return strands


def strand_intersects_band(strand: Strand, low: float, high: float) -> bool:
    for a, b in zip(strand.points, strand.points[1:]):
        ymin = min(a.y, b.y)
        ymax = max(a.y, b.y)
        if ymax > low + EPS and ymin < high - EPS:
            return True
    return False


def x_intersections(strand: Strand, y: float) -> list[float]:
    xs: list[float] = []
    for a, b in zip(strand.points, strand.points[1:]):
        if abs(a.y - b.y) <= EPS:
            if abs(y - a.y) <= EPS:
                xs.extend((a.x, b.x))
            continue
        if y < min(a.y, b.y) - EPS or y > max(a.y, b.y) + EPS:
            continue
        t = (y - a.y) / (b.y - a.y)
        if -EPS <= t <= 1.0 + EPS:
            xs.append(a.x + t * (b.x - a.x))
    return xs


def min_gap_in_band(strands: list[Strand], low: float, high: float) -> float | None:
    best = math.inf
    if high - low <= EPS:
        return None
    for step in range(1, SCANLINES + 1):
        y = low + (high - low) * step / (SCANLINES + 1)
        hits: list[tuple[float, tuple[int, int, int, int]]] = []
        for strand in strands:
            for hit_index, x in enumerate(x_intersections(strand, y)):
                hits.append((x, (strand.edge, strand.op, strand.curve, hit_index)))
        hits.sort(key=lambda item: item[0])
        for (left_x, left_id), (right_x, right_id) in zip(hits, hits[1:]):
            if left_id[:3] == right_id[:3]:
                continue
            best = min(best, max(0.0, right_x - left_x))
    if best is math.inf:
        return None
    return best


def measure_layout(layout: dict) -> list[BandDensity]:
    ranks = rank_centers(layout)
    strands = drawn_strands(layout)
    densities: list[BandDensity] = []
    for index, (upper, lower) in enumerate(zip(ranks, ranks[1:])):
        high = max(upper, lower)
        low = min(upper, lower)
        band_strands = [
            strand for strand in strands if strand_intersects_band(strand, low, high)
        ]
        densities.append(
            BandDensity(
                band=f"rank{index}-rank{index + 1}",
                upper_rank_y=upper,
                lower_rank_y=lower,
                segment_count=len(band_strands),
                min_neighbor_gap=min_gap_in_band(band_strands, low, high),
            )
        )
    return densities


def format_gap(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.3f}"


def emit(densities: list[BandDensity], fmt: str) -> None:
    if fmt == "json":
        print(
            json.dumps(
                [
                    {
                        "band": density.band,
                        "upper_rank_y": density.upper_rank_y,
                        "lower_rank_y": density.lower_rank_y,
                        "segment_count": density.segment_count,
                        "min_neighbor_gap": density.min_neighbor_gap,
                    }
                    for density in densities
                ],
                sort_keys=True,
            )
        )
        return
    if fmt == "tsv":
        print("band\tupper_rank_y\tlower_rank_y\tsegment_count\tmin_neighbor_gap")
        for density in densities:
            print(
                f"{density.band}\t{density.upper_rank_y:.3f}\t"
                f"{density.lower_rank_y:.3f}\t{density.segment_count}\t"
                f"{format_gap(density.min_neighbor_gap)}"
            )
        return
    print("band          upper_y  lower_y  segments  min_neighbor_gap")
    for density in densities:
        print(
            f"{density.band:<12} {density.upper_rank_y:8.3f} "
            f"{density.lower_rank_y:8.3f} {density.segment_count:9d} "
            f"{format_gap(density.min_neighbor_gap):>16}"
        )


def synthetic_layout() -> dict:
    def node(gvid: int, name: str, y: float) -> dict:
        return {"_gvid": gvid, "name": name, "pos": f"{gvid * 20},{y}"}

    def edge(edge_id: int, x: float) -> dict:
        return {
            "tail": 0,
            "head": 3,
            "_draw_": [
                {
                    "op": "b",
                    "points": [[x, 100.0], [x, 70.0], [x, 30.0], [x, 0.0]],
                }
            ],
        }

    return {
        "objects": [
            node(0, "a0", 100.0),
            node(1, "a1", 100.0),
            node(2, "b0", 0.0),
            node(3, "b1", 0.0),
        ],
        "edges": [edge(0, 0.0), edge(1, 10.0), edge(2, 30.0)],
    }


def self_test() -> None:
    densities = measure_layout(synthetic_layout())
    assert len(densities) == 1, densities
    density = densities[0]
    assert density.segment_count == 3, density
    assert density.min_neighbor_gap is not None, density
    assert abs(density.min_neighbor_gap - 10.0) < 0.01, density
    print("measure_rank_density self-test ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.graph is None:
        raise SystemExit("graph is required unless --self-test is used")
    if not args.dot.exists():
        raise SystemExit(f"dot binary not found: {args.dot}")
    if not args.graph.exists():
        raise SystemExit(f"graph not found: {args.graph}")
    layout = render_json(
        args.dot,
        args.graph,
        concentrate=args.concentrate,
        newrank=args.newrank,
        timeout=args.timeout,
    )
    emit(measure_layout(layout), args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
