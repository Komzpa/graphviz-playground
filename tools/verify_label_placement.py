#!/usr/bin/env python3
"""Verify concentrate_junction label placement from dot -Tjson output."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


MODES = ("off", "on")
RANKERS = (
    ("default", ()),
    ("newrank", ("-Gnewrank=true",)),
)
DEFAULT_CASES = ("drbd-anchor.dot", "junction-fanin.dot", "junction-fanout.dot")
REQUIRED_COUNTS = {
    "ioctl_set_disk()": 1,
    "receive_param()": 1,
    "io completion error": 1,
    "start resync": 1,
}


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class DrawnLabel:
    text: str
    anchor: Point
    height: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("dot_files", nargs="*")
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--dot-path")
    parser.add_argument("--require-counts", nargs=2, metavar=("CASE", "MODE"))
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_dot(dot_path: str | None) -> str:
    if dot_path:
        return dot_path
    dot = shutil.which("dot")
    if dot is None:
        raise RuntimeError("dot not found in PATH")
    return dot


def default_cases(root: Path) -> list[tuple[str, Path]]:
    return [(Path(name).stem.upper(), root / "tests" / name) for name in DEFAULT_CASES]


def cases_from_args(args: argparse.Namespace, root: Path) -> list[tuple[str, Path]]:
    if not args.dot_files:
        cases = default_cases(root)
    else:
        cases = [(Path(path).stem.upper(), Path(path)) for path in args.dot_files]
    missing = [str(path) for _, path in cases if not path.exists()]
    if missing:
        raise FileNotFoundError("missing fixture(s): " + ", ".join(missing))
    return cases


def run_dot(dot: str, path: Path, mode: str) -> dict:
    if "/" not in mode:
        raise ValueError(f"invalid combined mode: {mode}")
    mode_name, ranker = mode.split("/", 1)
    if mode_name not in {"off", "on"}:
        raise ValueError(f"unknown mode: {mode}")
    concentrate_junction = mode_name == "on"
    ranker_flags = () if ranker == "default" else ("-Gnewrank=true",)
    proc = subprocess.run(
        [dot, *(("-Gconcentrate=true",) if concentrate_junction else ()), *ranker_flags, "-Tjson", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"dot failed for {path} mode={mode} rc={proc.returncode}\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def parse_bb(bb: str) -> tuple[float, float, float, float]:
    values = tuple(float(part) for part in bb.split(","))
    if len(values) != 4:
        raise ValueError(f"unexpected bb: {bb!r}")
    return values


def drawn_labels(edge: dict) -> list[DrawnLabel]:
    labels: list[DrawnLabel] = []
    font_size = 14.0
    for op in edge.get("_ldraw_", []):
        if op.get("op") == "F":
            font_size = float(op.get("size", font_size))
        elif op.get("op") == "T":
            x, y = op["pt"]
            labels.append(
                DrawnLabel(
                    text=str(op.get("text", "")),
                    anchor=Point(float(x), float(y)),
                    height=font_size,
                )
            )
    return labels


def bezier_point(curve: list[Point], t: float) -> Point:
    omt = 1.0 - t
    return Point(
        omt**3 * curve[0].x
        + 3.0 * omt**2 * t * curve[1].x
        + 3.0 * omt * t**2 * curve[2].x
        + t**3 * curve[3].x,
        omt**3 * curve[0].y
        + 3.0 * omt**2 * t * curve[1].y
        + 3.0 * omt * t**2 * curve[2].y
        + t**3 * curve[3].y,
    )


def point_segment_distance(point: Point, start: Point, end: Point) -> float:
    vx = end.x - start.x
    vy = end.y - start.y
    wx = point.x - start.x
    wy = point.y - start.y
    denom = vx * vx + vy * vy
    if denom == 0.0:
        return math.hypot(wx, wy)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    return math.hypot(point.x - (start.x + t * vx), point.y - (start.y + t * vy))


def distance_to_spline(edge: dict, anchor: Point) -> float:
    best = math.inf
    for op in edge.get("_draw_", []):
        if op.get("op") != "b":
            continue
        points = [Point(float(x), float(y)) for x, y in op.get("points", [])]
        for i in range(0, len(points) - 3, 3):
            curve = points[i : i + 4]
            prev = bezier_point(curve, 0.0)
            for step in range(1, 101):
                point = bezier_point(curve, step / 100.0)
                best = min(best, point_segment_distance(anchor, prev, point))
                prev = point
    return best


def check_case(dot: str, case_name: str, path: Path, modes: tuple[str, ...]) -> tuple[int, dict[str, Counter[str]]]:
    violations = 0
    counts_by_mode: dict[str, Counter[str]] = {}
    for mode in modes:
        layout = run_dot(dot, path, mode)
        xmin, ymin, xmax, ymax = parse_bb(layout["bb"])
        counts = Counter()
        for edge in layout.get("edges", []):
            owned_by_concentrate_junction = edge.get("_concentrate_junction_original") == "true"
            for label in drawn_labels(edge):
                counts[label.text] += 1
                distance = distance_to_spline(edge, label.anchor)
                threshold = 4.0 * label.height
                in_bbox = xmin <= label.anchor.x <= xmax and ymin <= label.anchor.y <= ymax
                too_far = owned_by_concentrate_junction and distance > threshold
                status = "OK"
                if not in_bbox:
                    status = "OUTSIDE_BBOX"
                elif too_far:
                    status = "TOO_FAR_FROM_SPLINE"
                elif not owned_by_concentrate_junction and distance > threshold:
                    status = "PRINTED_ONLY_DISTANCE_NOT_JUNCTION_OWNED"
                print(
                    f"{case_name} mode={mode} label={label.text!r} "
                    f"anchor=({label.anchor.x:.2f},{label.anchor.y:.2f}) "
                    f"bbox=({xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}) "
                    f"distance_to_own_spline={distance:.2f} "
                    f"label_height={label.height:.2f} threshold={threshold:.2f} "
                    f"concentrate_junction_owned={owned_by_concentrate_junction} status={status}"
                )
                if not in_bbox or too_far:
                    violations += 1
        counts_by_mode[mode] = counts
    if violations:
        print(f"{case_name}: {violations} placement violation(s)")
    else:
        print(f"{case_name}: zero placement violations")
    return violations, counts_by_mode


def check_required_counts(
    selector: str,
    mode: str,
    cases: list[tuple[str, Path]],
    counts: dict[tuple[str, str], Counter[str]],
) -> int:
    selected = [name for name, _ in cases if selector.upper() in name or name in selector.upper()]
    if not selected:
        print(f"FAIL --require-counts: no case matches {selector!r}")
        return 1
    name = selected[0]
    failures = 0
    matching = [
        key_mode
        for case_name, key_mode in counts
        if case_name == name
        and (key_mode == mode or ("/" not in mode and key_mode.startswith(f"{mode}/")))
    ]
    if not matching:
        print(f"FAIL --require-counts: no counts for mode={mode!r} case={name!r}")
        return 1
    actual = Counter()
    for key_mode in matching:
        actual.update(counts.get((name, key_mode), Counter()))
    for label, expected in REQUIRED_COUNTS.items():
        got = actual[label]
        print(f"require-counts case={name} mode={mode} label={label!r} expected={expected} actual={got}")
        if got != expected:
            failures += 1
    return failures


def main() -> int:
    args = parse_args()
    modes = tuple(
        f"{mode}/{ranker}" for mode in (args.mode or MODES) for ranker, _ in RANKERS
    )
    try:
        root = repo_root()
        dot = resolve_dot(args.dot_path)
        cases = cases_from_args(args, root)
        failures = 0
        all_counts: dict[tuple[str, str], Counter[str]] = {}
        for case_name, path in cases:
            case_failures, counts_by_mode = check_case(dot, case_name, path, modes)
            failures += case_failures
            for mode, counts in counts_by_mode.items():
                all_counts[(case_name, mode)] = counts
        if args.require_counts:
            selector, mode = args.require_counts
            failures += check_required_counts(selector, mode.lower(), cases, all_counts)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if failures:
        print(f"verify_label_placement: {failures} failure(s)")
        return 1
    print("verify_label_placement: zero placement violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
