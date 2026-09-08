#!/usr/bin/env python3
"""Verify flat same-rank edges have room for arrows at both ends."""

from __future__ import annotations

import concurrent.futures
import filecmp
import json
import contextlib
import math
import os
import resource
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_COMMIT = "f8f30878f"
FLAT_BOTH_ARROW_MARGIN = 2.0
FIXTURE = Path("tests/graphs/concentrate-demo/same-rank-equivalent-edges-concentrate.dot")
MAX_WORKERS = 2
POINT_EPSILON = 0.02
RANKERS = (("default", ()), ("newrank=true", ("-Gnewrank=true",)))
RENDER_TIMEOUT_SECONDS = 10
ULIMIT_V_KB = 2 * 1024 * 1024
STOCK_REPRO = "strict digraph { { rank=same; a; b }  a -> b [dir=both] }\n"


@dataclass(frozen=True)
class FlatArrowRoom:
    path: Path
    ranker: str
    tail: str
    head: str
    gap: float
    tail_arrow: float
    head_arrow: float

    @property
    def clearance(self) -> float:
        return self.gap - self.tail_arrow - self.head_arrow


@dataclass(frozen=True)
class ScanResult:
    rooms: list[FlatArrowRoom]
    skipped: int


@dataclass(frozen=True)
class IdentityResult:
    path: Path
    ranker: str
    same: bool
    has_flat_both_edge: bool
    before_rooms: list[FlatArrowRoom]
    after_rooms: list[FlatArrowRoom]
    skipped: bool


def tracked_graphs() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "*.dot", "*.gv"], text=True)
    return [Path(line) for line in output.splitlines()]


def current_dot() -> str:
    dot = shutil.which("dot")
    if dot is None:
        raise SystemExit("dot not found on PATH")
    return dot


def baseline_dot() -> Path:
    env_dot = os.environ.get("FLAT_ARROW_BASELINE_DOT")
    path = Path(env_dot) if env_dot else Path("build-baseline/cmd/dot/dot_builtins")
    if not path.exists():
        raise SystemExit(
            "baseline dot not found; set FLAT_ARROW_BASELINE_DOT or build "
            "build-baseline/cmd/dot/dot_builtins from f8f30878f"
        )
    return path


def capped_dot_command(dot: str | Path, output_format: str, extra_args: tuple[str, ...], path: Path) -> list[str]:
    argv = [str(dot), "-Kdot", f"-T{output_format}", *extra_args, str(path)]
    return ["bash", "-lc", f"ulimit -v {ULIMIT_V_KB}; exec \"$@\"", "dot-ulimit", *argv]


def render_json(dot: str | Path, path: Path, extra_args: tuple[str, ...]) -> dict[str, Any] | None:
    with tempfile.TemporaryFile() as stdout:
        try:
            proc = subprocess.run(
                capped_dot_command(dot, "json", extra_args, path),
                stdout=stdout,
                stderr=subprocess.PIPE,
                check=False,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            print(
                f"{path} [{' '.join(extra_args) or 'default'}] skipped: "
                f"dot timed out after {RENDER_TIMEOUT_SECONDS}s"
            )
            return None
        if proc.returncode != 0:
            diagnostic = proc.stderr.decode("utf-8", errors="replace").splitlines()
            print(
                f"{path} [{' '.join(extra_args) or 'default'}] skipped: "
                f"dot exited {proc.returncode}: {diagnostic[0] if diagnostic else ''}"
            )
            return None
        stdout.seek(0)
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            return json.load(stdout)
        print(
            f"{path} [{' '.join(extra_args) or 'default'}] skipped: "
            "dot produced invalid JSON"
        )
        return None


def render_xdot_to(dot: str | Path, path: Path, extra_args: tuple[str, ...], out: Path) -> str:
    with out.open("wb") as stdout:
        try:
            proc = subprocess.run(
                capped_dot_command(dot, "xdot", extra_args, path),
                stdout=stdout,
                stderr=subprocess.PIPE,
                check=False,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            print(
                f"{path} [{' '.join(extra_args) or 'default'}] identity skipped: "
                f"dot timed out after {RENDER_TIMEOUT_SECONDS}s"
            )
            return "failed"
    if proc.returncode != 0:
        diagnostic = proc.stderr.decode("utf-8", errors="replace").splitlines()
        print(
            f"{path} [{' '.join(extra_args) or 'default'}] identity skipped: "
            f"dot exited {proc.returncode}: {diagnostic[0] if diagnostic else ''}"
        )
        return "failed"
    if out.stat().st_size == 0:
        print(
            f"{path} [{' '.join(extra_args) or 'default'}] identity skipped: "
            "dot produced empty xdot"
        )
        return "empty"
    return "ok"


def node_box(node: dict[str, Any]) -> tuple[float, float, float, float]:
    x, y = (float(value) for value in node["pos"].split(","))
    half_width = float(node["width"]) * 72 / 2
    half_height = float(node["height"]) * 72 / 2
    return x - half_width, y - half_height, x + half_width, y + half_height


def points_from_stream(stream: list[dict[str, Any]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for op in stream:
        if "points" in op:
            points.extend((float(x), float(y)) for x, y in op["points"])
        elif "rect" in op:
            x, y, w, h = (float(value) for value in op["rect"])
            points.extend(((x - w, y - h), (x + w, y + h)))
    return points


def drawn_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    return points_from_stream(edge.get("_draw_", []))


def extent(points: list[tuple[float, float]], axis: int) -> float:
    values = [point[axis] for point in points]
    return max(values) - min(values)


def flat_axis(
    tail_box: tuple[float, float, float, float],
    head_box: tuple[float, float, float, float],
    points: list[tuple[float, float]],
) -> int | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    horizontal_span = max(xs) - min(xs)
    vertical_span = max(ys) - min(ys)
    if vertical_span <= POINT_EPSILON and horizontal_span > POINT_EPSILON:
        return 0
    if horizontal_span <= POINT_EPSILON and vertical_span > POINT_EPSILON:
        return 1
    tail_center_y = (tail_box[1] + tail_box[3]) / 2
    head_center_y = (head_box[1] + head_box[3]) / 2
    tail_center_x = (tail_box[0] + tail_box[2]) / 2
    head_center_x = (head_box[0] + head_box[2]) / 2
    if abs(tail_center_y - head_center_y) <= POINT_EPSILON:
        return 0
    if abs(tail_center_x - head_center_x) <= POINT_EPSILON:
        return 1
    return None


def box_gap(
    tail_box: tuple[float, float, float, float],
    head_box: tuple[float, float, float, float],
    axis: int,
) -> float:
    low_side = min(tail_box[axis + 2], head_box[axis + 2])
    high_side = max(tail_box[axis], head_box[axis])
    return high_side - low_side


def flat_arrow_rooms(path: Path, ranker: str, layout: dict[str, Any]) -> list[FlatArrowRoom]:
    objects = {node["_gvid"]: node for node in layout.get("objects", []) if "pos" in node}
    rooms: list[FlatArrowRoom] = []
    for edge in layout.get("edges", []):
        if "_draw_" not in edge or "_hdraw_" not in edge or "_tdraw_" not in edge:
            continue
        tail = objects.get(edge["tail"])
        head = objects.get(edge["head"])
        if tail is None or head is None or tail["_gvid"] == head["_gvid"]:
            continue
        tail_box = node_box(tail)
        head_box = node_box(head)
        axis = flat_axis(tail_box, head_box, drawn_points(edge))
        if axis is None:
            continue
        tail_points = points_from_stream(edge["_tdraw_"])
        head_points = points_from_stream(edge["_hdraw_"])
        if not tail_points or not head_points:
            continue
        gap = box_gap(tail_box, head_box, axis)
        tail_arrow = extent(tail_points, axis)
        head_arrow = extent(head_points, axis)
        rooms.append(
            FlatArrowRoom(
                path=path,
                ranker=ranker,
                tail=tail["name"],
                head=head["name"],
                gap=gap,
                tail_arrow=tail_arrow,
                head_arrow=head_arrow,
            )
        )
    return rooms


def scan_one(args: tuple[str | Path, Path, str, tuple[str, ...]]) -> ScanResult:
    dot, path, ranker, extra_args = args
    layout = render_json(dot, path, extra_args)
    if layout is None:
        return ScanResult([], 1)
    return ScanResult(flat_arrow_rooms(path, ranker, layout), 0)


def scan_rooms(dot: str | Path, paths: list[Path]) -> tuple[list[FlatArrowRoom], int]:
    jobs = [(dot, path, ranker, args) for path in paths for ranker, args in RANKERS]
    rooms: list[FlatArrowRoom] = []
    skipped = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(scan_one, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            rooms.extend(result.rooms)
            skipped += result.skipped
    rooms.sort(key=lambda room: (str(room.path), room.ranker, room.tail, room.head))
    return rooms, skipped


def print_room(prefix: str, room: FlatArrowRoom) -> None:
    print(
        f"{prefix}{room.path} [{room.ranker}] {room.tail}->{room.head}: "
        f"gap={room.gap:.2f}pt "
        f"tail_arrow={room.tail_arrow:.2f}pt "
        f"head_arrow={room.head_arrow:.2f}pt "
        f"sum={room.tail_arrow + room.head_arrow:.2f}pt "
        f"clearance={room.clearance:.2f}pt"
    )


def rooms_by_key(rooms: list[FlatArrowRoom]) -> dict[tuple[str, str, str, str], FlatArrowRoom]:
    return {(str(room.path), room.ranker, room.tail, room.head): room for room in rooms}


def rooms_by_path_ranker(
    rooms: list[FlatArrowRoom],
) -> dict[tuple[Path, str], list[FlatArrowRoom]]:
    grouped: dict[tuple[Path, str], list[FlatArrowRoom]] = defaultdict(list)
    for room in rooms:
        grouped[(room.path, room.ranker)].append(room)
    return grouped


def identity_one(
    current: str,
    baseline: Path,
    path: Path,
    ranker: str,
    extra_args: tuple[str, ...],
    before_rooms: list[FlatArrowRoom],
    after_rooms: list[FlatArrowRoom],
) -> IdentityResult:
    has_flat_both_edge = bool(before_rooms or after_rooms)
    same: bool | None = None
    with tempfile.TemporaryDirectory() as tmp:
        skipped = False
        for attempt in range(2):
            old_out = Path(tmp) / f"old-{attempt}.xdot"
            new_out = Path(tmp) / f"new-{attempt}.xdot"
            old_status = render_xdot_to(baseline, path, extra_args, old_out)
            new_status = render_xdot_to(current, path, extra_args, new_out)
            if old_status != "ok" or new_status != "ok":
                skipped = True
                if old_status == "empty" or new_status == "empty":
                    continue
                return IdentityResult(
                    path, ranker, False, has_flat_both_edge, before_rooms, after_rooms, True
                )
            same = filecmp.cmp(old_out, new_out, shallow=False)
            if same:
                break
        if same is None and skipped:
            return IdentityResult(
                path, ranker, False, has_flat_both_edge, before_rooms, after_rooms, True
            )
    assert same is not None
    return IdentityResult(path, ranker, same, has_flat_both_edge, before_rooms, after_rooms, False)


def compare_identity(
    current: str,
    baseline: Path,
    paths: list[Path],
    before_rooms_by_path_ranker: dict[tuple[Path, str], list[FlatArrowRoom]],
    after_rooms_by_path_ranker: dict[tuple[Path, str], list[FlatArrowRoom]],
) -> list[IdentityResult]:
    jobs = [(path, ranker, args) for path in paths for ranker, args in RANKERS]
    results: list[IdentityResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(
                identity_one,
                current,
                baseline,
                path,
                ranker,
                args,
                before_rooms_by_path_ranker.get((path, ranker), []),
                after_rooms_by_path_ranker.get((path, ranker), []),
            )
            for path, ranker, args in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda result: (str(result.path), result.ranker))


def report_before_after(
    current: str,
    baseline: Path,
    tempdir: Path,
) -> None:
    stock = tempdir / "stock-flat-dir-both.dot"
    stock.write_text(STOCK_REPRO, encoding="utf-8")
    for label, path in (("named fixture", FIXTURE), ("stock repro", stock)):
        print(f"{label}: {path}")
        for ranker, args in RANKERS:
            before = scan_one((baseline, path, ranker, args)).rooms
            after = scan_one((current, path, ranker, args)).rooms
            for room in before:
                print_room("  before ", room)
            for room in after:
                print_room("  after  ", room)


def peak_rss_mb() -> float:
    usage = max(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    )
    if os.uname().sysname == "Darwin":
        return usage / 1024 / 1024
    return usage / 1024


def git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def main() -> None:
    current = current_dot()
    baseline = baseline_dot()
    paths = tracked_graphs()
    failures = 0

    print(f"commit sha: {git_sha()}")
    print(f"baseline commit: {BASE_COMMIT}")
    print(
        f"chosen margin: {FLAT_BOTH_ARROW_MARGIN:.2f}pt; "
        "large enough to leave a visible shaft after rounding, small enough to "
        "avoid moving unrelated neighbours by more than the missing room"
    )
    print(f"current dot: {current}")
    print(f"baseline dot: {baseline}")
    print(f"max concurrent renders: {MAX_WORKERS}")
    print(f"per-render address-space cap: {ULIMIT_V_KB // 1024} MB")
    print(f"render timeout: {RENDER_TIMEOUT_SECONDS}s")

    with tempfile.TemporaryDirectory() as tmp:
        report_before_after(current, baseline, Path(tmp))

    before_rooms, before_skipped = scan_rooms(baseline, paths)
    rooms, skipped = scan_rooms(current, paths)
    for room in rooms:
        print_room("", room)
        if room.clearance < -POINT_EPSILON or math.isnan(room.clearance):
            failures += 1

    identity = compare_identity(
        current,
        baseline,
        paths,
        rooms_by_path_ranker(before_rooms),
        rooms_by_path_ranker(rooms),
    )
    comparable = [result for result in identity if not result.skipped]
    no_flat = [result for result in comparable if not result.has_flat_both_edge]
    matched = sum(1 for result in no_flat if result.same)
    print(f"byte-identical graph/rankers with no both-ended flat edge: {matched}/{len(no_flat)}")
    for result in no_flat:
        if not result.same:
            print(f"unexpected changed graph without both-ended flat edge: {result.path} [{result.ranker}]")
            failures += 1

    changed_with_flat = [result for result in comparable if not result.same and result.has_flat_both_edge]
    by_path: dict[Path, list[IdentityResult]] = defaultdict(list)
    for result in changed_with_flat:
        by_path[result.path].append(result)
    print("changed graphs with both-ended flat edges:")
    if not by_path:
        print("  none")
    for path, results in sorted(by_path.items(), key=lambda item: str(item[0])):
        print(f"  {path}")
        for result in results:
            before_by_key = rooms_by_key(result.before_rooms)
            after_by_key = rooms_by_key(result.after_rooms)
            keys = sorted(set(before_by_key) | set(after_by_key))
            for key in keys:
                if key in before_by_key:
                    print_room("    before ", before_by_key[key])
                if key in after_by_key:
                    print_room("    after  ", after_by_key[key])

    print(f"checked flat both-ended edges: {len(rooms)}")
    print(f"skipped baseline graph/rankers: {before_skipped}")
    print(f"skipped current graph/rankers: {skipped}")
    print(f"skipped identity graph/rankers: {sum(1 for result in identity if result.skipped)}")
    print(f"peak verifier rss: {peak_rss_mb():.1f} MB")
    if failures:
        raise SystemExit(f"flat arrow room verification failures: {failures}")


if __name__ == "__main__":
    main()
