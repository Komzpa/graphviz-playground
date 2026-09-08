#!/usr/bin/env python3
"""Verify labelled junction fan readability and byte-identity fallout."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# The anchor graph and the baseline binary are not in the tree; point these at
# local copies to run the full check. Without them the in-tree checks still run.
ANCHOR = Path(os.environ.get("JUNCTION_LABEL_ANCHOR", ""))
DEFAULT_BASELINE = Path(os.environ.get("JUNCTION_LABEL_BASELINE", ""))
REQUIRED_LABELS = (
    "ioctl_set_disk()",
    "receive_param()",
    "io completion error",
    "start resync",
)
NAMED_LABELS = (
    "ioctl_set_disk()",
    "receive_param()",
    "io completion error",
)
RANKERS = (("default", ()), ("newrank=true", ("-Gnewrank=true",)))


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass
class LabelBox:
    edge: str
    text: str
    box: tuple[float, float, float, float]
    expanded: tuple[float, float, float, float]
    own_edge_index: int
    foreign_splines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Counts:
    foreign_spline_labels: int
    label_label_overlaps: int
    label_node_overlaps: int
    final_crossings: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (
            self.foreign_spline_labels,
            self.label_label_overlaps,
            self.label_node_overlaps,
            self.final_crossings,
        )


@dataclass
class Analysis:
    layout: dict[str, Any]
    labels: list[LabelBox]
    counts: Counts


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dot", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current-dot", type=Path)
    parser.add_argument("--anchor", type=Path, default=ANCHOR)
    parser.add_argument("--timeout", default="15s")
    parser.add_argument("--memory-kb", type=int, default=2_097_152)
    parser.add_argument("--identity-limit", type=int, default=40)
    return parser.parse_args()


def resolve_dot(path: Path | None) -> Path:
    if path is not None:
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    built = repo_root() / "build" / "cmd" / "dot" / "dot_builtins"
    if built.exists():
        return built
    system = shutil.which("dot")
    if system is None:
        raise FileNotFoundError("dot")
    return Path(system)


def run_dot(
    dot: Path,
    path: Path,
    output_format: str,
    ranker_args: tuple[str, ...] = (),
    *,
    verbose: bool = False,
    timeout: str,
    memory_kb: int,
) -> subprocess.CompletedProcess[str]:
    args = [
        str(dot),
        "-Gconcentrate=true",
        *ranker_args,
        f"-T{output_format}",
        str(path),
    ]
    if verbose:
        args.insert(1, "-v")
    proc = subprocess.run(
        [
            "timeout",
            timeout,
            "bash",
            "-lc",
            f"ulimit -v {memory_kb}; exec \"$@\"",
            "dot-ulimit",
            *args,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc


def checked_json(
    dot: Path,
    path: Path,
    ranker_args: tuple[str, ...],
    args: argparse.Namespace,
) -> dict[str, Any]:
    proc = run_dot(
        dot,
        path,
        "json",
        ranker_args,
        timeout=args.timeout,
        memory_kb=args.memory_kb,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"dot json failed rc={proc.returncode} path={path}\n{proc.stderr}")
    return json.loads(proc.stdout)


def final_crossings(
    dot: Path,
    path: Path,
    ranker_args: tuple[str, ...],
    args: argparse.Namespace,
) -> int:
    proc = run_dot(
        dot,
        path,
        "svg",
        ranker_args,
        verbose=True,
        timeout=args.timeout,
        memory_kb=args.memory_kb,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"dot -v failed rc={proc.returncode} path={path}\n{proc.stderr}")
    matches = re.findall(r"mincross[^:\n]*:\s+(\d+)\s+crossings", proc.stderr)
    if not matches:
        matches = re.findall(r"(\d+)\s+crossings", proc.stderr)
    if not matches:
        raise RuntimeError(f"could not parse crossing count for {path}")
    return int(matches[-1])


def edge_name(layout: dict[str, Any], edge: dict[str, Any]) -> str:
    names = {obj["_gvid"]: obj["name"] for obj in layout.get("objects", [])}
    return f"{names.get(edge.get('tail'), edge.get('tail'))}->{names.get(edge.get('head'), edge.get('head'))}"


def label_boxes(layout: dict[str, Any]) -> list[LabelBox]:
    boxes: list[LabelBox] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        font_size = 14.0
        for op in edge.get("_ldraw_", []):
            if op.get("op") == "F":
                font_size = float(op.get("size", font_size))
            if op.get("op") != "T":
                continue
            x, y = (float(v) for v in op["pt"])
            width = float(op.get("width", 0.0))
            align = op.get("align", "c")
            if align == "l":
                left = x
            elif align == "r":
                left = x - width
            else:
                left = x - width / 2.0
            bottom = y - 0.35 * font_size
            top = y + 0.75 * font_size
            box = (left, bottom, left + width, top)
            expanded = (box[0] - 3.0, box[1] - 3.0, box[2] + 3.0, box[3] + 3.0)
            boxes.append(
                LabelBox(
                    edge=edge_name(layout, edge),
                    text=str(op.get("text", "")),
                    box=box,
                    expanded=expanded,
                    own_edge_index=edge_index,
                )
            )
    return boxes


def node_boxes(layout: dict[str, Any]) -> list[tuple[str, tuple[float, float, float, float]]]:
    boxes = []
    for obj in layout.get("objects", []):
        pos = obj.get("pos")
        if not pos:
            continue
        x, y = (float(part) for part in pos.split(",")[:2])
        width = float(obj.get("width", 0.0)) * 72.0
        height = float(obj.get("height", 0.0)) * 72.0
        if width <= 0.0 or height <= 0.0:
            continue
        boxes.append((obj.get("name", str(obj.get("_gvid"))), (x - width / 2, y - height / 2, x + width / 2, y + height / 2)))
    return boxes


def bezier_point(points: list[Point], t: float) -> Point:
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


def edge_segments(edge: dict[str, Any]) -> list[tuple[Point, Point]]:
    segments: list[tuple[Point, Point]] = []
    for op in edge.get("_draw_", []):
        if op.get("op") in {"L", "P"}:
            pts = [Point(float(x), float(y)) for x, y in op.get("points", [])]
            segments.extend(zip(pts, pts[1:]))
        elif op.get("op") in {"b", "B"}:
            pts = [Point(float(x), float(y)) for x, y in op.get("points", [])]
            for i in range(0, len(pts) - 3, 3):
                curve = pts[i : i + 4]
                last = bezier_point(curve, 0.0)
                for step in range(1, 41):
                    current = bezier_point(curve, step / 40.0)
                    segments.append((last, current))
                    last = current
    return segments


def segment_enters_box(seg: tuple[Point, Point], box: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    p, q = seg
    if x0 <= p.x <= x1 and y0 <= p.y <= y1:
        return True
    if x0 <= q.x <= x1 and y0 <= q.y <= y1:
        return True
    dx = q.x - p.x
    dy = q.y - p.y
    t0 = 0.0
    t1 = 1.0
    for edge_p, edge_q in (
        (-dx, p.x - x0),
        (dx, x1 - p.x),
        (-dy, p.y - y0),
        (dy, y1 - p.y),
    ):
        if edge_p == 0.0:
            if edge_q < 0.0:
                return False
            continue
        ratio = edge_q / edge_p
        if edge_p < 0.0:
            if ratio > t1:
                return False
            t0 = max(t0, ratio)
        else:
            if ratio < t0:
                return False
            t1 = min(t1, ratio)
    return True


def boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def analyze(dot: Path, path: Path, ranker_args: tuple[str, ...], args: argparse.Namespace) -> Analysis:
    layout = checked_json(dot, path, ranker_args, args)
    labels = label_boxes(layout)
    edges = layout.get("edges", [])
    edge_segment_lists = [edge_segments(edge) for edge in edges]
    edge_names = [edge_name(layout, edge) for edge in edges]
    for label in labels:
        crossed_by: list[str] = []
        for edge_index, segments in enumerate(edge_segment_lists):
            if edge_index == label.own_edge_index:
                continue
            if any(segment_enters_box(segment, label.expanded) for segment in segments):
                crossed_by.append(edge_names[edge_index])
        label.foreign_splines = crossed_by
    label_label = 0
    for i, left in enumerate(labels):
        for right in labels[i + 1 :]:
            if boxes_overlap(left.box, right.box):
                label_label += 1
    label_node = 0
    for label in labels:
        for node, box in node_boxes(layout):
            if node.startswith("_concentrate_junction_"):
                continue
            if boxes_overlap(label.box, box):
                label_node += 1
    return Analysis(
        layout=layout,
        labels=labels,
        counts=Counts(
            foreign_spline_labels=sum(1 for label in labels if label.foreign_splines),
            label_label_overlaps=label_label,
            label_node_overlaps=label_node,
            final_crossings=final_crossings(dot, path, ranker_args, args),
        ),
    )


def required_label_counts(analysis: Analysis) -> Counter[str]:
    counts = Counter(label.text for label in analysis.labels)
    return Counter({label: counts[label] for label in REQUIRED_LABELS})


def has_labelled_fan(layout: dict[str, Any]) -> bool:
    return any(
        edge.get("_concentrate_junction_original") == "true" and edge.get("_ldraw_")
        for edge in layout.get("edges", [])
    )


def tracked_graphs(root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "graphs", "tests"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    paths = []
    for rel in proc.stdout.splitlines():
        if Path(rel).suffix in {".dot", ".gv"}:
            path = root / rel
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            if "concentrate_junction" in text:
                continue
            paths.append(path)
    return paths


def render_bytes(
    dot: Path,
    path: Path,
    ranker_args: tuple[str, ...],
    args: argparse.Namespace,
) -> bytes | None:
    cmd = [
        str(dot),
        "-Gconcentrate=true",
        *ranker_args,
        "-Tjson",
        str(path),
    ]
    proc = subprocess.run(
        [
            "timeout",
            args.timeout,
            "bash",
            "-lc",
            f"ulimit -v {args.memory_kb}; exec \"$@\"",
            "dot-ulimit",
            *cmd,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def sha(data: bytes | None) -> str:
    if data is None:
        return "<skipped>"
    return hashlib.sha256(data).hexdigest()


def check_anchor(args: argparse.Namespace, baseline: Path, current: Path) -> int:
    failures = 0
    print(f"commit sha: {subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_root(), text=True).strip()}")
    print(f"anchor: {args.anchor}")
    print(f"baseline dot: {baseline}")
    print(f"current dot: {current}")
    for ranker, ranker_args in RANKERS:
        before = analyze(baseline, args.anchor, ranker_args, args)
        after = analyze(current, args.anchor, ranker_args, args)
        print(
            f"anchor [{ranker}] before counts: "
            f"foreign_spline_labels={before.counts.foreign_spline_labels} "
            f"label_label_overlaps={before.counts.label_label_overlaps} "
            f"label_node_overlaps={before.counts.label_node_overlaps} "
            f"final_crossings={before.counts.final_crossings}"
        )
        print(
            f"anchor [{ranker}] after counts: "
            f"foreign_spline_labels={after.counts.foreign_spline_labels} "
            f"label_label_overlaps={after.counts.label_label_overlaps} "
            f"label_node_overlaps={after.counts.label_node_overlaps} "
            f"final_crossings={after.counts.final_crossings}"
        )
        for name, old, new in zip(
            ("foreign_spline_labels", "label_label_overlaps", "label_node_overlaps", "final_crossings"),
            before.counts.as_tuple(),
            after.counts.as_tuple(),
        ):
            if new > old:
                print(f"FAIL anchor [{ranker}] {name} regressed: before={old} after={new}")
                failures += 1
        before_required = required_label_counts(before)
        after_required = required_label_counts(after)
        for label in REQUIRED_LABELS:
            print(
                f"anchor [{ranker}] required label {label!r}: "
                f"before={before_required[label]} after={after_required[label]}"
            )
            if before_required[label] != 1 or after_required[label] != 1:
                print(f"FAIL anchor [{ranker}] label count for {label!r}")
                failures += 1
        before_by_text = defaultdict(list)
        after_by_text = defaultdict(list)
        for label in before.labels:
            before_by_text[label.text].append(label)
        for label in after.labels:
            after_by_text[label.text].append(label)
        for label in NAMED_LABELS:
            old = before_by_text[label][0]
            new = after_by_text[label][0]
            print(
                f"anchor [{ranker}] story {label!r}: "
                f"before_box={fmt_box(old.box)} before_crossed_by={old.foreign_splines or '[]'}; "
                f"after_box={fmt_box(new.box)} after_crossed_by={new.foreign_splines or '[]'}"
            )
    return failures


def fmt_box(box: tuple[float, float, float, float]) -> str:
    return "(" + ",".join(f"{value:.2f}" for value in box) + ")"


def check_identity(args: argparse.Namespace, baseline: Path, current: Path) -> int:
    failures = 0
    no_fan_total = 0
    no_fan_matched = 0
    changed_with_fan: set[str] = set()
    changed_without_fan: list[str] = []
    paths = tracked_graphs(repo_root())
    if args.identity_limit > 0:
        paths = paths[: args.identity_limit]
    for ranker, ranker_args in RANKERS:
        for path in paths:
            before_bytes = render_bytes(baseline, path, ranker_args, args)
            after_bytes = render_bytes(current, path, ranker_args, args)
            if before_bytes is None or after_bytes is None:
                continue
            same = before_bytes == after_bytes
            try:
                layout = json.loads(after_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                layout = {}
            labelled_fan = has_labelled_fan(layout)
            rel = str(path.relative_to(repo_root()))
            if labelled_fan:
                if not same:
                    changed_with_fan.add(rel)
            else:
                no_fan_total += 1
                if same:
                    no_fan_matched += 1
                else:
                    changed_without_fan.append(f"{rel} [{ranker}] before={sha(before_bytes)} after={sha(after_bytes)}")
    print(f"byte-identical graph/rankers without labelled fan: {no_fan_matched}/{no_fan_total}")
    if changed_with_fan:
        print("changed graphs containing a labelled fan:")
        for rel in sorted(changed_with_fan):
            print(f"  {rel}")
    else:
        print("changed graphs containing a labelled fan: []")
    if changed_without_fan:
        for item in changed_without_fan:
            print(f"FAIL changed graph without labelled fan: {item}")
        failures += len(changed_without_fan)
    return failures


def main() -> int:
    args = parse_args()
    try:
        baseline = resolve_dot(args.baseline_dot)
        current = resolve_dot(args.current_dot)
        if not args.anchor.exists():
            raise FileNotFoundError(args.anchor)
        failures = check_anchor(args, baseline, current)
        failures += check_identity(args, baseline, current)
        if failures:
            print(f"FAIL verify_junction_labels: failures={failures}")
            return 1
        print("OK verify_junction_labels")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
