#!/usr/bin/env python3
"""Sweep arrowhead source order and spread regressions against a fixed commit."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any


BASE_SHA = "2e35a1494e729bcd82dfa16cfb26babe9080b047"
ANGLE = 35.0
SAMPLES = 24
EPSILON = 1e-6
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
NONDETERMINISTIC_IDENTITY_NAMES = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}
PUBLIC_FIXTURE_EXCLUDES = {Path("tests/graphs/b15.gv")}
GRADED = (
    Path("tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"),
    Path("tests/graphs/concentrate-demo/distinct-parallel-colors-stay-distinct.dot"),
)
CRASH_FIXTURES = (
    Path("tests/graphs/concentrate-demo/crash-record-port-concentrate-2764.dot"),
    Path("tests/2764.dot"),
    Path("tests/2765.dot"),
)
MODES = {
    "default": (),
    "false": ("-Gconcentrate=false",),
    "true": ("-Gconcentrate=true",),
}


@dataclass(frozen=True)
class Render:
    rel: str
    mode: str
    rc: int | str
    json: bytes = b""
    xdot: bytes = b""
    error: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))


def run(args: list[str | Path], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        preexec_fn=cap_memory,
    )


def checked(args: list[str | Path], cwd: Path, timeout: int) -> None:
    proc = run(args, cwd, timeout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(proc.returncode)


def current_dot(root: Path, timeout: int) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root, timeout)
    return dot


def base_dot(root: Path, sha: str, timeout: int) -> Path:
    override = os.environ.get("VERIFY_ARM_ORDER_BASE_DOT")
    if override:
        return Path(override).resolve()
    worktree = root.parent / f"{root.name}-arm-order-{sha[:9]}"
    if not worktree.exists():
        checked(["git", "worktree", "add", "--detach", worktree, sha], root, timeout)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=worktree, text=True).strip()
    if head != sha:
        raise RuntimeError(f"{worktree} is at {head}, expected {sha}")
    dot = worktree / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked(
            [
                "cmake",
                "-S",
                ".",
                "-B",
                "build",
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=ON",
                "-DBUILD_TESTING=ON",
            ],
            worktree,
            timeout,
        )
    checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree, timeout)
    return dot


def graph_inputs(root: Path) -> list[Path]:
    paths: set[Path] = set((root / "graphs" / "directed").glob("*.gv"))
    paths.update((root / "graphs" / "undirected").glob("*.gv"))
    paths.update((root / "tests" / "graphs").glob("**/*.gv"))
    paths.update((root / "tests" / "graphs").glob("**/*.dot"))
    paths.update(root / rel for rel in GRADED)
    return sorted(path for path in paths if path.relative_to(root) not in PUBLIC_FIXTURE_EXCLUDES)


def render_json(dot: Path, root: Path, rel: str, mode: str, timeout: int) -> Render:
    try:
        proc = run([dot, *MODES[mode], "-Tjson", root / rel], root, timeout)
    except subprocess.TimeoutExpired as err:
        stderr = (err.stderr or b"").decode("utf-8", "replace")[:400]
        return Render(rel, mode, "TIMEOUT", error=stderr)
    error = proc.stderr.decode("utf-8", "replace")[:400]
    return Render(rel, mode, proc.returncode, json=proc.stdout, error=error)


def render_xdot(dot: Path, root: Path, rel: str, mode: str, timeout: int) -> Render:
    try:
        proc = run([dot, *MODES[mode], "-Txdot", root / rel], root, timeout)
    except subprocess.TimeoutExpired as err:
        stderr = (err.stderr or b"").decode("utf-8", "replace")[:400]
        return Render(rel, mode, "TIMEOUT", error=stderr)
    error = proc.stderr.decode("utf-8", "replace")[:400]
    return Render(rel, mode, proc.returncode, xdot=proc.stdout, error=error)


def layouts(data: bytes) -> list[dict[str, Any]]:
    text = data.decode("utf-8", "replace")
    decoder = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        value, offset = decoder.raw_decode(text, offset)
        out.append(value)
    return out


def point(value: list[float]) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def cubic(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1.0 - t
    return (
        u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
    )


def turn(a, b, c) -> float:
    incoming = b[0] - a[0], b[1] - a[1]
    outgoing = c[0] - b[0], c[1] - b[1]
    li = math.hypot(*incoming)
    lo = math.hypot(*outgoing)
    if li < 1e-9 or lo < 1e-9:
        return 0.0
    cosine = max(-1.0, min(1.0, (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / (li * lo)))
    return math.degrees(math.acos(cosine))


def sampled_curve(edge: dict[str, Any]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for op in edge.get("_draw_", []):
        if op.get("op") not in ("b", "B"):
            continue
        pts = [point(p) for p in op.get("points", [])]
        for i in range(0, len(pts) - 3, 3):
            segment = [cubic(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], j / SAMPLES) for j in range(SAMPLES + 1)]
            out.extend(segment[1:] if out else segment)
    return out


def turn_score(rendered: bytes) -> tuple[int, float]:
    count = 0
    worst = 0.0
    for layout in layouts(rendered):
        for edge in layout.get("edges", []):
            curve = sampled_curve(edge)
            for a, b, c in zip(curve, curve[1:], curve[2:]):
                angle = turn(a, b, c)
                if angle > ANGLE:
                    count += 1
                    worst = max(worst, angle)
    return count, worst


def signed_area(poly: list[tuple[float, float]]) -> float:
    return sum(
        poly[i][0] * poly[(i + 1) % len(poly)][1]
        - poly[(i + 1) % len(poly)][0] * poly[i][1]
        for i in range(len(poly))
    ) / 2.0


def bbox(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap_area(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def inside_halfplane(p, start, end, clockwise: bool) -> bool:
    cross = (end[0] - start[0]) * (p[1] - start[1]) - (end[1] - start[1]) * (p[0] - start[0])
    return cross <= 1e-9 if clockwise else cross >= -1e-9


def segment_intersection(a, b, c, d) -> tuple[float, float]:
    den = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
    if abs(den) < 1e-9:
        return b
    left = a[0] * b[1] - a[1] * b[0]
    right = c[0] * d[1] - c[1] * d[0]
    return (
        (left * (c[0] - d[0]) - (a[0] - b[0]) * right) / den,
        (left * (c[1] - d[1]) - (a[1] - b[1]) * right) / den,
    )


def convex_intersection_area(subject, clip) -> float:
    out = list(subject)
    clockwise = signed_area(clip) < 0.0
    for i, start in enumerate(clip):
        end = clip[(i + 1) % len(clip)]
        incoming = out
        out = []
        if not incoming:
            return 0.0
        prev = incoming[-1]
        for current in incoming:
            current_inside = inside_halfplane(current, start, end, clockwise)
            prev_inside = inside_halfplane(prev, start, end, clockwise)
            if current_inside:
                if not prev_inside:
                    out.append(segment_intersection(prev, current, start, end))
                out.append(current)
            elif prev_inside:
                out.append(segment_intersection(prev, current, start, end))
            prev = current
    return abs(signed_area(out)) if len(out) >= 3 else 0.0


def arrow_polygons(rendered: bytes) -> list[tuple[str, list[tuple[float, float]]]]:
    polygons = []
    for layout in layouts(rendered):
        for edge in layout.get("edges", []):
            for stream, endpoint_name in (("_hdraw_", "head"), ("_tdraw_", "tail")):
                endpoint = str(edge.get(endpoint_name, ""))
                for op in edge.get(stream, []):
                    if op.get("op") in ("P", "p"):
                        poly = [point(p) for p in op.get("points", [])]
                        if len(poly) >= 3:
                            polygons.append((endpoint, poly))
    return polygons


def arrowhead_overlap(rendered: bytes) -> tuple[int, float]:
    heads = [(endpoint, poly, bbox(poly)) for endpoint, poly in arrow_polygons(rendered)]
    pairs = 0
    area = 0.0
    for i, (left_endpoint, left, left_box) in enumerate(heads):
        for right_endpoint, right, right_box in heads[i + 1 :]:
            if left_endpoint != right_endpoint or bbox_overlap_area(left_box, right_box) <= 0.0:
                continue
            overlap = convex_intersection_area(left, right)
            if overlap > EPSILON:
                pairs += 1
                area += overlap
    return pairs, area


def real_node_y_count(rendered: bytes) -> int:
    ys = set()
    for layout_index, layout in enumerate(layouts(rendered)):
        for obj in layout.get("objects", []):
            if "pos" not in obj:
                continue
            try:
                width = float(obj.get("width", 0.0))
                if width <= 0.05:
                    continue
                ys.add((layout_index, round(float(obj["pos"].split(",", 1)[1]), 3)))
            except (TypeError, ValueError):
                continue
    return len(ys)


def arm_groups(rendered: bytes) -> dict[str, list[tuple[tuple[float, float], tuple[float, float]]]]:
    groups: dict[str, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for layout in layouts(rendered):
        names = {obj["_gvid"]: obj.get("name") for obj in layout.get("objects", []) if "_gvid" in obj}
        for edge in layout.get("edges", []):
            if any(edge.get(attr) for attr in ("arrowhead", "arrowtail", "dir")):
                continue
            pts = None
            for op in edge.get("_draw_", []):
                if op.get("op") in ("b", "B"):
                    pts = [point(p) for p in op.get("points", [])]
                    break
            if not pts or len(pts) < 4:
                continue
            for stream, node_id, source in (("_hdraw_", edge.get("head"), pts[0]), ("_tdraw_", edge.get("tail"), pts[-1])):
                tip = None
                for op in edge.get(stream, []):
                    if op.get("op") in ("P", "p"):
                        poly = [point(p) for p in op.get("points", [])]
                        if len(poly) == 3:
                            cx = sum(p[0] for p in poly) / 3
                            cy = sum(p[1] for p in poly) / 3
                            tip = max(poly, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
                        break
                if tip is not None:
                    groups.setdefault(str(names.get(node_id)), []).append((source, tip))
    return groups


def swaps(group: list[tuple[tuple[float, float], tuple[float, float]]]) -> int:
    if len(group) < 2:
        return 0
    tips = [tip for _source, tip in group]
    ax = max(p[0] for p in tips) - min(p[0] for p in tips)
    ay = max(p[1] for p in tips) - min(p[1] for p in tips)
    project = (lambda p: p[0]) if ax >= ay else (lambda p: p[1])
    count = 0
    for (s1, t1), (s2, t2) in combinations(group, 2):
        dt = project(t1) - project(t2)
        ds = project(s1) - project(s2)
        if abs(dt) < 1e-6 or abs(ds) < 1e-6:
            continue
        if (dt > 0) != (ds > 0):
            count += 1
    return count


def arm_order(rendered: bytes) -> tuple[int, dict[str, int], dict[str, int]]:
    groups = arm_groups(rendered)
    per_node = {node: swaps(group) for node, group in groups.items()}
    sizes = {node: len(group) for node, group in groups.items()}
    return sum(per_node.values()), per_node, sizes


def sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, help="candidate dot binary")
    parser.add_argument("--base", type=Path, help="2e35a1494 dot binary")
    parser.add_argument("--base-sha", default=BASE_SHA)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("VERIFY_ARM_ORDER_JOBS", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("VERIFY_ARM_ORDER_TIMEOUT", "300")))
    args = parser.parse_args()

    root = repo_root()
    dot = args.dot.resolve() if args.dot else current_dot(root, args.timeout)
    base = args.base.resolve() if args.base else base_dot(root, args.base_sha, args.timeout)
    inputs = graph_inputs(root)
    rels = [path.relative_to(root).as_posix() for path in inputs]

    print(f"base_sha={args.base_sha}")
    print(f"public_fixture_inputs={len(rels)}")

    def job(rel: str):
        rows = {}
        for mode in MODES:
            rows[mode] = (
                render_json(base, root, rel, mode, args.timeout),
                render_json(dot, root, rel, mode, args.timeout),
            )
        true_xdot = (
            render_xdot(base, root, rel, "true", args.timeout),
            render_xdot(dot, root, rel, "true", args.timeout),
        )
        return rel, rows, true_xdot

    results = {}
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(job, rel): rel for rel in rels}
        for done, future in enumerate(futures.as_completed(pending), 1):
            rel, rows, true_xdot = future.result()
            results[rel] = (rows, true_xdot)
            if done % 25 == 0 or done == len(rels):
                print(f"progress_checked={done}/{len(rels)}", flush=True)

    graded_failures = []
    swap_increases = []
    overlap_increases = []
    turn_increases = []
    true_xdot_changes = []
    rank_y_drops = []
    unexpected_render_failures = []

    for rel in rels:
        rows, true_xdot = results[rel]
        for mode, (before, after) in rows.items():
            if before.rc == 0 and after.rc != 0:
                unexpected_render_failures.append((rel, mode, after.rc))
            if before.rc != 0 or after.rc != 0:
                continue
            before_swaps, before_nodes, _before_sizes = arm_order(before.json)
            after_swaps, after_nodes, after_sizes = arm_order(after.json)
            if after_swaps > before_swaps:
                swap_increases.append((rel, mode, before_swaps, after_swaps))
            if Path(rel) in GRADED and mode in ("default", "false"):
                print(f"graded\t{rel}\t{mode}\tswapped_pairs={after_swaps}")
                for node in sorted(after_sizes):
                    print(f"graded_node\t{rel}\t{mode}\t{node}\t{after_nodes.get(node, 0)}\t{after_sizes[node]}")
                if after_swaps != 0:
                    graded_failures.append((rel, mode, after_swaps))
            before_overlap = arrowhead_overlap(before.json)
            after_overlap = arrowhead_overlap(after.json)
            if after_overlap[1] > before_overlap[1] + EPSILON:
                overlap_increases.append((rel, mode, before_overlap[1], after_overlap[1]))
            before_turn = turn_score(before.json)
            after_turn = turn_score(after.json)
            if after_turn[0] > before_turn[0]:
                turn_increases.append((rel, mode, before_turn[0], after_turn[0], before_turn[1], after_turn[1]))
            before_y = real_node_y_count(before.json)
            after_y = real_node_y_count(after.json)
            if after_y < before_y:
                rank_y_drops.append((rel, mode, before_y, after_y))
        before_xdot, after_xdot = true_xdot
        if before_xdot.rc == 0 and after_xdot.rc == 0 and sha(before_xdot.xdot) != sha(after_xdot.xdot):
            if Path(rel).name not in NONDETERMINISTIC_IDENTITY_NAMES:
                true_xdot_changes.append(rel)

    target_rel = GRADED[0].as_posix()
    target_rows, _target_xdot = results[target_rel]
    target_false = target_rows["false"][1]
    target_overlap = arrowhead_overlap(target_false.json) if target_false.rc == 0 else (-1, math.nan)

    crash_failures = []
    for rel_path in CRASH_FIXTURES:
        rel = rel_path.as_posix()
        proc = render_json(dot, root, rel, "true", args.timeout)
        print(f"crash_fixture_rc\t{rel}\t{proc.rc}")
        if proc.rc != 0:
            crash_failures.append((rel, proc.rc))

    print(f"target_false_overlap_pairs={target_overlap[0]}")
    print(f"target_false_overlap_pt2={target_overlap[1]:.3f}")
    print(f"graded_swap_failures={len(graded_failures)}")
    print(f"swap_increase_fixtures={len({(rel, mode) for rel, mode, *_ in swap_increases})}")
    print(f"overlap_increase_fixtures={len({(rel, mode) for rel, mode, *_ in overlap_increases})}")
    print(f"turn_increase_fixtures={len({(rel, mode) for rel, mode, *_ in turn_increases})}")
    print(f"concentrate_true_xdot_changes={len(true_xdot_changes)}")
    print(f"distinct_y_drop_cases={len({(rel, mode) for rel, mode, *_ in rank_y_drops})}")
    print(f"unexpected_render_failures={len(unexpected_render_failures)}")
    print(f"crash_fixture_failures={len(crash_failures)}")

    for rel, mode, before, after in swap_increases[:50]:
        print(f"swap_increase\t{rel}\t{mode}\t{before}->{after}")
    for rel, mode, before, after in overlap_increases[:50]:
        print(f"overlap_increase\t{rel}\t{mode}\t{before:.6f}->{after:.6f}")
    for rel, mode, before, after, before_worst, after_worst in turn_increases[:50]:
        print(f"turn_increase\t{rel}\t{mode}\t{before}->{after}\tworst={before_worst:.3f}->{after_worst:.3f}")
    for rel in true_xdot_changes[:50]:
        print(f"concentrate_true_xdot_change\t{rel}")
    for rel, mode, before, after in rank_y_drops[:50]:
        print(f"distinct_y_drop\t{rel}\t{mode}\t{before}->{after}")
    for rel, mode, rc in unexpected_render_failures[:50]:
        print(f"unexpected_render_failure\t{rel}\t{mode}\trc={rc}")

    ok = (
        not graded_failures
        and not swap_increases
        and not overlap_increases
        and not turn_increases
        and not true_xdot_changes
        and not rank_y_drops
        and not unexpected_render_failures
        and not crash_failures
        and target_overlap[1] <= EPSILON
    )
    print("OK verify_arm_order_sweep" if ok else "FAIL verify_arm_order_sweep")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
