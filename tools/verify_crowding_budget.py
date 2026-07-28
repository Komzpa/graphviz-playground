#!/usr/bin/env python3
"""Verify concentrate does not increase worst-band rank crowding."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import measure_rank_density


FIXTURES = [
    Path("tests/drbd-anchor.dot"),
    Path("tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"),
    Path("tests/graphs/concentrate-demo/dense-rank-crowding-budget.dot"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, default=Path("build/cmd/dot/dot_builtins"))
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def render(dot: Path, fixture: Path, *, concentrate: bool, newrank: bool, timeout: float) -> dict:
    return measure_rank_density.render_json(
        dot, fixture, concentrate=concentrate, newrank=newrank, timeout=timeout
    )


def worst(densities: list[measure_rank_density.BandDensity]) -> measure_rank_density.BandDensity:
    return max(densities, key=lambda density: density.segment_count)


def arrowhead_count(layout: dict) -> int:
    return sum(1 for edge in layout.get("edges", []) if edge.get("_hdraw_"))


def junction_count(layout: dict) -> int:
    return sum(
        1
        for obj in layout.get("objects", [])
        if obj.get("_concentrate_junction_node") == "true"
    )


def committed(path: Path) -> bool:
    return (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    if not args.dot.exists():
        raise SystemExit(f"dot binary not found: {args.dot}")

    for fixture in FIXTURES:
        if not fixture.exists():
            failures.append(f"missing fixture {fixture}")
            continue
        if not committed(fixture):
            failures.append(f"fixture is not committed: {fixture}")
            continue
        for newrank in (False, True):
            ranker = "newrank" if newrank else "default"
            off_layout = render(
                args.dot, fixture, concentrate=False, newrank=newrank, timeout=args.timeout
            )
            on_layout = render(
                args.dot, fixture, concentrate=True, newrank=newrank, timeout=args.timeout
            )
            off = measure_rank_density.measure_layout(off_layout)
            on = measure_rank_density.measure_layout(on_layout)
            print(f"# {fixture} {ranker}")
            print("mode\tband\tupper_y\tlower_y\tsegments\tmin_neighbor_gap")
            for mode, densities in (("off", off), ("on", on)):
                for density in densities:
                    print(
                        f"{mode}\t{density.band}\t{density.upper_rank_y:.3f}\t"
                        f"{density.lower_rank_y:.3f}\t{density.segment_count}\t"
                        f"{measure_rank_density.format_gap(density.min_neighbor_gap)}"
                    )
            off_worst = worst(off)
            on_worst = worst(on)
            if on_worst.segment_count > off_worst.segment_count:
                failures.append(
                    f"{fixture} {ranker}: worst band grew "
                    f"{off_worst.segment_count}->{on_worst.segment_count}"
                )

    fan = Path("tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot")
    for newrank in (False, True):
        off_layout = render(args.dot, fan, concentrate=False, newrank=newrank, timeout=args.timeout)
        on_layout = render(args.dot, fan, concentrate=True, newrank=newrank, timeout=args.timeout)
        ranker = "newrank" if newrank else "default"
        off_arrows = arrowhead_count(off_layout)
        on_arrows = arrowhead_count(on_layout)
        print(
            f"# fan-in-control {ranker} junctions={junction_count(on_layout)} "
            f"arrowheads_off={off_arrows} arrowheads_on={on_arrows}"
        )
        if junction_count(on_layout) == 0:
            failures.append(f"fan-in control {ranker}: no junction drawn")
        if off_arrows != on_arrows:
            failures.append(
                f"fan-in control {ranker}: arrowheads changed {off_arrows}->{on_arrows}"
            )

    if failures:
        print(json.dumps({"failures": failures}, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
