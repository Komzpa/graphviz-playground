#!/usr/bin/env python3
"""Verify pmpipe is the only concentrate=true fixture changed from f5c8b42e7."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys


BASE_SHA = "f5c8b42e7eee0dd79c535831277557328d6216e9"
ANGLE = 35.0
SAMPLES = 24
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
NONDETERMINISTIC_IDENTITY_NAMES = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}
PUBLIC_FIXTURE_EXCLUDES = {"tests/graphs/b15.gv"}
CRASH_FIXTURES = (
    "tests/graphs/concentrate-demo/crash-record-port-concentrate-2764.dot",
    "tests/2764.dot",
    "tests/2765.dot",
)
MODES = {
    "default": (),
    "concentrate_false": ("-Gconcentrate=false",),
    "concentrate_true": ("-Gconcentrate=true",),
}
EPSILON = 1e-6


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))


def run(args: list[str | Path], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args],
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


def current_dot(repo: Path, timeout: int) -> Path:
    dot = repo / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], repo, timeout)
    return dot


def base_dot(repo: Path, timeout: int) -> Path:
    override = os.environ.get("VERIFY_PMPIPE_BASE_DOT")
    if override:
        return Path(override).resolve()
    worktree = repo.parent / f"{repo.name}-head-{BASE_SHA[:9]}"
    if not worktree.exists():
        checked(["git", "worktree", "add", "--detach", worktree, BASE_SHA], repo, timeout)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=worktree, text=True).strip()
    if head != BASE_SHA:
        raise RuntimeError(f"{worktree} is at {head}, expected {BASE_SHA}")
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


def corpus(repo: Path) -> list[str]:
    paths = set((repo / "graphs" / "directed").glob("*.gv"))
    paths.update((repo / "graphs" / "undirected").glob("*.gv"))
    paths.update((repo / "tests" / "graphs").glob("*.gv"))
    paths.update((repo / "tests" / "graphs").glob("*.dot"))
    return sorted(
        str(path.relative_to(repo))
        for path in paths
        if str(path.relative_to(repo)) not in PUBLIC_FIXTURE_EXCLUDES
    )


def render_json(dot: Path, repo: Path, rel: str, flags: tuple[str, ...], timeout: int):
    return run([dot, *flags, "-Tjson", repo / rel], repo, timeout)


def render_xdot(dot: Path, repo: Path, rel: str, flags: tuple[str, ...], timeout: int):
    return run([dot, *flags, "-Txdot", repo / rel], repo, timeout)


def point(v: list[float]) -> tuple[float, float]:
    return float(v[0]), float(v[1])


def lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def cubic(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    a = lerp(p0, p1, t)
    b = lerp(p1, p2, t)
    c = lerp(p2, p3, t)
    d = lerp(a, b, t)
    e = lerp(b, c, t)
    return lerp(d, e, t)


def turn(a, b, c) -> float:
    incoming = b[0] - a[0], b[1] - a[1]
    outgoing = c[0] - b[0], c[1] - b[1]
    ni = math.hypot(*incoming)
    no = math.hypot(*outgoing)
    if ni < 1e-6 or no < 1e-6:
        return 0.0
    cos = max(
        -1.0,
        min(1.0, (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / (ni * no)),
    )
    return math.degrees(math.acos(cos))


def layouts(data: bytes) -> list[dict]:
    text = data.decode("utf-8", "replace")
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


def turn_score(data: bytes) -> tuple[int, float]:
    count = 0
    worst = 0.0
    for layout in layouts(data):
        for edge in layout.get("edges", []):
            for op in edge.get("_draw_", []):
                if op.get("op") != "b":
                    continue
                pts = [point(p) for p in op.get("points", [])]
                if len(pts) < 4:
                    continue
                poly: list[tuple[float, float]] = []
                for i in range(0, len(pts) - 3, 3):
                    seg = [
                        cubic(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], j / SAMPLES)
                        for j in range(SAMPLES + 1)
                    ]
                    poly.extend(seg[1:] if poly else seg)
                for i in range(1, len(poly) - 1):
                    angle = turn(poly[i - 1], poly[i], poly[i + 1])
                    if angle > ANGLE:
                        count += 1
                        worst = max(worst, angle)
    return count, worst


def real_node_y_count(data: bytes) -> int:
    ys = set()
    for layout_index, layout in enumerate(layouts(data)):
        for obj in layout.get("objects", []):
            if "pos" not in obj:
                continue
            width = float(obj.get("width", 0.0))
            if width <= 0.05:
                continue
            ys.add((layout_index, round(float(obj["pos"].split(",", 1)[1]), 3)))
    return len(ys)


def arrow_polygons(data: bytes) -> list[tuple[str, list[tuple[float, float]]]]:
    polygons = []
    for layout in layouts(data):
        for edge in layout.get("edges", []):
            for stream, endpoint_name in (("_hdraw_", "head"), ("_tdraw_", "tail")):
                endpoint = str(edge.get(endpoint_name, ""))
                for op in edge.get(stream, []):
                    if op.get("op") not in ("P", "p"):
                        continue
                    poly = [point(p) for p in op.get("points", [])]
                    if len(poly) >= 3:
                        polygons.append((endpoint, poly))
    return polygons


def bbox(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap_area(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )


def signed_area(poly: list[tuple[float, float]]) -> float:
    return sum(
        poly[i][0] * poly[(i + 1) % len(poly)][1]
        - poly[(i + 1) % len(poly)][0] * poly[i][1]
        for i in range(len(poly))
    ) / 2.0


def inside_halfplane(p, start, end, clockwise: bool) -> bool:
    cross = (end[0] - start[0]) * (p[1] - start[1]) - (end[1] - start[1]) * (
        p[0] - start[0]
    )
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


def arrowhead_overlap(data: bytes) -> tuple[int, int, float]:
    heads = [(endpoint, poly, bbox(poly)) for endpoint, poly in arrow_polygons(data)]
    pairs = 0
    area = 0.0
    for i, (left_endpoint, left, left_box) in enumerate(heads):
        for right_endpoint, right, right_box in heads[i + 1 :]:
            if left_endpoint != right_endpoint:
                continue
            if bbox_overlap_area(left_box, right_box) <= 0.0:
                continue
            overlap = convex_intersection_area(left, right)
            if overlap > EPSILON:
                pairs += 1
                area += overlap
    return len(heads), pairs, area


def sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, help="candidate dot binary")
    parser.add_argument("--base", type=Path, help="f5c8b42e7 dot binary")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("VERIFY_PMPIPE_JOBS", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("VERIFY_PMPIPE_TIMEOUT", "300")))
    args = parser.parse_args()

    repo = repo_root()
    dot = args.dot or current_dot(repo, args.timeout)
    base = args.base or base_dot(repo, args.timeout)
    inputs = corpus(repo)
    print(f"base_sha={BASE_SHA}")
    print(f"public_fixture_inputs={len(inputs)}")

    def check(rel: str):
        base_true_xdot = render_xdot(base, repo, rel, MODES["concentrate_true"], args.timeout)
        cur_true_xdot = render_xdot(dot, repo, rel, MODES["concentrate_true"], args.timeout)
        rows = {}
        for mode, flags in MODES.items():
            rows[mode] = (
                render_json(base, repo, rel, flags, args.timeout),
                render_json(dot, repo, rel, flags, args.timeout),
            )
        return rel, base_true_xdot, cur_true_xdot, rows

    changed = []
    overlap_increases = []
    turn_increases = []
    y_drops = []
    unexpected_render_failures = []
    pmpipe_true = None

    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(check, rel): rel for rel in inputs}
        for done, future in enumerate(futures.as_completed(pending), 1):
            rel, base_true_xdot, cur_true_xdot, rows = future.result()
            if (
                base_true_xdot.returncode == 0
                and cur_true_xdot.returncode == 0
                and sha(base_true_xdot.stdout) != sha(cur_true_xdot.stdout)
                and Path(rel).name not in NONDETERMINISTIC_IDENTITY_NAMES
            ):
                changed.append(rel)

            fixture_overlap_increase = False
            fixture_turn_increase = False
            fixture_y_drop = False
            for mode, (base_json, cur_json) in rows.items():
                if base_json.returncode == 0 and cur_json.returncode != 0:
                    unexpected_render_failures.append((rel, mode, cur_json.returncode))
                    continue
                if base_json.returncode != 0 or cur_json.returncode != 0:
                    continue
                base_overlap = arrowhead_overlap(base_json.stdout)
                cur_overlap = arrowhead_overlap(cur_json.stdout)
                if rel == "graphs/directed/pmpipe.gv" and mode == "concentrate_true":
                    pmpipe_true = (base_overlap, cur_overlap)
                if cur_overlap[2] > base_overlap[2] + EPSILON:
                    fixture_overlap_increase = True
                base_turn = turn_score(base_json.stdout)
                cur_turn = turn_score(cur_json.stdout)
                if cur_turn[0] > base_turn[0]:
                    fixture_turn_increase = True
                if mode == "default":
                    before_y = real_node_y_count(base_json.stdout)
                    after_y = real_node_y_count(cur_json.stdout)
                    if after_y < before_y:
                        fixture_y_drop = True
            if fixture_overlap_increase:
                overlap_increases.append(rel)
            if fixture_turn_increase:
                turn_increases.append(rel)
            if fixture_y_drop:
                y_drops.append(rel)
            if done % 25 == 0 or done == len(inputs):
                print(f"progress_checked={done}/{len(inputs)}", flush=True)

    changed = sorted(changed)
    changed_names = sorted({Path(rel).name for rel in changed})
    overlap_increases = sorted(set(overlap_increases))
    turn_increases = sorted(set(turn_increases))
    y_drops = sorted(set(y_drops))

    if pmpipe_true is None:
        print("pmpipe_true_overlap_pt2 = MISSING")
        pmpipe_ok = False
    else:
        before, after = pmpipe_true
        print(
            f"pmpipe_true_heads = {after[0]} (was {before[0]})\n"
            f"pmpipe_true_overlap_pairs = {after[1]} (was {before[1]})\n"
            f"pmpipe_true_overlap_pt2 = {after[2]:.3f} (was {before[2]:.3f})"
        )
        pmpipe_ok = after[2] <= EPSILON

    print(
        "concentrate_true_changed_fixtures = "
        + (" ".join(changed_names) if changed_names else "(none)")
    )
    for rel in changed:
        print(f"concentrate_true_changed_fixture_path\t{rel}")
    print(f"overlap_increase_fixtures = {len(overlap_increases)}")
    for rel in overlap_increases[:50]:
        print(f"overlap_increase\t{rel}")
    print(f"turn_increase_fixtures = {len(turn_increases)}")
    for rel in turn_increases[:50]:
        print(f"turn_increase\t{rel}")
    print(f"distinct_y_drop_cases = {len(y_drops)}")
    for rel in y_drops[:50]:
        print(f"distinct_y_drop\t{rel}")
    print(f"unexpected_render_failures = {len(unexpected_render_failures)}")
    for rel, mode, rc in unexpected_render_failures[:50]:
        print(f"unexpected_render_failure\t{rel}\t{mode}\trc={rc}")

    crash_failures = []
    for rel in CRASH_FIXTURES:
        proc = render_json(dot, repo, rel, MODES["concentrate_true"], args.timeout)
        print(f"crash_fixture_rc\t{rel}\t{proc.returncode}")
        if proc.returncode != 0:
            crash_failures.append(rel)

    expected_changed = ["pmpipe.gv"]
    ok = (
        pmpipe_ok
        and changed_names == expected_changed
        and not overlap_increases
        and not turn_increases
        and not y_drops
        and not unexpected_render_failures
        and not crash_failures
    )
    print("OK verify_pmpipe_concentrate_overlap" if ok else "FAIL verify_pmpipe_concentrate_overlap")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
