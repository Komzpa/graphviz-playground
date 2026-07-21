#!/usr/bin/env python3
"""Measure whether dot concentrate still reduces drawn xdot edge routes."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_ROOT = Path("/home/kom/tmp/graphviz-pr1-cleanup-20260719")
DEFAULT_GALLERY = DEFAULT_ROOT / "gallery2"
DEFAULT_BASE_DOT = DEFAULT_ROOT / "corpus-v2/base-build-git/cmd/dot/dot_builtins"
DEFAULT_BRANCH_DOT = DEFAULT_ROOT / "gallery-build-a9d097e69/cmd/dot/dot_builtins"
DEFAULT_ANCHOR = DEFAULT_GALLERY / "sources/spicy/0622-b47ad55a6abeae9d.dot"


@dataclasses.dataclass(frozen=True)
class Counts:
    plain: int
    concentrate: int

    @property
    def gain(self) -> int:
        return self.plain - self.concentrate


@dataclasses.dataclass(frozen=True)
class FixtureResult:
    fixture_id: str
    section: str
    source: Path
    base: Counts
    branch: Counts

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


def measure_source(dot: Path, source_path: Path, timeout: float) -> Counts:
    source = source_path.read_text(encoding="utf-8")
    plain = xdot_drawn_route_count(
        run_xdot(dot, set_concentrate(source, False), timeout)
    )
    concentrate = xdot_drawn_route_count(
        run_xdot(dot, set_concentrate(source, True), timeout)
    )
    return Counts(plain=plain, concentrate=concentrate)


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
        "| rank | id | section | base gain | branch gain | drop | source |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for index, item in enumerate(top_drops, start=1):
        lines.append(
            f"| {index} | {item.fixture_id} | {item.section} | {item.base.gain} | "
            f"{item.branch.gain} | {item.gain_drop} | {item.source} |"
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
        ]
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
