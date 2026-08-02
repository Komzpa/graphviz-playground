#!/usr/bin/env python3
"""Verify concentrate trunk compaction does not add drawn spline corners."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys


# The commit this check is a regression test FOR. It was first written against
# f1a5100c0, which was reverted the same day for putting sharp corners on 36
# fixtures with concentrate on; anchored there, this gate reported 55 "changed"
# fixtures that were the revert's differences, not this change's, and failed for
# a reason that had nothing to do with trunk corners.
# A gate's base must be the commit its subject applies to, never a commit that
# happens to be in the reflog.
BASE_SHA = "130d2bc53d38fc68f00bad355a1395a8ff792738"
TARGET = "tests/graphs/concentrate-demo/trunk-corner-anonymous-state.dot"
ANGLE = 35.0
SAMPLES = 24
TIMEOUT = int(os.environ.get("TRUNK_CORNERS_TIMEOUT", "240"))
JOBS = int(os.environ.get("TRUNK_CORNERS_JOBS", "6"))
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
PUBLIC_FIXTURE_EXCLUDES = {
    "tests/graphs/b15.gv",
    "tests/graphs/concentrate-demo/malformed-nodesep-rejected-2758.dot",
}
NONDETERMINISTIC_IDENTITY_NAMES = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}
CRASH_FIXTURES = (
    "tests/graphs/concentrate-demo/crash-record-port-concentrate-2764.dot",
    "tests/2764.dot",
    "tests/2765.dot",
)


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))


def run(args: list[str | Path], cwd: Path) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        [str(a) for a in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=cap_memory,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired([str(a) for a in args], TIMEOUT, stdout, stderr)
    return subprocess.CompletedProcess([str(a) for a in args], proc.returncode, stdout, stderr)


def checked(args: list[str | Path], cwd: Path) -> None:
    proc = run(args, cwd)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(proc.returncode)


def current_dot(repo: Path) -> Path:
    dot = repo / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], repo)
    return dot


def base_dot(repo: Path) -> Path:
    override = os.environ.get("TRUNK_CORNERS_BASE_DOT")
    if override:
        return Path(override).resolve()
    worktree = repo.parent / f"{repo.name}-trunk-corners-{BASE_SHA[:9]}"
    if not worktree.exists():
        checked(["git", "worktree", "add", "--detach", worktree, BASE_SHA], repo)
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
        )
    checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree)
    return dot


def corpus(repo: Path) -> list[str]:
    paths = set((repo / "graphs" / "directed").glob("*.gv"))
    paths.update((repo / "graphs" / "undirected").glob("*.gv"))
    paths.update((repo / "tests" / "graphs").glob("*.gv"))
    paths.update((repo / "tests" / "graphs").glob("*.dot"))
    paths.update((repo / "tests" / "graphs" / "concentrate-demo").glob("*.dot"))
    return sorted(
        str(p.relative_to(repo))
        for p in paths
        if str(p.relative_to(repo)) not in PUBLIC_FIXTURE_EXCLUDES
    )


def layouts(data: bytes) -> list[dict]:
    text = data.decode("utf-8")
    decoder = json.JSONDecoder()
    out = []
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        layout, offset = decoder.raw_decode(text, offset)
        out.append(layout)
    return out


def point(v: list[float]) -> tuple[float, float]:
    return float(v[0]), float(v[1])


def cubic(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def turn(a, b, c) -> float:
    vin = b[0] - a[0], b[1] - a[1]
    vout = c[0] - b[0], c[1] - b[1]
    ni = math.hypot(*vin)
    no = math.hypot(*vout)
    if ni < 1e-6 or no < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, (vin[0] * vout[0] + vin[1] * vout[1]) / (ni * no)))
    return math.degrees(math.acos(cos))


def score_turns(data: bytes) -> tuple[int, float]:
    count = 0
    worst = 0.0
    for layout in layouts(data):
        for edge in layout.get("edges", []):
            for op in edge.get("_draw_", []):
                if op.get("op") != "b":
                    continue
                pts = [point(p) for p in op.get("points", [])]
                poly: list[tuple[float, float]] = []
                for i in range(0, len(pts) - 3, 3):
                    seg = [cubic(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], j / SAMPLES) for j in range(SAMPLES + 1)]
                    if poly:
                        seg = seg[1:]
                    poly.extend(seg)
                for i in range(1, len(poly) - 1):
                    angle = turn(poly[i - 1], poly[i], poly[i + 1])
                    worst = max(worst, angle)
                    count += angle > ANGLE
    return count, worst


def real_node_y_count(data: bytes) -> int:
    ys = set()
    for layout_index, layout in enumerate(layouts(data)):
        for obj in layout.get("objects", []):
            if "pos" not in obj:
                continue
            if float(obj.get("width", 0.0)) <= 0.05:
                continue
            ys.add((layout_index, round(float(obj["pos"].split(",", 1)[1]), 3)))
    return len(ys)


def signed_area(poly: list[tuple[float, float]]) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2.0


def inside_halfplane(p, start, end, clockwise: bool) -> bool:
    cross = (end[0] - start[0]) * (p[1] - start[1]) - (end[1] - start[1]) * (p[0] - start[0])
    return cross <= 1e-9 if clockwise else cross >= -1e-9


def segment_intersection(a, b, c, d):
    den = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
    if abs(den) < 1e-9:
        return b
    left = a[0] * b[1] - a[1] * b[0]
    right = c[0] * d[1] - c[1] * d[0]
    return ((left * (c[0] - d[0]) - (a[0] - b[0]) * right) / den, (left * (c[1] - d[1]) - (a[1] - b[1]) * right) / den)


def convex_intersection_area(subject, clip) -> float:
    out = subject
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


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def arrowhead_overlap(data: bytes) -> tuple[int, float]:
    polygons = []
    for layout_index, layout in enumerate(layouts(data)):
        for edge_index, edge in enumerate(layout.get("edges", [])):
            for stream, endpoint_name in (("_hdraw_", "head"), ("_tdraw_", "tail")):
                endpoint = str(edge.get(endpoint_name, ""))
                for op_index, op in enumerate(edge.get(stream, [])):
                    if op.get("op") == "P":
                        poly = [point(p) for p in op.get("points", [])]
                        if len(poly) >= 3:
                            polygons.append((f"{layout_index}:{edge_index}:{stream}:{op_index}", endpoint, poly, bbox(poly)))
    pairs = 0
    area = 0.0
    for i, (_lid, left_endpoint, left, left_box) in enumerate(polygons):
        for _rid, right_endpoint, right, right_box in polygons[i + 1 :]:
            if left_endpoint != right_endpoint or bbox_overlap(left_box, right_box) <= 0.0:
                continue
            overlap = convex_intersection_area(left, right)
            if overlap > 1e-6:
                pairs += 1
                area += overlap
    return pairs, area


def render(dot: Path, repo: Path, rel: str, fmt: str):
    try:
        return run([dot, "-Gconcentrate=true", f"-T{fmt}", repo / rel], repo)
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def metrics(dot: Path, repo: Path, rel: str):
    js = render(dot, repo, rel, "json")
    if js == "TIMEOUT":
        return js
    if js.returncode != 0:
        return None, None, None, js.returncode
    return score_turns(js.stdout), arrowhead_overlap(js.stdout), real_node_y_count(js.stdout), js.returncode


def one(repo: Path, old: Path, new: Path, rel: str):
    old_x = render(old, repo, rel, "xdot")
    new_x = render(new, repo, rel, "xdot")
    changed = False
    if old_x != "TIMEOUT" and new_x != "TIMEOUT" and old_x.returncode == 0 and new_x.returncode == 0 and Path(rel).name not in NONDETERMINISTIC_IDENTITY_NAMES:
        changed = hashlib.sha256(old_x.stdout).digest() != hashlib.sha256(new_x.stdout).digest()
    if not changed and rel != TARGET:
        return rel, old_x, new_x, "IDENTICAL", "IDENTICAL", changed
    old_m = metrics(old, repo, rel)
    new_m = metrics(new, repo, rel)
    return rel, old_x, new_x, old_m, new_m, changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path)
    parser.add_argument("--base", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = root()
    new = args.dot.resolve() if args.dot else current_dot(repo)
    old = args.base.resolve() if args.base else base_dot(repo)
    inputs = corpus(repo)
    if TARGET not in inputs:
        print(f"missing_target={TARGET}")
        return 1

    target_old = metrics(old, repo, TARGET)
    target_new = metrics(new, repo, TARGET)
    print(f"base_sha={BASE_SHA}")
    print(f"base_dot={old}")
    print(f"current_dot={new}")
    print(f"public_fixture_inputs={len(inputs)}")
    print(
        "target_fixture="
        f"{TARGET} old_turns={target_old[0][0]} old_worst={target_old[0][1]:.3f} "
        f"new_turns={target_new[0][0]} new_worst={target_new[0][1]:.3f}"
    )

    rows = []
    with futures.ThreadPoolExecutor(max_workers=JOBS) as executor:
        for row in executor.map(lambda rel: one(repo, old, new, rel), inputs):
            rows.append(row)

    turn_increases = []
    overlap_increases = []
    y_drops = []
    changed = []
    render_failures = []
    for rel, old_x, new_x, old_m, new_m, did_change in rows:
        if old_x == "TIMEOUT" or new_x == "TIMEOUT" or old_m == "TIMEOUT" or new_m == "TIMEOUT":
            render_failures.append((rel, "TIMEOUT"))
            continue
        if old_m == "IDENTICAL" and new_m == "IDENTICAL":
            continue
        if old_x.returncode != 0 or new_x.returncode != 0 or old_m[3] != 0 or new_m[3] != 0:
            render_failures.append((rel, old_x.returncode, new_x.returncode, old_m[3], new_m[3]))
            continue
        if did_change:
            changed.append(rel)
        if new_m[0][0] > old_m[0][0]:
            turn_increases.append((rel, old_m[0], new_m[0]))
        if new_m[1][1] > old_m[1][1] + 1e-6:
            overlap_increases.append((rel, old_m[1], new_m[1]))
        if new_m[2] < old_m[2]:
            y_drops.append((rel, old_m[2], new_m[2]))

    crash_rc = []
    for rel in CRASH_FIXTURES:
        proc = render(new, repo, rel, "json")
        crash_rc.append((rel, "TIMEOUT" if proc == "TIMEOUT" else proc.returncode))

    print(f"turn_increase_fixtures={len(turn_increases)}")
    print(f"overlap_increase_fixtures={len(overlap_increases)}")
    print(f"concentrate_true_changed_fixtures={changed}")
    print(f"distinct_y_drop_cases={len(y_drops)}")
    print(f"crash_fixture_rc={crash_rc}")
    if render_failures:
        print(f"render_failures={render_failures[:20]}")
    # Assert the property, not the delta. As first written this checked that the
    # target went from a sharp corner to none BETWEEN base and current, which can
    # only ever be true in the single commit that fixed it: once landed, the base
    # already carries the fix and the same check reports FAIL on a healthy tree.
    # A regression test asks "is it still right", so: the target must be smooth
    # now, and nothing may get worse than the base.
    ok = (
        target_new[0][1] < ANGLE
        and not turn_increases
        and not overlap_increases
        and not y_drops
        and all(rc == 0 for _rel, rc in crash_rc)
        and not render_failures
    )
    print("OK verify_concentrate_trunk_corners" if ok else "FAIL verify_concentrate_trunk_corners")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
