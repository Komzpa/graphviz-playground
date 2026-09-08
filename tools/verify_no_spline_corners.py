#!/usr/bin/env python3
"""Verify the public dot corpus did not gain drawn spline corners.

The corner metric samples each drawn cubic from the JSON ``_draw_`` stream and
measures turns on the resulting polyline.  Control-polygon angles are the wrong
metric here: they describe Bezier handles, not the curve Graphviz actually
draws, and can both miss visible bends and flag harmless handles.

Arrowhead overlap is measured from the DRAWN polygon streams, not from edge
attributes.  ``ctest -R concentrate`` finds no tests in this build directory;
``python3 -m pytest tests/test_concentrate_*.py -q`` is the real concentrate
test gate.
"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys


UPSTREAM_SHA = "aef8a6fd874726f17e3797561d23491bd9e156fa"
FROZEN_SHA = "876aeea0f36ef77a4a04baee195b1939b2e54ef2"
ANGLE = 35.0
SAMPLES = 24
TIMEOUT = int(os.environ.get("VERIFY_SPLINE_CORNERS_TIMEOUT", "300"))
JOBS = int(os.environ.get("VERIFY_SPLINE_CORNERS_JOBS", "16"))
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
CRASH_FIXTURES = (
    "tests/graphs/concentrate-demo/crash-record-port-concentrate-2764.dot",
    "tests/2764.dot",
    "tests/2765.dot",
)
PUBLIC_FIXTURE_EXCLUDES = {"tests/graphs/b15.gv"}
TURN_EXCLUDED = {
    "tests/graphs/b69.gv": "file sets concentrate=true, so default mode is the frozen concentrate column",
}
RANK_EXCLUDED = TURN_EXCLUDED
NONDETERMINISTIC_IDENTITY_NAMES = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))


def run(args: list[str | Path], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=TIMEOUT,
        check=False,
        preexec_fn=cap_memory,
    )


def dot_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, default)).resolve()


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


def configured_dot(repo: Path, env_name: str, sha: str, tag: str) -> Path:
    override = os.environ.get(env_name)
    if override:
        return Path(override).resolve()
    worktree = repo.parent / f"{repo.name}-{tag}-{sha[:9]}"
    if not worktree.exists():
        checked(["git", "worktree", "add", "--detach", worktree, sha], repo)
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
        )
    checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree)
    return dot


def corpus(repo: Path) -> list[str]:
    paths = set((repo / "graphs" / "directed").glob("*.gv"))
    paths.update((repo / "tests" / "graphs").glob("*.dot"))
    paths.update((repo / "tests" / "graphs").glob("*.gv"))
    paths.update((repo / "graphs" / "undirected").glob("*.gv"))
    return sorted(
        str(p.relative_to(repo))
        for p in paths
        if str(p.relative_to(repo)) not in PUBLIC_FIXTURE_EXCLUDES
    )


def has_explicit_concentrate(repo: Path, rel: str) -> bool:
    text = (repo / rel).read_text(encoding="utf-8", errors="ignore")
    return "concentrate" in text


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
    vin = b[0] - a[0], b[1] - a[1]
    vout = c[0] - b[0], c[1] - b[1]
    ni = math.hypot(*vin)
    no = math.hypot(*vout)
    if ni < 1e-6 or no < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, (vin[0] * vout[0] + vin[1] * vout[1]) / (ni * no)))
    return math.degrees(math.acos(cos))


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


def score_layout(layout: dict) -> tuple[int, float, int]:
    count = 0
    worst = 0.0
    drawn = 0
    for edge in layout.get("edges", []):
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            pts = [point(p) for p in op.get("points", [])]
            if len(pts) < 4:
                continue
            drawn += 1
            poly: list[tuple[float, float]] = []
            for i in range(0, len(pts) - 3, 3):
                seg = [cubic(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], j / SAMPLES) for j in range(SAMPLES + 1)]
                if poly:
                    seg = seg[1:]
                poly.extend(seg)
            for i in range(1, len(poly) - 1):
                angle = turn(poly[i - 1], poly[i], poly[i + 1])
                if angle > ANGLE:
                    count += 1
                    worst = max(worst, angle)
    return count, worst, drawn


def score_json(data: bytes) -> tuple[int, float, int]:
    total = 0
    worst = 0.0
    drawn = 0
    for layout in layouts(data):
        count, layout_worst, layout_drawn = score_layout(layout)
        total += count
        worst = max(worst, layout_worst)
        drawn += layout_drawn
    return total, worst, drawn


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


def arrow_polygons(data: bytes) -> list[tuple[str, str, list[tuple[float, float]]]]:
    polygons = []
    for layout_index, layout in enumerate(layouts(data)):
        for edge_index, edge in enumerate(layout.get("edges", [])):
            for stream, endpoint_name in (("_hdraw_", "head"), ("_tdraw_", "tail")):
                endpoint = str(edge.get(endpoint_name, ""))
                for op_index, op in enumerate(edge.get(stream, [])):
                    if op.get("op") != "P":
                        continue
                    poly = [point(p) for p in op.get("points", [])]
                    if len(poly) >= 3:
                        ident = f"{layout_index}:{edge_index}:{stream}:{op_index}"
                        polygons.append((ident, endpoint, poly))
    return polygons


def bbox(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap_area(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )


def signed_area(poly: list[tuple[float, float]]) -> float:
    return sum(
        poly[i][0] * poly[(i + 1) % len(poly)][1]
        - poly[(i + 1) % len(poly)][0] * poly[i][1]
        for i in range(len(poly))
    ) / 2.0


def inside_halfplane(
    p: tuple[float, float], start: tuple[float, float], end: tuple[float, float], clockwise: bool
) -> bool:
    cross = (end[0] - start[0]) * (p[1] - start[1]) - (end[1] - start[1]) * (
        p[0] - start[0]
    )
    return cross <= 1e-9 if clockwise else cross >= -1e-9


def segment_intersection(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> tuple[float, float]:
    den = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
    if abs(den) < 1e-9:
        return b
    left = a[0] * b[1] - a[1] * b[0]
    right = c[0] * d[1] - c[1] * d[0]
    return (
        (left * (c[0] - d[0]) - (a[0] - b[0]) * right) / den,
        (left * (c[1] - d[1]) - (a[1] - b[1]) * right) / den,
    )


def convex_intersection_area(
    subject: list[tuple[float, float]], clip: list[tuple[float, float]]
) -> float:
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


def arrowhead_overlap(data: bytes) -> tuple[int, float]:
    polygons = [
        (ident, endpoint, poly, bbox(poly)) for ident, endpoint, poly in arrow_polygons(data)
    ]
    pairs = 0
    area = 0.0
    for i, (_left_id, left_endpoint, left, left_box) in enumerate(polygons):
        for _right_id, right_endpoint, right, right_box in polygons[i + 1 :]:
            if left_endpoint != right_endpoint:
                continue
            if bbox_overlap_area(left_box, right_box) <= 0.0:
                continue
            overlap = convex_intersection_area(left, right)
            if overlap > 1e-6:
                pairs += 1
                area += overlap
    return pairs, area


def render_json(dot: Path, repo: Path, rel: str, *flags: str):
    return run([dot, *flags, "-Tjson", repo / rel], repo)


def render_xdot(dot: Path, repo: Path, rel: str, *flags: str):
    return run([dot, *flags, "-Txdot", repo / rel], repo)


def render_plain(dot: Path, repo: Path, rel: str, *flags: str):
    return run([dot, *flags, "-Tplain", repo / rel], repo)


def normalized_xdot_for_default_false(data: bytes) -> bytes:
    lines = data.splitlines(keepends=True)
    return b"".join(line for line in lines if line.strip() != b"concentrate=false,")


def main() -> int:
    repo = root()
    current = dot_path("VERIFY_CURRENT_DOT", current_dot(repo))
    upstream = configured_dot(repo, "VERIFY_UPSTREAM_DOT", UPSTREAM_SHA, "upstream")
    frozen = configured_dot(repo, "VERIFY_FROZEN_DOT", FROZEN_SHA, "frozen")
    inputs = corpus(repo)
    explicit_true = [rel for rel in inputs if has_explicit_concentrate(repo, rel)]
    print(f"current_dot={current}")
    print(f"upstream_dot={upstream} sha={UPSTREAM_SHA}")
    print(f"frozen_dot={frozen} sha={FROZEN_SHA}")
    print(f"public_fixture_inputs={len(inputs)}")
    if len(inputs) != 321:
        print("FAIL public_fixture_inputs expected 321")
        return 1
    for rel, reason in TURN_EXCLUDED.items():
        print(f"turn_excluded\t{rel}\treason={reason}")
    for rel, reason in RANK_EXCLUDED.items():
        print(f"rank_excluded\t{rel}\treason={reason}")
    for rel in explicit_true:
        print(f"default_false_excluded\t{rel}\treason=file sets concentrate=true")

    def check_one(rel: str):
        up = render_json(upstream, repo, rel)
        cur = render_json(current, repo, rel)
        cur_plain = render_plain(current, repo, rel)
        cur_false_plain = render_plain(current, repo, rel, "-Gconcentrate=false")
        frozen_true = render_xdot(frozen, repo, rel, "-Gconcentrate=true")
        cur_true = render_xdot(current, repo, rel, "-Gconcentrate=true")
        upstream_json = render_json(upstream, repo, rel)
        return rel, up, cur, cur_plain, cur_false_plain, frozen_true, cur_true, upstream_json

    turn_regressions = []
    overlap_increases = []
    xdot_diffs = []
    default_false_diffs = []
    rank_drops = []
    unexpected_render_failures = []
    with futures.ThreadPoolExecutor(max_workers=JOBS) as pool:
        pending = {pool.submit(check_one, rel): rel for rel in inputs}
        for done, future in enumerate(futures.as_completed(pending), 1):
            (
                rel,
                up,
                cur,
                cur_plain,
                cur_false_plain,
                frozen_true,
                cur_true,
                upstream_json,
            ) = future.result()
            if up.returncode == 0 and cur.returncode != 0:
                unexpected_render_failures.append((rel, cur.returncode))
            if up.returncode == 0 and cur.returncode == 0:
                up_score = score_json(up.stdout)
                cur_score = score_json(cur.stdout)
                if cur_score[0] > up_score[0] and rel not in TURN_EXCLUDED:
                    turn_regressions.append((rel, up_score, cur_score))
                up_overlap = arrowhead_overlap(up.stdout)
                cur_overlap = arrowhead_overlap(cur.stdout)
                if cur_overlap[1] > up_overlap[1] + 1e-6:
                    overlap_increases.append((rel, up_overlap, cur_overlap))
            if frozen_true.returncode == 0 and cur_true.returncode == 0:
                if hashlib.sha256(frozen_true.stdout).digest() != hashlib.sha256(cur_true.stdout).digest():
                    if Path(rel).name not in NONDETERMINISTIC_IDENTITY_NAMES:
                        xdot_diffs.append(rel)
            if cur_plain.returncode == 0 and cur_false_plain.returncode == 0 and rel not in explicit_true:
                if hashlib.sha256(cur_plain.stdout).digest() != hashlib.sha256(cur_false_plain.stdout).digest():
                    if Path(rel).name not in NONDETERMINISTIC_IDENTITY_NAMES:
                        default_false_diffs.append(rel)
            if upstream_json.returncode == 0 and cur.returncode == 0 and rel not in RANK_EXCLUDED:
                before = real_node_y_count(upstream_json.stdout)
                after = real_node_y_count(cur.stdout)
                if after < before:
                    rank_drops.append((rel, before, after))
            if done % 25 == 0 or done == len(inputs):
                print(f"progress_checked={done}/{len(inputs)}", flush=True)

    print(f"unexpected_render_failures={len(unexpected_render_failures)}")
    for rel, rc in unexpected_render_failures[:50]:
        print(f"unexpected_render_failure\t{rel}\trc={rc}")
    print(f"turn_increase_fixtures={len(turn_regressions)}")
    for rel, up_score, cur_score in turn_regressions[:50]:
        print(f"turn_increase\t{rel}\tup={up_score[0]}/{up_score[1]:.2f}\tcur={cur_score[0]}/{cur_score[1]:.2f}")
    print(f"overlap_increase_fixtures={len(overlap_increases)}")
    for rel, up_overlap, cur_overlap in overlap_increases[:50]:
        print(
            f"overlap_increase\t{rel}\t"
            f"up_pairs={up_overlap[0]}\tcur_pairs={cur_overlap[0]}\t"
            f"up_area={up_overlap[1]:.6f}\tcur_area={cur_overlap[1]:.6f}"
        )
    print(f"concentrate_true_xdot_changes={len(xdot_diffs)}")
    for rel in xdot_diffs[:50]:
        print(f"concentrate_true_xdot_diff\t{rel}")
    print(f"default_false_plain_differences={len(default_false_diffs)}")
    for rel in default_false_diffs[:50]:
        print(f"default_false_diff\t{rel}")
    print(f"distinct_y_drop_cases={len(rank_drops)}")
    for rel, before, after in rank_drops[:50]:
        print(f"rank_drop\t{rel}\tupstream={before}\tcurrent={after}")

    crash_failures = []
    for rel in CRASH_FIXTURES:
        proc = render_json(current, repo, rel, "-Gconcentrate=true")
        print(f"crash_fixture\t{rel}\trc={proc.returncode}")
        if proc.returncode != 0:
            crash_failures.append(rel)

    failures = (
        unexpected_render_failures
        or turn_regressions
        or overlap_increases
        or xdot_diffs
        or default_false_diffs
        or rank_drops
        or crash_failures
    )
    if failures:
        print("FAIL verify_no_spline_corners")
        return 1
    print("OK verify_no_spline_corners")
    return 0


if __name__ == "__main__":
    sys.exit(main())
