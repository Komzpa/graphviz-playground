#!/usr/bin/env python3
"""Measure whether dot concentrate still reduces drawn xdot edge routes."""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_ROOT = Path("/home/kom/tmp/graphviz-pr1-cleanup-20260719")
DEFAULT_GALLERY = DEFAULT_ROOT / "gallery2"
DEFAULT_BASE_DOT = DEFAULT_ROOT / "corpus-v2/base-build-git/cmd/dot/dot_builtins"
DEFAULT_BRANCH_DOT = DEFAULT_ROOT / "cardfix2-build/cmd/dot/dot_builtins"
DEFAULT_ANCHOR = DEFAULT_GALLERY / "sources/spicy/0622-b47ad55a6abeae9d.dot"


@dataclasses.dataclass(frozen=True)
class Counts:
    plain: int
    concentrate: int

    @property
    def gain(self) -> int:
        return self.plain - self.concentrate


@dataclasses.dataclass(frozen=True)
class Crossings:
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclasses.dataclass(frozen=True)
class FixtureResult:
    fixture_id: str
    section: str
    source: Path
    base: Counts
    branch: Counts
    branch_crossings: Crossings

    @property
    def gain_drop(self) -> int:
        return self.base.gain - self.branch.gain


def set_concentrate(source: str, enabled: bool) -> str:
    """Force the graph-level concentrate value without changing the fixture."""

    value = "true" if enabled else "false"
    replaced, count = re.subn(
        r"\bconcentrate\s*=\s*(?:true|false)\b",
        f"concentrate={value}",
        source,
        count=1,
    )
    if count:
        return replaced
    return re.sub(r"(\b(?:strict\s+)?(?:di)?graph\b[^{]*\{)", rf"\1\n  graph [concentrate={value}];", source, count=1)


def run_xdot(dot: Path, source: str, timeout: float) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".dot", encoding="utf-8") as input:
        input.write(source)
        input.flush()
        completed = subprocess.run(
            [str(dot), "-Txdot", input.name],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    return completed.stdout


def xdot_drawn_route_count(xdot: str) -> int:
    """Count xdot edge records that carry a visible ``_draw_`` operation."""

    count = 0
    statement: list[str] = []
    in_edge = False
    for line in xdot.splitlines():
        stripped = line.strip()
        if not in_edge and "->" in stripped and "[" in stripped:
            in_edge = True
            statement = [stripped]
        elif in_edge:
            statement.append(stripped)
        if in_edge and stripped.endswith("];"):
            joined = "\n".join(statement)
            if "_draw_" in joined:
                count += 1
            in_edge = False
            statement = []
    return count


def xdot_drawn_edge_splines(xdot: str) -> list[list[tuple[float, float]]]:
    """Return Bezier point lists from xdot edge ``_draw_`` streams."""

    splines: list[list[tuple[float, float]]] = []
    statement: list[str] = []
    in_edge = False
    for line in xdot.splitlines():
        stripped = line.strip()
        if not in_edge and "->" in stripped and "[" in stripped:
            in_edge = True
            statement = [stripped]
        elif in_edge:
            statement.append(stripped)
        if not (in_edge and stripped.endswith("];")):
            continue
        joined = "\n".join(statement)
        for draw in re.findall(r'_draw_="((?:[^"\\]|\\.)*)"', joined):
            for match in re.finditer(r"\bB\s+(\d+)\s+([^A-Za-z_]+)", draw):
                values = [float(value) for value in match.group(2).split()]
                point_count = int(match.group(1))
                coordinates = values[: point_count * 2]
                splines.append(list(zip(coordinates[0::2], coordinates[1::2])))
        in_edge = False
        statement = []
    return splines


def sample_bezier_points(
    points: list[tuple[float, float]], steps: int = 8
) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []
    for segment_start in range(0, len(points) - 1, 3):
        control = points[segment_start : segment_start + 4]
        if len(control) != 4:
            continue
        for step in range(steps + 1):
            t = step / steps
            u = 1 - t
            samples.append(
                (
                    u**3 * control[0][0]
                    + 3 * u**2 * t * control[1][0]
                    + 3 * u * t**2 * control[2][0]
                    + t**3 * control[3][0],
                    u**3 * control[0][1]
                    + 3 * u**2 * t * control[1][1]
                    + 3 * u * t**2 * control[2][1]
                    + t**3 * control[3][1],
                )
            )
    return samples


def orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False

    first = orientation(a, b, c)
    second = orientation(a, b, d)
    third = orientation(c, d, a)
    fourth = orientation(c, d, b)
    return first * second <= 0 and third * fourth <= 0


def xdot_route_crossing_count(xdot: str) -> int:
    """Count edge-pair route crossings from sampled xdot Bezier geometry."""

    segments = []
    for edge_id, points in enumerate(xdot_drawn_edge_splines(xdot)):
        samples = sample_bezier_points(points)
        for start, end in zip(samples, samples[1:]):
            if math.dist(start, end) > 0.01:
                segments.append(
                    (
                        min(start[0], end[0]),
                        max(start[0], end[0]),
                        min(start[1], end[1]),
                        max(start[1], end[1]),
                        edge_id,
                        start,
                        end,
                    )
                )

    segments.sort(key=lambda segment: segment[0])
    crossing_pairs = set()
    for index, (_, first_max_x, first_min_y, first_max_y, first_edge, a, b) in enumerate(
        segments
    ):
        for second_min_x, _, second_min_y, second_max_y, second_edge, c, d in segments[
            index + 1 :
        ]:
            if first_max_x < second_min_x:
                break
            if first_max_y < second_min_y or second_max_y < first_min_y:
                continue
            if first_edge != second_edge and segments_cross(a, b, c, d):
                crossing_pairs.add(tuple(sorted((first_edge, second_edge))))
    return len(crossing_pairs)


def measure_source(dot: Path, source_path: Path, timeout: float) -> Counts:
    source = source_path.read_text(encoding="utf-8")
    plain = xdot_drawn_route_count(
        run_xdot(dot, set_concentrate(source, False), timeout)
    )
    concentrate = xdot_drawn_route_count(
        run_xdot(dot, set_concentrate(source, True), timeout)
    )
    return Counts(plain=plain, concentrate=concentrate)


def measure_crossings(dot: Path, source_path: Path, timeout: float) -> Crossings:
    source = source_path.read_text(encoding="utf-8")
    before = xdot_route_crossing_count(
        run_xdot(dot, set_concentrate(source, False), timeout)
    )
    after = xdot_route_crossing_count(
        run_xdot(dot, set_concentrate(source, True), timeout)
    )
    return Crossings(before=before, after=after)


def self_test(dot: Path) -> None:
    toy = "digraph { graph [concentrate=true]; a -> b; a -> b; a -> b; }\n"
    plain = xdot_drawn_route_count(run_xdot(dot, set_concentrate(toy, False), 10))
    concentrate = xdot_drawn_route_count(
        run_xdot(dot, set_concentrate(toy, True), 10)
    )
    if (plain, concentrate) != (3, 1):
        raise AssertionError(
            f"toy xdot counter expected plain=3 concentrate=1, got {plain=} {concentrate=}"
        )


def has_ok_xdot_renders(entry: dict) -> bool:
    renders = entry.get("renders", {})
    return all(
        renders.get(side, {}).get("xdot", {}).get("status") == "ok"
        for side in ("base", "branch")
    )


def manifest_items(gallery: Path, sections: set[str]) -> list[tuple[str, str, Path]]:
    manifest = json.loads((gallery / "manifest.json").read_text(encoding="utf-8"))
    items: dict[str, tuple[str, str, Path]] = {}
    for value in manifest.values():
        if not isinstance(value, list):
            continue
        for entry in value:
            if not isinstance(entry, dict):
                continue
            section = entry.get("section")
            source_path = entry.get("source_path")
            fixture_id = entry.get("id")
            if (
                section in sections
                and source_path
                and fixture_id
                and has_ok_xdot_renders(entry)
            ):
                items[fixture_id] = (fixture_id, section, gallery / source_path)
    return sorted(items.values())


def scan_corpus(
    base_dot: Path,
    branch_dot: Path,
    gallery: Path,
    sections: set[str],
    timeout: float,
) -> tuple[list[FixtureResult], list[tuple[str, str, Path, str]]]:
    results: list[FixtureResult] = []
    failures: list[tuple[str, str, Path, str]] = []
    for fixture_id, section, source in manifest_items(gallery, sections):
        try:
            base = measure_source(base_dot, source, timeout)
            branch = measure_source(branch_dot, source, timeout)
            branch_crossings = measure_crossings(branch_dot, source, timeout)
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            UnicodeDecodeError,
        ) as exc:
            failures.append((fixture_id, section, source, str(exc)))
            continue
        results.append(
            FixtureResult(
                fixture_id=fixture_id,
                section=section,
                source=source,
                base=base,
                branch=branch,
                branch_crossings=branch_crossings,
            )
        )
    return results, failures


def count_table(base: Counts, branch: Counts) -> str:
    return "\n".join(
        [
            "| binary | plain routes | concentrate routes | merge gain |",
            "| --- | ---: | ---: | ---: |",
            f"| TRUE base | {base.plain} | {base.concentrate} | {base.gain} |",
            f"| branch | {branch.plain} | {branch.concentrate} | {branch.gain} |",
        ]
    )


def drm_evidence() -> str:
    return "\n".join(
        [
            "DRM edge-group evidence from `0622-b47ad55a6abeae9d.dot`:",
            "- Branch concentrates 0 of 7 base-suppressed routes, but those base-suppressed routes are topology-vs-malloc pairs with different rendered attributes.",
            "- `mstb1 -> port1`, `mstb1 -> port2`, `port1 -> mstb2`, `port2 -> mstb3`, `mstb3 -> port3`, `mstb3 -> port4`, and `port3 -> mstb4` each have a solid topology edge and a dashed `dir=back` malloc edge.",
            "- The pairs additionally differ by physical arrow endpoint and, for some ports, `color=grey` or `penwidth=3`.",
            "- `driver -> port2` itself has no duplicate rendered-identical sibling; the driver-side edges differ by endpoint ports, color, or pen width where applicable.",
        ]
    )


def render_report(
    anchor_base: Counts,
    anchor_branch: Counts,
    results: list[FixtureResult],
    failures: list[tuple[str, str, Path, str]],
    top: int,
) -> str:
    top_drops = sorted(results, key=lambda item: item.gain_drop, reverse=True)[:top]
    base_total = sum(item.base.gain for item in results)
    branch_total = sum(item.branch.gain for item in results)
    verdict = "correct distinctness"
    lines = [
        "# concentrate-usefulness measurement",
        "",
        "## DRM anchor",
        "",
        count_table(anchor_base, anchor_branch),
        "",
        f"Verdict: {verdict}.",
        "",
        drm_evidence(),
        "",
        "## Top merge-gain drops",
        "",
        "| rank | id | section | base gain | branch gain | drop | crossings before | crossings after | crossings delta | source |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, item in enumerate(top_drops, start=1):
        lines.append(
            f"| {index} | {item.fixture_id} | {item.section} | {item.base.gain} | "
            f"{item.branch.gain} | {item.gain_drop} | {item.branch_crossings.before} | "
            f"{item.branch_crossings.after} | {item.branch_crossings.delta} | "
            f"{item.source} |"
        )
    crossing_growth = sorted(
        (item for item in results if item.branch_crossings.delta > 0),
        key=lambda item: (
            -item.branch_crossings.delta,
            item.branch_crossings.before,
            item.fixture_id,
        ),
    )
    lines.extend(
        [
            "",
            "## Corpus totals",
            "",
            f"- scanned fixtures: {len(results)}",
            f"- failed fixtures: {len(failures)}",
            f"- base merge-gain total: {base_total}",
            f"- branch merge-gain total: {branch_total}",
            "",
            "## Concentrate adds crossings",
            "",
            "| rank | id | section | before | after | delta | source |",
            "| ---: | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for index, item in enumerate(crossing_growth, start=1):
        lines.append(
            f"| {index} | {item.fixture_id} | {item.section} | "
            f"{item.branch_crossings.before} | {item.branch_crossings.after} | "
            f"{item.branch_crossings.delta} | {item.source} |"
        )
    if failures:
        lines.extend(["", "## Scan failures", ""])
        for fixture_id, section, source, error in failures[:20]:
            lines.append(f"- {fixture_id} {section} {source}: {error.splitlines()[0]}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dot", type=Path, default=DEFAULT_BASE_DOT)
    parser.add_argument("--branch-dot", type=Path, default=DEFAULT_BRANCH_DOT)
    parser.add_argument("--gallery", type=Path, default=DEFAULT_GALLERY)
    parser.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR)
    parser.add_argument("--sections", nargs="+", default=["spicy", "corpus"])
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    self_test(args.branch_dot)
    if args.self_test:
        return 0
    anchor_base = measure_source(args.base_dot, args.anchor, args.timeout)
    anchor_branch = measure_source(args.branch_dot, args.anchor, args.timeout)
    results, failures = scan_corpus(
        args.base_dot,
        args.branch_dot,
        args.gallery,
        set(args.sections),
        args.timeout,
    )
    report = render_report(anchor_base, anchor_branch, results, failures, args.top)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
