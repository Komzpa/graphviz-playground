#!/usr/bin/env python3
"""Verify endpoint-label attribution on the Honda-Tokoro concentrate fixture."""

from __future__ import annotations

import json
import math
import os
import hashlib
import subprocess
import sys
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOT = ROOT / "build" / "cmd" / "dot" / "dot_builtins"
BASELINE_DOT = ROOT.parent / "baseline-1597" / "build" / "cmd" / "dot" / "dot_builtins"
FIXTURE = ROOT / "graphs" / "directed" / "honda-tokoro.gv"
CANDIDATE_MARGIN_PT = 1.0
RANKERS = (("default", ()), ("newrank=true", ("-Gnewrank=true",)))

BASELINE_SHA = "1597f2926"
UPSTREAM = {
    "off": {"labels": 17, "ambiguous": 4, "wrong": 4},
    "on": {"labels": 16, "ambiguous": 4, "wrong": 4},
}
OURS_BEFORE = {
    "off": {"labels": 17, "ambiguous": 3, "wrong": 1},
    "on": {"labels": 17, "ambiguous": 1, "wrong": 0},
}
EXPECTED = {
    "off": {"labels": 17, "ambiguous_max": 1, "wrong": 0},
    "on": {"labels": 17, "ambiguous_max": 1, "wrong": 0},
}
P1_U_IDENTITY = "n005->n002:_hldraw_::u::0"


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


@dataclass(frozen=True)
class LabelReport:
    label: Label
    candidate_count: int
    own_distance: float
    nearest_foreign_distance: float
    nearest_edge_index: int
    nearest_edge_owner: str
    side_status: str
    box_hits: int

    @property
    def ambiguous(self) -> bool:
        return self.candidate_count > 1

    @property
    def wrong_owner(self) -> bool:
        return self.nearest_edge_index != self.label.edge_index


def render_json(
    dot: Path = DOT,
    fixture: Path = FIXTURE,
    *,
    concentrate: bool,
    ranker_args: tuple[str, ...] = (),
    output_format: str = "json",
) -> dict:
    if not dot.exists():
        sys.exit(f"missing dot binary: {dot.relative_to(ROOT)}")
    if not fixture.exists():
        sys.exit(f"missing fixture: {fixture.relative_to(ROOT)}")

    mode = "true" if concentrate else "false"
    command = [
        "bash",
        "-lc",
        "ulimit -v 2097152; exec timeout 20 \"$@\"",
        "dot-ulimit",
        str(dot),
        f"-Gconcentrate={mode}",
        *ranker_args,
        f"-T{output_format}",
        str(fixture),
    ]
    output = subprocess.check_output(command, cwd=ROOT)
    return json.loads(output)


def render_bytes(
    dot: Path,
    fixture: Path,
    *,
    concentrate: bool,
    ranker_args: tuple[str, ...] = (),
    output_format: str = "xdot",
) -> bytes | None:
    mode = "true" if concentrate else "false"
    proc = subprocess.run(
        [
            "bash",
            "-lc",
            "ulimit -v 2097152; exec timeout 20 \"$@\"",
            "dot-ulimit",
            str(dot),
            f"-Gconcentrate={mode}",
            *ranker_args,
            f"-T{output_format}",
            str(fixture),
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


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


def cross(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    return first[0] * second[1] - first[1] * second[0]


def box_for_label(label: Label, padding: float = 0.0) -> tuple[float, float, float, float]:
    x, y = label.center
    return (
        x - label.width / 2.0 - padding,
        y - label.height / 2.0 - padding,
        x + label.width / 2.0 + padding,
        y + label.height / 2.0 + padding,
    )


def point_in_box(point: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orient(p, q, r):
        value = cross((q[0] - p[0], q[1] - p[1]), (r[0] - p[0], r[1] - p[1]))
        if abs(value) < 1e-9:
            return 0
        return 1 if value > 0 else -1

    def on_segment(p, q, r):
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9
        )

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and on_segment(a, c, b))
        or (o2 == 0 and on_segment(a, d, b))
        or (o3 == 0 and on_segment(c, a, d))
        or (o4 == 0 and on_segment(c, b, d))
    )


def segment_intersects_box(
    start: tuple[float, float],
    end: tuple[float, float],
    box: tuple[float, float, float, float],
) -> bool:
    if point_in_box(start, box) or point_in_box(end, box):
        return True
    x0, y0, x1, y1 = box
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return any(
        segments_intersect(start, end, corners[index], corners[(index + 1) % 4])
        for index in range(4)
    )


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
            font_size = float(edge.get("fontsize", 14.0))
            for operation in edge.get(stream, []):
                if operation.get("op") == "F":
                    font_size = float(operation["size"])
                    continue
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
                        width=float(operation.get("width", 0.0)),
                        height=font_size,
                    )
                )
    return rows


def edge_owner_names(layout: dict) -> list[str]:
    names = {node["_gvid"]: node["name"] for node in layout["objects"]}
    return [f"{names[edge['tail']]}->{names[edge['head']]}" for edge in layout["edges"]]


def shared_endpoint_groups(layout: dict) -> dict[tuple[str, int, str], list[int]]:
    groups: dict[tuple[str, int, str], list[int]] = {}
    if "edges" not in layout:
        return groups
    for edge_index, edge in enumerate(layout["edges"]):
        for endpoint, attr in (("head", "samehead"), ("tail", "sametail")):
            group = edge.get(attr, "")
            if not group:
                continue
            node_index = edge[endpoint]
            groups.setdefault((endpoint, node_index, group), []).append(edge_index)
    return {key: value for key, value in groups.items() if len(value) > 1}


def group_for_label(label: Label, layout: dict) -> tuple[str, int, str] | None:
    edge = layout["edges"][label.edge_index]
    if label.stream == "_hldraw_" and edge.get("samehead"):
        return ("head", edge["head"], edge["samehead"])
    if label.stream == "_tldraw_" and edge.get("sametail"):
        return ("tail", edge["tail"], edge["sametail"])
    return None


def terminal_vector(
    edge: dict,
    endpoint: str,
) -> tuple[float, float] | None:
    points = []
    for operation in edge.get("_draw_", []):
        if operation.get("op") in {"b", "B"}:
            points = [tuple(map(float, point)) for point in operation["points"]]
            break
        if operation.get("op") == "L":
            points = [tuple(map(float, point)) for point in operation["points"]]
            break
    if len(points) < 2:
        return None
    if endpoint == "head":
        end = points[-1]
        start = points[0]
    else:
        end = points[0]
        start = points[-1]
    return (start[0] - end[0], start[1] - end[1])


def label_side_status(label: Label, layout: dict) -> str:
    group = group_for_label(label, layout)
    groups = shared_endpoint_groups(layout)
    if group is None or group not in groups:
        return "not-shared"
    edge = layout["edges"][label.edge_index]
    own_vector = terminal_vector(edge, group[0])
    if own_vector is None:
        return "unknown"
    endpoint_point = tuple(
        map(float, edge.get("pos", "0,0").split(" ")[-1 if group[0] == "head" else 0].split(","))
    )
    label_vector = (
        label.center[0] - endpoint_point[0],
        label.center[1] - endpoint_point[1],
    )
    label_cross = cross(own_vector, label_vector)
    if abs(label_cross) <= 1e-6:
        return "unknown"
    sibling_sides = []
    for sibling_index in groups[group]:
        if sibling_index == label.edge_index:
            continue
        sibling_vector = terminal_vector(layout["edges"][sibling_index], group[0])
        if sibling_vector is None:
            continue
        sibling_cross = cross(own_vector, sibling_vector)
        if abs(sibling_cross) > 1e-6:
            sibling_sides.append(sibling_cross)
    if not sibling_sides:
        return "unknown"
    return "outer" if all(label_cross * side > 0.0 for side in sibling_sides) else "inner"


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
                side_status=label_side_status(label, layout),
                box_hits=sum(
                    1
                    for index, segments in enumerate(segments_by_edge)
                    for start, end in segments
                    if segment_intersects_box(start, end, box_for_label(label, padding=0.0))
                ),
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
            f"nearest={report.nearest_edge_owner} "
            f"side={report.side_status} box_hits={report.box_hits}"
        )


def summary(label_reports: list[LabelReport]) -> dict[str, int]:
    return {
        "labels": len(label_reports),
        "ambiguous": sum(report.ambiguous for report in label_reports),
        "wrong": sum(report.wrong_owner for report in label_reports),
        "wrong_side": sum(report.side_status == "inner" for report in label_reports),
        "box_hits": sum(report.box_hits > 0 for report in label_reports),
    }


def tracked_graphs() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "graphs", "tests"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [
        ROOT / rel
        for rel in proc.stdout.splitlines()
        if Path(rel).suffix in {".dot", ".gv"}
    ]


def has_shared_endpoint_port_label(layout: dict) -> bool:
    groups = shared_endpoint_groups(layout)
    if not groups:
        return False
    for label in label_rows(layout):
        group = group_for_label(label, layout)
        if group in groups:
            return True
    return False


def edge_statements(text: str) -> list[str]:
    statements = []
    current = []
    bracket_depth = 0
    for char in text:
        current.append(char)
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth > 0:
            bracket_depth -= 1
        elif char == ";" and bracket_depth == 0:
            statements.append("".join(current))
            current = []
    if current:
        statements.append("".join(current))
    return statements


def source_has_shared_endpoint_port_label(path: Path) -> bool:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    groups: dict[tuple[str, str], list[bool]] = {}
    for statement in edge_statements(text):
        if "->" not in statement or "[" not in statement:
            continue
        for endpoint, group_attr, label_attr in (
            ("head", "samehead", "headlabel"),
            ("tail", "sametail", "taillabel"),
        ):
            match = re.search(rf"\b{group_attr}\s*=\s*\"?([^\",\]\s]+)", statement)
            if not match:
                continue
            has_label = re.search(rf"\b{label_attr}\s*=", statement) is not None
            groups.setdefault((endpoint, match.group(1)), []).append(has_label)
    return any(len(members) > 1 and any(members) for members in groups.values())


def digest(data: bytes | None) -> str:
    if data is None:
        return "<skipped>"
    return hashlib.sha256(data).hexdigest()


def identity_sweep() -> tuple[int, dict[str, tuple[int, int]], list[str]]:
    if not BASELINE_DOT.exists():
        print("identity sweep: baseline dot missing; skipped")
        return 0, {}, []

    failures = 0
    stats = {ranker: [0, 0] for ranker, _ in RANKERS}
    changed_shared: set[str] = set()
    changed_unexpected: list[str] = []
    paths = tracked_graphs()
    shared_by_source = {path for path in paths if source_has_shared_endpoint_port_label(path)}

    def compare_one(item: tuple[str, tuple[str, ...], Path]) -> tuple[str, bool, bool, str]:
        ranker, ranker_args, path = item
        before = render_bytes(
            BASELINE_DOT, path, concentrate=True, ranker_args=ranker_args
        )
        after = render_bytes(DOT, path, concentrate=True, ranker_args=ranker_args)
        rel = str(path.relative_to(ROOT))
        if before is None or after is None:
            return ranker, path in shared_by_source, True, rel
        if before != after:
            before_hashes = {digest(before)}
            after_hashes = {digest(after)}
            for _ in range(2):
                retry_before = render_bytes(
                    BASELINE_DOT, path, concentrate=True, ranker_args=ranker_args
                )
                retry_after = render_bytes(
                    DOT, path, concentrate=True, ranker_args=ranker_args
                )
                if retry_before is not None:
                    before_hashes.add(digest(retry_before))
                if retry_after is not None:
                    after_hashes.add(digest(retry_after))
            if before_hashes & after_hashes:
                return ranker, path in shared_by_source, True, rel
        return ranker, path in shared_by_source, before == after, (
            f"{rel} [{ranker}] before={digest(before)} after={digest(after)}"
        )

    tasks = [
        (ranker, ranker_args, path)
        for ranker, ranker_args in RANKERS
        for path in paths
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        for done, (ranker, shared_port_label, same, detail) in enumerate(
            executor.map(compare_one, tasks), start=1
        ):
            if done % 200 == 0:
                print(f"identity progress graph/rankers={done}/{len(tasks)}", flush=True)
            if shared_port_label:
                if not same:
                    changed_shared.add(detail.split(" [", 1)[0])
                continue
            stats[ranker][1] += 1
            if same:
                stats[ranker][0] += 1
            else:
                changed_unexpected.append(detail)
    for ranker, (matched, total) in stats.items():
        print(
            f"byte-identical graph/rankers without shared endpoint port labels "
            f"[{ranker}]: {matched}/{total}"
        )
    if changed_shared:
        print("changed graphs with shared endpoint port labels:")
        for rel in sorted(changed_shared):
            print(f"  {rel}")
    else:
        print("changed graphs with shared endpoint port labels: []")
    for item in changed_unexpected:
        print(f"FAIL unexpected identity change: {item}")
    failures += len(changed_unexpected)
    return failures, {key: tuple(value) for key, value in stats.items()}, sorted(changed_shared)


def main() -> int:
    mode_reports = {}
    baseline_reports = {}
    try:
        for mode, concentrate in (("off", False), ("on", True)):
            mode_reports[mode] = reports(render_json(concentrate=concentrate))
            if BASELINE_DOT.exists():
                baseline_reports[mode] = reports(
                    render_json(BASELINE_DOT, concentrate=concentrate)
                )
    except (subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as err:
        print(f"FAIL verify_port_labels: {err}")
        return 1

    failures = []
    print(
        "# honda-tokoro endpoint labels candidate-owner rule "
        f"limit=2*nearest+{CANDIDATE_MARGIN_PT:.1f}pt"
    )
    print("mode source labels ambiguous wrong_owner wrong_side box_hit_labels")
    for mode in ("off", "on"):
        current_summary = summary(mode_reports[mode])
        print(
            f"{mode} current {current_summary['labels']} "
            f"{current_summary['ambiguous']} {current_summary['wrong']} "
            f"{current_summary['wrong_side']} {current_summary['box_hits']}"
        )
        if mode in baseline_reports:
            before_summary = summary(baseline_reports[mode])
            print(
                f"{mode} ours@{BASELINE_SHA} {before_summary['labels']} "
                f"{before_summary['ambiguous']} {before_summary['wrong']} "
                f"{before_summary['wrong_side']} {before_summary['box_hits']}"
            )
            if current_summary["box_hits"] > before_summary["box_hits"]:
                failures.append(
                    f"{mode} label-box-on-spline intersections increased "
                    f"{before_summary['box_hits']}->{current_summary['box_hits']}"
                )
            if current_summary["wrong_side"] > before_summary["wrong_side"]:
                failures.append(
                    f"{mode} shared endpoint wrong-side labels increased "
                    f"{before_summary['wrong_side']}->{current_summary['wrong_side']}"
                )
        print(
            f"{mode} upstream {UPSTREAM[mode]['labels']} "
            f"{UPSTREAM[mode]['ambiguous']} {UPSTREAM[mode]['wrong']} n/a n/a"
        )
        print_reports(f"# honda-tokoro concentrate={str(mode == 'on').lower()}", mode_reports[mode])

    for mode in ("off", "on"):
        current_summary = summary(mode_reports[mode])
        expected = EXPECTED[mode]
        if current_summary["labels"] != expected["labels"]:
            failures.append(
                f"{mode} label count changed from {expected['labels']} "
                f"to {current_summary['labels']}"
            )
        if current_summary["wrong"] != expected["wrong"]:
            failures.append(f"{mode} wrong-owner labels={current_summary['wrong']}")
        if current_summary["ambiguous"] > expected["ambiguous_max"]:
            failures.append(
                f"{mode} ambiguous labels {current_summary['ambiguous']} "
                f"> {expected['ambiguous_max']}"
            )

    for mode, label_reports in mode_reports.items():
        p1_reports = [
            report for report in label_reports if report.label.identity == P1_U_IDENTITY
        ]
        if len(p1_reports) != 1:
            failures.append(f"{mode} p1 :u: label count={len(p1_reports)}")
        elif p1_reports[0].candidate_count != 1:
            failures.append(
                f"{mode} p1 :u: candidates={p1_reports[0].candidate_count}"
            )

    identity_failures, _, _ = identity_sweep()
    if identity_failures:
        failures.append(f"identity sweep failures={identity_failures}")

    if failures:
        print("FAIL verify_port_labels:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print("OK verify_port_labels")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("LC_ALL", "C")
    sys.exit(main())
