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
MARGIN_PT = 1.0

# Ambiguous on 73b8ef683 with MARGIN_PT=2.0. This lets the gate reject new
# ambiguous endpoint labels without requiring an old binary in the test run.
BASELINE_AMBIGUOUS = {
    "n007->n006:_hldraw_::u::0",
    "n012->n011:_hldraw_::s::0",
    "n016->n015:_hldraw_::u::0",
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
    width: float
    height: float

    @property
    def box(self) -> tuple[float, float, float, float]:
        x, y = self.center
        return (
            x - self.width / 2.0,
            y - self.height / 2.0,
            x + self.width / 2.0,
            y + self.height / 2.0,
        )


def render_json() -> dict:
    if not DOT.exists():
        sys.exit(f"missing dot binary: {DOT.relative_to(ROOT)}")
    if not FIXTURE.exists():
        sys.exit(f"missing fixture: {FIXTURE.relative_to(ROOT)}")

    command = [
        "bash",
        "-lc",
        "ulimit -v 2097152; exec timeout 20 \"$0\" -Gconcentrate=true -Tjson \"$1\"",
        str(DOT),
        str(FIXTURE),
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


def labels(layout: dict) -> list[Label]:
    names = {node["_gvid"]: node["name"] for node in layout["objects"]}
    labels_seen = []
    per_edge_stream_text: dict[tuple[int, str, str], int] = {}
    for edge_index, edge in enumerate(layout["edges"]):
        tail = names[edge["tail"]]
        head = names[edge["head"]]
        for stream in ("_ldraw_", "_hldraw_", "_tldraw_"):
            font_size = 14.0
            for operation in edge.get(stream, []):
                if operation.get("op") == "F":
                    font_size = float(operation["size"])
                if operation.get("op") != "T":
                    continue
                text = operation["text"]
                key = (edge_index, stream, text)
                occurrence = per_edge_stream_text.get(key, 0)
                per_edge_stream_text[key] = occurrence + 1
                labels_seen.append(
                    Label(
                        identity=f"{tail}->{head}:{stream}:{text}:{occurrence}",
                        edge_index=edge_index,
                        tail=tail,
                        head=head,
                        stream=stream,
                        text=text,
                        center=tuple(map(float, operation["pt"])),
                        width=float(operation.get("width", 0.0)),
                        height=font_size,
                    )
                )
    return labels_seen


def main() -> int:
    layout = render_json()
    label_rows = labels(layout)
    if not any(label.stream in {"_hldraw_", "_tldraw_"} for label in label_rows):
        print("FAIL verify_port_labels: endpoint label streams are empty")
        return 1

    segments_by_edge = [edge_segments(edge) for edge in layout["edges"]]
    ambiguous = set()
    print(
        "stream label owner box own_edge_distance_pt nearest_foreign_distance_pt "
        f"delta_pt ambiguous_margin_pt={MARGIN_PT:.2f}"
    )
    for label in label_rows:
        own = distance_to_edge(label.center, segments_by_edge[label.edge_index])
        foreign_distances = [
            distance_to_edge(label.center, segments)
            for index, segments in enumerate(segments_by_edge)
            if index != label.edge_index and segments
        ]
        nearest_foreign = min(foreign_distances, default=math.inf)
        delta = nearest_foreign - own
        is_ambiguous = delta < MARGIN_PT
        if is_ambiguous:
            ambiguous.add(label.identity)
        box = ",".join(f"{value:.2f}" for value in label.box)
        print(
            f"{label.stream} {label.text!r} {label.tail}->{label.head} "
            f"box=[{box}] own={own:.2f} foreign={nearest_foreign:.2f} "
            f"delta={delta:.2f} ambiguous={'yes' if is_ambiguous else 'no'}"
        )

    new_ambiguous = ambiguous - BASELINE_AMBIGUOUS
    if new_ambiguous:
        print("FAIL verify_port_labels: new ambiguous labels:")
        for identity in sorted(new_ambiguous):
            print(f"  {identity}")
        return 1

    print(
        f"OK verify_port_labels: labels={len(label_rows)} "
        f"ambiguous={len(ambiguous)} baseline_allowed={len(BASELINE_AMBIGUOUS)}"
    )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("LC_ALL", "C")
    sys.exit(main())
