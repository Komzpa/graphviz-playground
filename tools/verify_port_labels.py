#!/usr/bin/env python3
"""Verify endpoint-label attribution on the Honda-Tokoro concentrate fixture."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOT = ROOT / "build" / "cmd" / "dot" / "dot_builtins"
FIXTURE = ROOT / "graphs" / "directed" / "honda-tokoro.gv"
CANDIDATE_MARGIN_PT = 1.0
UPSTREAM_AMBIGUOUS = 2

# Drawn -Tjson baseline at 3cade507e with -Gconcentrate=true.
BASELINE_SHA = "3cade507e"
BASELINE_LABELS = 17
BASELINE_AMBIGUOUS = 4
BASELINE_WRONG_OWNER = 1
BASELINE_CANDIDATES = {
    "n005->n002:_hldraw_::u::0": 2,
    "n007->n006:_hldraw_::u::0": 2,
    "n012->n011:_hldraw_::s::0": 2,
    "n016->n015:_hldraw_::u::0": 2,
}


@dataclass(frozen=True)
class Label:
    identity: str
    edge_index: int
    tail: str
    head: str
    stream: str
    text: str
    center: tuple[float, float]


@dataclass(frozen=True)
class LabelReport:
    label: Label
    candidate_count: int
    own_distance: float
    nearest_foreign_distance: float
    nearest_edge_index: int
    nearest_edge_owner: str

    @property
    def ambiguous(self) -> bool:
        return self.candidate_count > 1

    @property
    def wrong_owner(self) -> bool:
        return self.nearest_edge_index != self.label.edge_index


def render_json(dot: Path = DOT, fixture: Path = FIXTURE) -> dict:
    if not dot.exists():
        sys.exit(f"missing dot binary: {dot.relative_to(ROOT)}")
    if not fixture.exists():
        sys.exit(f"missing fixture: {fixture.relative_to(ROOT)}")

    command = [
        "bash",
        "-lc",
        "ulimit -v 2097152; exec timeout 20 \"$0\" -Gconcentrate=true -Tjson \"$1\"",
        str(dot),
        str(fixture),
    ]
    output = subprocess.check_output(command, cwd=ROOT)
    return json.loads(output)


def bezier(points: list[tuple[float, float]], t: float) -> tuple[float, float]:
    omt = 1.0 - t
    return (
        omt**3 * points[0][0]
        + 3.0 * omt**2 * t * points[1][0]
        + 3.0 * omt * t**2 * points[2][0]
        + t**3 * points[3][0],
        omt**3 * points[0][1]
        + 3.0 * omt**2 * t * points[1][1]
        + 3.0 * omt * t**2 * points[2][1]
        + t**3 * points[3][1],
    )


def point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return math.dist(point, start)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def edge_segments(edge: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    for operation in edge.get("_draw_", []):
        if operation.get("op") in {"b", "B"}:
            points = [tuple(map(float, point)) for point in operation["points"]]
            for index in range(0, len(points) - 3, 3):
                previous = points[index]
                for step in range(1, 81):
                    current = bezier(points[index : index + 4], step / 80.0)
                    segments.append((previous, current))
                    previous = current
        elif operation.get("op") == "L":
            points = [tuple(map(float, point)) for point in operation["points"]]
            segments.extend(zip(points, points[1:]))
    return segments


def distance_to_edge(
    point: tuple[float, float],
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> float:
    return min(
        (point_segment_distance(point, start, end) for start, end in segments),
        default=math.inf,
    )


def label_rows(layout: dict) -> list[Label]:
    names = {node["_gvid"]: node["name"] for node in layout["objects"]}
    rows = []
    per_edge_stream_text: dict[tuple[int, str, str], int] = {}
    for edge_index, edge in enumerate(layout["edges"]):
        tail = names[edge["tail"]]
        head = names[edge["head"]]
        for stream in ("_ldraw_", "_hldraw_", "_tldraw_"):
            for operation in edge.get(stream, []):
                if operation.get("op") != "T":
                    continue
                text = operation["text"]
                key = (edge_index, stream, text)
                occurrence = per_edge_stream_text.get(key, 0)
                per_edge_stream_text[key] = occurrence + 1
                rows.append(
                    Label(
                        identity=f"{tail}->{head}:{stream}:{text}:{occurrence}",
                        edge_index=edge_index,
                        tail=tail,
                        head=head,
                        stream=stream,
                        text=text,
                        center=tuple(map(float, operation["pt"])),
                    )
                )
    return rows


def edge_owner_names(layout: dict) -> list[str]:
    names = {node["_gvid"]: node["name"] for node in layout["objects"]}
    return [f"{names[edge['tail']]}->{names[edge['head']]}" for edge in layout["edges"]]


def reports(layout: dict) -> list[LabelReport]:
    rows = label_rows(layout)
    if not any(label.stream in {"_hldraw_", "_tldraw_"} for label in rows):
        raise RuntimeError("endpoint label streams are empty")

    segments_by_edge = [edge_segments(edge) for edge in layout["edges"]]
    owner_names = edge_owner_names(layout)
    label_reports = []
    for label in rows:
        distances = [
            distance_to_edge(label.center, segments)
            for segments in segments_by_edge
        ]
        nearest_distance = min(distances, default=math.inf)
        nearest_edge_index = min(range(len(distances)), key=distances.__getitem__)
        candidate_limit = 2.0 * nearest_distance + CANDIDATE_MARGIN_PT
        candidate_count = sum(distance < candidate_limit for distance in distances)
        foreign_distances = [
            distance
            for index, distance in enumerate(distances)
            if index != label.edge_index
        ]
        label_reports.append(
            LabelReport(
                label=label,
                candidate_count=candidate_count,
                own_distance=distances[label.edge_index],
                nearest_foreign_distance=min(foreign_distances, default=math.inf),
                nearest_edge_index=nearest_edge_index,
                nearest_edge_owner=owner_names[nearest_edge_index],
            )
        )
    return label_reports


def print_reports(title: str, label_reports: list[LabelReport]) -> None:
    print(title)
    print(
        "stream label owner anchor candidate_count own_edge_distance_pt "
        "nearest_foreign_distance_pt nearest_edge"
    )
    for report in label_reports:
        label = report.label
        anchor = f"{label.center[0]:.2f},{label.center[1]:.2f}"
        print(
            f"{label.stream} {label.text!r} {label.tail}->{label.head} "
            f"anchor={anchor} candidates={report.candidate_count} "
            f"own={report.own_distance:.2f} "
            f"foreign={report.nearest_foreign_distance:.2f} "
            f"nearest={report.nearest_edge_owner}"
        )


def main() -> int:
    try:
        label_reports = reports(render_json())
    except (subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as err:
        print(f"FAIL verify_port_labels: {err}")
        return 1

    print_reports(
        f"# honda-tokoro concentrate=true current candidate-owner rule "
        f"limit=2*nearest+{CANDIDATE_MARGIN_PT:.1f}pt",
        label_reports,
    )

    ambiguous = [report for report in label_reports if report.ambiguous]
    wrong_owner = [report for report in label_reports if report.wrong_owner]
    regressions = [
        report
        for report in label_reports
        if report.candidate_count
        > BASELINE_CANDIDATES.get(report.label.identity, 1)
    ]

    print(
        f"summary labels={len(label_reports)} ambiguous={len(ambiguous)} "
        f"wrong_owner={len(wrong_owner)} upstream_ambiguous={UPSTREAM_AMBIGUOUS} "
        f"baseline={BASELINE_SHA}:labels={BASELINE_LABELS},"
        f"ambiguous={BASELINE_AMBIGUOUS},wrong_owner={BASELINE_WRONG_OWNER}"
    )

    failures = []
    if len(label_reports) != BASELINE_LABELS:
        failures.append(
            f"label count changed from {BASELINE_LABELS} to {len(label_reports)}"
        )
    if wrong_owner:
        failures.append("nearest edge is not the owning edge")
    if len(ambiguous) >= UPSTREAM_AMBIGUOUS:
        failures.append(
            f"ambiguous labels {len(ambiguous)} is not below upstream {UPSTREAM_AMBIGUOUS}"
        )
    if regressions:
        failures.append(f"candidate-count regressions against {BASELINE_SHA}")

    if failures:
        print("FAIL verify_port_labels:")
        for failure in failures:
            print(f"  {failure}")
        for report in wrong_owner:
            print(f"  wrong-owner {report.label.identity}")
        for report in regressions:
            print(
                f"  regression {report.label.identity}: "
                f"{BASELINE_CANDIDATES.get(report.label.identity, 1)}"
                f"->{report.candidate_count}"
            )
        return 1

    print("OK verify_port_labels")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("LC_ALL", "C")
    sys.exit(main())
