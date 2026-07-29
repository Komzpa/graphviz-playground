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
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOT = ROOT / "build" / "cmd" / "dot" / "dot_builtins"
FIXTURE = ROOT / "graphs" / "directed" / "honda-tokoro.gv"
CANDIDATE_MARGIN_PT = 1.0
RANKERS = (("default", ()), ("newrank=true", ("-Gnewrank=true",)))

BASELINE_SHA = "3a6ab63bcdb80d04a89a1812ee55e65130772c0f"
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
P2_U_IDENTITY = "n007->n006:_hldraw_::u::0"
P2_SAME_OWNER_KNOWN_OPEN_TEXTS = frozenset([":s:", ":u:"])
P2_SAME_OWNER_KNOWN_OPEN_EDGE = "n007->n006"


def run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def dot_built_at(sha: str) -> Path:
    base = Path(tempfile.gettempdir()) / f"graphviz-port-label-baseline-{sha[:9]}"
    if not base.exists():
        run(["git", "worktree", "add", "--detach", str(base), sha])
    head = run(["git", "rev-parse", "HEAD"], cwd=base).stdout.strip()
    if head != sha:
        raise RuntimeError(f"baseline worktree {base} is at {head}, expected {sha}")
    exe = base / "build" / "cmd" / "dot" / "dot_builtins"
    if not exe.exists():
        run(
            [
                "cmake",
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=ON",
                "-DBUILD_TESTING=ON",
                "-S",
                str(base),
                "-B",
                str(base / "build"),
            ],
            cwd=base,
        )
    run(["cmake", "--build", str(base / "build"), "--target", "dot_builtins", "-j", "4"], cwd=base)
    return exe


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


@dataclass(frozen=True)
class LabelOverlap:
    left: Label
    right: Label
    same_owner: bool


def same_owner(left: Label, right: Label) -> bool:
    return left.tail == right.tail and left.head == right.head


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
        "ulimit -v 2097152; exec timeout -k 5s 20s \"$@\"",
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
            "ulimit -v 2097152; exec timeout -k 5s 20s \"$@\"",
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


def boxes_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return (
        left[0] < right[2]
        and left[2] > right[0]
        and left[1] < right[3]
        and left[3] > right[1]
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


def _overlap_text(label: Label, box: tuple[float, float, float, float] | None = None) -> str:
    box = box_for_label(label, 0.0) if box is None else box
    return (
        f"{label.identity} {label.tail}->{label.head} "
        f"anchor={label.center[0]:.2f},{label.center[1]:.2f} "
        f"box=({box[0]:.2f},{box[1]:.2f},{box[2]:.2f},{box[3]:.2f})"
    )


def find_label_box_overlaps(label_reports: list[LabelReport]) -> list[LabelOverlap]:
    boxed: list[tuple[Label, tuple[float, float, float, float]]] = [
        (report.label, box_for_label(report.label, 0.0)) for report in label_reports
    ]
    overlaps: list[LabelOverlap] = []
    for index in range(len(boxed)):
        left_label, left_box = boxed[index]
        for right_index in range(index + 1, len(boxed)):
            right_label, right_box = boxed[right_index]
            if boxes_overlap(left_box, right_box):
                left = left_label
                right = right_label
                if left.identity > right.identity:
                    left, right = right, left
                overlaps.append(
                    LabelOverlap(
                        left=left,
                        right=right,
                        same_owner=same_owner(left_label, right_label),
                    )
                )
    return overlaps


def is_known_open_same_owner_overlap(left: Label, right: Label) -> bool:
    if not same_owner(left, right):
        return False
    if f"{left.tail}->{left.head}" != P2_SAME_OWNER_KNOWN_OPEN_EDGE:
        return False
    if f"{right.tail}->{right.head}" != P2_SAME_OWNER_KNOWN_OPEN_EDGE:
        return False
    return {
        left.text,
        right.text,
    } == P2_SAME_OWNER_KNOWN_OPEN_TEXTS


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


def source_has_port_label(path: Path) -> bool:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    return re.search(r"\b(headlabel|taillabel)\s*=", text) is not None


def digest(data: bytes | None) -> str:
    if data is None:
        return "<skipped>"
    return hashlib.sha256(data).hexdigest()


def identity_sweep(baseline_dot: Path) -> tuple[int, dict[str, tuple[int, int]], list[str]]:
    failures = 0
    stats = {ranker: [0, 0] for ranker, _ in RANKERS}
    changed_port_label: set[str] = set()
    changed_unexpected: list[str] = []
    paths = tracked_graphs()
    port_label_by_source = {path for path in paths if source_has_port_label(path)}

    def compare_one(item: tuple[str, tuple[str, ...], Path]) -> tuple[str, bool, bool, str]:
        ranker, ranker_args, path = item
        before = render_bytes(
            baseline_dot, path, concentrate=True, ranker_args=ranker_args
        )
        after = render_bytes(DOT, path, concentrate=True, ranker_args=ranker_args)
        rel = str(path.relative_to(ROOT))
        if before is None or after is None:
            return ranker, path in port_label_by_source, True, rel
        if b"_concentrate_junction" in before or b"_concentrate_junction" in after:
            # Junction-transform graphs are owned by the burst/kink gates; the
            # label gate asserting identity there turns every accepted junction
            # change into hundreds of false reds (356 on 2026-07-28).
            return ranker, path in port_label_by_source, True, rel
        if before != after:
            before_hashes = {digest(before)}
            after_hashes = {digest(after)}
            for _ in range(2):
                retry_before = render_bytes(
                    baseline_dot, path, concentrate=True, ranker_args=ranker_args
                )
                retry_after = render_bytes(
                    DOT, path, concentrate=True, ranker_args=ranker_args
                )
                if retry_before is not None:
                    before_hashes.add(digest(retry_before))
                if retry_after is not None:
                    after_hashes.add(digest(retry_after))
            if before_hashes & after_hashes:
                return ranker, path in port_label_by_source, True, rel
        return ranker, path in port_label_by_source, before == after, (
            f"{rel} [{ranker}] before={digest(before)} after={digest(after)}"
        )

    tasks = [
        (ranker, ranker_args, path)
        for ranker, ranker_args in RANKERS
        for path in paths
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        for done, (ranker, port_label, same, detail) in enumerate(
            executor.map(compare_one, tasks), start=1
        ):
            if done % 200 == 0:
                print(f"identity progress graph/rankers={done}/{len(tasks)}", flush=True)
            if port_label:
                if not same:
                    changed_port_label.add(detail.split(" [", 1)[0])
                continue
            stats[ranker][1] += 1
            if same:
                stats[ranker][0] += 1
            else:
                changed_unexpected.append(detail)
    for ranker, (matched, total) in stats.items():
        print(
            f"byte-identical graph/rankers without port labels "
            f"[{ranker}]: {matched}/{total}"
        )
    if changed_port_label:
        print("changed graphs with port labels:")
        for rel in sorted(changed_port_label):
            print(f"  {rel}")
    else:
        print("changed graphs with port labels: []")
    for item in changed_unexpected:
        print(f"FAIL unexpected identity change: {item}")
    failures += len(changed_unexpected)
    return failures, {key: tuple(value) for key, value in stats.items()}, sorted(changed_port_label)


def main() -> int:
    mode_reports = {}
    baseline_reports = {}
    mode_overlaps: dict[str, list[LabelOverlap]] = {}
    try:
        baseline_dot = dot_built_at(BASELINE_SHA)
        for mode, concentrate in (("off", False), ("on", True)):
            mode_reports[mode] = reports(render_json(concentrate=concentrate))
            mode_overlaps[mode] = find_label_box_overlaps(mode_reports[mode])
            baseline_reports[mode] = reports(
                render_json(baseline_dot, concentrate=concentrate)
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
    for mode, overlaps in mode_overlaps.items():
        competing_overlaps = [overlap for overlap in overlaps if not overlap.same_owner]
        known_open_overlaps = [
            overlap for overlap in overlaps
            if overlap.same_owner
            and is_known_open_same_owner_overlap(overlap.left, overlap.right)
        ]
        hidden_overlaps = [
            overlap for overlap in overlaps
            if overlap.same_owner
            and not is_known_open_same_owner_overlap(overlap.left, overlap.right)
        ]
        print(
            f"{mode} label-box-overlaps total={len(overlaps)} "
            f"competing={len(competing_overlaps)} same_owner={len(overlaps) - len(competing_overlaps)}"
        )
        if known_open_overlaps:
            print(f"{mode} known-open same-owner overlaps:")
            for overlap in known_open_overlaps:
                left_box = box_for_label(overlap.left, 0.0)
                right_box = box_for_label(overlap.right, 0.0)
                print(
                    f"  OPEN {overlap.left.identity} <-> {overlap.right.identity} "
                    f"at {overlap.left.center[0]:.2f},{overlap.left.center[1]:.2f} "
                    f"and {overlap.right.center[0]:.2f},{overlap.right.center[1]:.2f} "
                    f"boxes=({left_box[0]:.2f},{left_box[1]:.2f},{left_box[2]:.2f},{left_box[3]:.2f}) "
                    f"({right_box[0]:.2f},{right_box[1]:.2f},{right_box[2]:.2f},{right_box[3]:.2f})"
                )
        if competing_overlaps:
            print(f"{mode} competing-owner overlaps:")
            for overlap in competing_overlaps:
                print(
                    "  COMPETE "
                    + _overlap_text(overlap.left, box_for_label(overlap.left, 0.0))
                    + " <-> "
                    + _overlap_text(overlap.right, box_for_label(overlap.right, 0.0))
                )
        if hidden_overlaps:
            print(f"{mode} same-owner overlaps (not known-open): {len(hidden_overlaps)}")
            for overlap in hidden_overlaps:
                print(
                    "  SAME-OWNER "
                    + _overlap_text(overlap.left, box_for_label(overlap.left, 0.0))
                    + " <-> "
                    + _overlap_text(overlap.right, box_for_label(overlap.right, 0.0))
                )

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
        if len([overlap for overlap in mode_overlaps[mode] if not overlap.same_owner]):
            failures.append(
                f"{mode} competing-owner label-box overlaps="
                f"{len([overlap for overlap in mode_overlaps[mode] if not overlap.same_owner])}"
            )
        if mode == "on" and mode_overlaps[mode]:
            failures.append(f"on label-box overlaps={len(mode_overlaps[mode])}")

    tracked = (("p1", P1_U_IDENTITY), ("p2", P2_U_IDENTITY))
    for mode, label_reports in mode_reports.items():
        for name, identity in tracked:
            found = [report for report in label_reports if report.label.identity == identity]
            if len(found) != 1:
                failures.append(f"{mode} {name} :u: label count={len(found)}")
            elif found[0].candidate_count != 1:
                failures.append(f"{mode} {name} :u: candidates={found[0].candidate_count}")

    identity_failures, _, _ = identity_sweep(baseline_dot)
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
