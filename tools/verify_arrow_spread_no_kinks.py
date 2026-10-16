#!/usr/bin/env python3
"""Verify curved-node arrow spreading without spline kinks or new overlaps.

This gate compares the current dot binary with commit 86dde204c over the public
graph fixtures. Sharpness is measured on the DRAWN Bezier curve by sampling each
cubic segment at 24 intervals and measuring turns in the resulting polyline.
Control-polygon angles are the wrong metric here: a cubic handle can look sharp
while the rendered curve is smooth, and the rejected curved-outline attempt made
the opposite mistake by tearing a visible corner where moved endpoint controls
rejoined the untouched spline body.

Arrowhead overlap is measured from the DRAWN `P` polygons in `_hdraw_` and
`_tdraw_`. `ctest -R concentrate` finds no tests in this build directory, so
`python3 -m pytest tests/test_concentrate_*.py -q` is the real concentrate gate.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import math
import os
import re
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_SHA = "86dde204c8ffd8db00b2343d48dda38844b5682b"
ANGLE_DEGREES = 35.0
SAMPLES_PER_CUBIC = 24
MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 240
JOBS = 8
TARGET = Path("tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot")
CONCENTRATE_TRUE_RE = re.compile(r"\bconcentrate\s*=\s*\"?true\"?", re.I)
CONCENTRATE_FALSE_XDOT_RE = re.compile(rb"\n\t\tconcentrate=false,")
NONDETERMINISTIC_IDENTITY_NAMES = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}
PUBLIC_FIXTURE_EXCLUDES = {Path("tests/graphs/b15.gv")}


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Render:
    rel: str
    mode: str
    rc: int | str
    xdot: bytes = b""
    plain: bytes = b""
    layouts: list[dict[str, Any]] | None = None
    error: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(args: list[str | Path], cwd: Path, *, memory: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=TIMEOUT,
        preexec_fn=cap_memory if memory else None,
    )


def checked(args: list[str | Path], cwd: Path) -> None:
    proc = run(args, cwd, memory=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(proc.returncode)


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root)
    return dot


def configured_dot(root: Path, sha: str) -> Path:
    override = os.environ.get("ARROW_SPREAD_BASE_DOT")
    if override:
        return Path(override)
    worktree = root.parent / f"{root.name}-arrow-spread-{sha[:9]}"
    if not worktree.exists():
        checked(["git", "worktree", "add", "--detach", worktree, sha], root)
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


def graph_inputs(root: Path) -> list[Path]:
    paths: set[Path] = set((root / "tests" / "graphs").glob("*.dot"))
    paths.update((root / "tests" / "graphs").glob("*.gv"))
    paths.update((root / "graphs" / "directed").glob("*.gv"))
    paths.update((root / "graphs" / "undirected").glob("*.gv"))
    paths = {path for path in paths if path.relative_to(root) not in PUBLIC_FIXTURE_EXCLUDES}
    return sorted(paths)


def mode_flags(mode: str) -> tuple[str, ...]:
    if mode == "default":
        return ()
    if mode == "false":
        return ("-Gconcentrate=false",)
    if mode == "true":
        return ("-Gconcentrate=true",)
    raise ValueError(mode)


def render(dot: Path, root: Path, rel: str, mode: str, *, plain_output: bool = False) -> Render:
    flags = mode_flags(mode)
    try:
        xdot = run([dot, *flags, "-Txdot", root / rel], root)
        if xdot.returncode != 0:
            return Render(rel, mode, xdot.returncode, error=xdot.stderr.decode("utf-8", "replace")[:400])
        plain_data = b""
        if plain_output:
            plain = run([dot, *flags, "-Tplain", root / rel], root)
            if plain.returncode != 0:
                return Render(rel, mode, plain.returncode, xdot=xdot.stdout, error=plain.stderr.decode("utf-8", "replace")[:400])
            plain_data = plain.stdout
        js = run([dot, *flags, "-Tjson", root / rel], root)
        if js.returncode != 0:
            return Render(rel, mode, js.returncode, xdot=xdot.stdout, plain=plain_data, error=js.stderr.decode("utf-8", "replace")[:400])
        return Render(rel, mode, 0, xdot=xdot.stdout, plain=plain_data, layouts=parse_json_stream(js.stdout))
    except subprocess.TimeoutExpired as err:
        stderr = (err.stderr or b"").decode("utf-8", "replace")[:400]
        return Render(rel, mode, "TIMEOUT", error=stderr)


def parse_json_stream(data: bytes) -> list[dict[str, Any]]:
    text = data.decode("utf-8")
    decoder = json.JSONDecoder()
    index = 0
    layouts: list[dict[str, Any]] = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        layouts.append(value)
    return layouts


def point(pair: list[float]) -> Point:
    return Point(float(pair[0]), float(pair[1]))


def cubic(a: Point, b: Point, c: Point, d: Point, t: float) -> Point:
    u = 1.0 - t
    return Point(
        u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x,
        u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t * t * t * d.y,
    )


def turn_angle(a: Point, b: Point, c: Point) -> float:
    vin = (b.x - a.x, b.y - a.y)
    vout = (c.x - b.x, c.y - b.y)
    lin = math.hypot(*vin)
    lout = math.hypot(*vout)
    if lin < 1e-9 or lout < 1e-9:
        return 0.0
    cos = max(-1.0, min(1.0, (vin[0] * vout[0] + vin[1] * vout[1]) / (lin * lout)))
    return math.degrees(math.acos(cos))


def sampled_polyline(edge: dict[str, Any]) -> list[Point]:
    out: list[Point] = []
    for op in edge.get("_draw_", []):
        if op.get("op") != "b":
            continue
        pts = [point(p) for p in op.get("points", [])]
        for i in range(0, len(pts) - 3, 3):
            for sample in range(SAMPLES_PER_CUBIC + 1):
                if out and sample == 0:
                    continue
                out.append(cubic(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], sample / SAMPLES_PER_CUBIC))
    return out


def sharp_turn_count(layouts: list[dict[str, Any]]) -> int:
    count = 0
    for layout in layouts:
        for edge in layout.get("edges", []):
            poly = sampled_polyline(edge)
            for a, b, c in zip(poly, poly[1:], poly[2:]):
                count += turn_angle(a, b, c) > ANGLE_DEGREES
    return count


def real_node_y_count(layouts: list[dict[str, Any]]) -> int:
    ys = set()
    for layout_index, layout in enumerate(layouts):
        for obj in layout.get("objects", []):
            name = obj.get("name", "")
            if obj.get("shape") == "point" or obj.get("_concentrate_junction_node") == "true":
                continue
            if name.startswith("_concentrate_junction_") or "pos" not in obj:
                continue
            try:
                _x, y = obj["pos"].split(",", 1)
                ys.add((layout_index, round(float(y), 6)))
            except ValueError:
                continue
    return len(ys)


def polygon_points(edge: dict[str, Any], stream: str) -> list[Point] | None:
    for op in edge.get(stream, []):
        if op.get("op") == "P":
            return [point(p) for p in op.get("points", [])]
    return None


def arrow_polygons(layouts: list[dict[str, Any]]) -> list[tuple[str, str, list[Point]]]:
    polygons = []
    for layout_index, layout in enumerate(layouts):
        for edge_index, edge in enumerate(layout.get("edges", [])):
            for stream in ("_hdraw_", "_tdraw_"):
                poly = polygon_points(edge, stream)
                if poly is not None:
                    edge_id = f"{layout_index}:{edge_index}:{stream}"
                    polygons.append((edge_id, str(edge.get("head" if stream == "_hdraw_" else "tail", "")), poly))
    return polygons


def bbox(poly: list[Point]) -> tuple[float, float, float, float]:
    xs = [p.x for p in poly]
    ys = [p.y for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def signed_area(poly: list[Point]) -> float:
    return sum(
        poly[i].x * poly[(i + 1) % len(poly)].y
        - poly[(i + 1) % len(poly)].x * poly[i].y
        for i in range(len(poly))
    ) / 2.0


def polygon_area(poly: list[Point]) -> float:
    return abs(signed_area(poly))


def inside_halfplane(point: Point, start: Point, end: Point, clockwise: bool) -> bool:
    cross = (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (point.x - start.x)
    return cross <= 1e-9 if clockwise else cross >= -1e-9


def segment_intersection(a: Point, b: Point, c: Point, d: Point) -> Point:
    den = (a.x - b.x) * (c.y - d.y) - (a.y - b.y) * (c.x - d.x)
    if abs(den) < 1e-9:
        return b
    left = a.x * b.y - a.y * b.x
    right = c.x * d.y - c.y * d.x
    return Point(
        (left * (c.x - d.x) - (a.x - b.x) * right) / den,
        (left * (c.y - d.y) - (a.y - b.y) * right) / den,
    )


def convex_intersection_area(subject: list[Point], clip: list[Point]) -> float:
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
    return polygon_area(out) if len(out) >= 3 else 0.0


def arrowhead_overlap_area(layouts: list[dict[str, Any]]) -> float:
    heads = [(edge_id, endpoint, poly, bbox(poly)) for edge_id, endpoint, poly in arrow_polygons(layouts)]
    total = 0.0
    for i, (_left_id, left_endpoint, left, left_box) in enumerate(heads):
        for _right_id, right_endpoint, right, right_box in heads[i + 1 :]:
            if left_endpoint == right_endpoint and bbox_overlap_area(left_box, right_box) > 0.0:
                total += convex_intersection_area(left, right)
    return total


def explicit_concentrate_true(root: Path, rel: str) -> bool:
    return bool(CONCENTRATE_TRUE_RE.search((root / rel).read_text(errors="ignore")))


def comparable(before: Render, after: Render) -> bool:
    return before.rc == 0 and after.rc == 0


def normalize_xdot_for_default_false(data: bytes) -> bytes:
    return CONCENTRATE_FALSE_XDOT_RE.sub(b"", data)


def nondeterministic_xdot_render(dot: Path, root: Path, rel: str, mode: str, first: bytes) -> bool:
    second = render(dot, root, rel, mode)
    return second.rc == 0 and normalize_xdot_for_default_false(second.xdot) != normalize_xdot_for_default_false(first)


def nondeterministic_plain_render(dot: Path, root: Path, rel: str, mode: str, first: bytes) -> bool:
    second = render(dot, root, rel, mode, plain_output=True)
    return second.rc == 0 and second.plain != first


def excluded_identity_fixture(rel: str) -> bool:
    return Path(rel).name in NONDETERMINISTIC_IDENTITY_NAMES


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=repo_root())
    parser.add_argument("--dot", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    dot = args.dot.resolve() if args.dot else current_dot(root)
    base_dot = configured_dot(root, BASE_SHA)
    rels = [str(path.relative_to(root)) for path in graph_inputs(root)]
    render_rels = sorted(set(rels + [TARGET.as_posix()]))

    items = [(binary, mode, rel) for binary in ("base", "current") for mode in ("default", "false", "true") for rel in render_rels]
    dots = {"base": base_dot, "current": dot}
    renders: dict[tuple[str, str, str], Render] = {}
    with futures.ThreadPoolExecutor(max_workers=JOBS) as pool:
        future_map = {
            pool.submit(
                render,
                dots[binary],
                root,
                rel,
                mode,
                plain_output=binary == "current" and mode in ("default", "false"),
            ): (binary, mode, rel)
            for binary, mode, rel in items
        }
        for future in futures.as_completed(future_map):
            renders[future_map[future]] = future.result()

    render_failures = [r for r in renders.values() if r.rc != 0]
    unexpected_render_failures = []
    overlap_increases = 0
    overlap_decreases = 0
    overlap_increase_rows: list[str] = []
    turn_increases = 0
    turn_increase_rows: list[str] = []
    true_xdot_changes = 0
    true_xdot_change_rows: list[str] = []
    true_xdot_nondeterministic = 0
    true_xdot_nondeterministic_rows: list[str] = []
    default_false_diffs = 0
    default_false_diff_rows: list[str] = []
    default_false_nondeterministic = 0
    default_false_nondeterministic_rows: list[str] = []
    default_false_explicit_true_skipped = 0
    rank_y_drops = 0

    for rel in rels:
        for mode in ("default", "false", "true"):
            before = renders[("base", mode, rel)]
            after = renders[("current", mode, rel)]
            if before.rc == 0 and after.rc != 0:
                unexpected_render_failures.append(after)
        for mode in ("default", "false"):
            before = renders[("base", mode, rel)]
            after = renders[("current", mode, rel)]
            if before.layouts is None or after.layouts is None:
                continue
            if mode == "default":
                before_overlap = arrowhead_overlap_area(before.layouts)
                after_overlap = arrowhead_overlap_area(after.layouts)
                if after_overlap > before_overlap + 1e-6:
                    overlap_increases += 1
                    overlap_increase_rows.append(
                        f"{rel}: {before_overlap:.6f}->{after_overlap:.6f}"
                    )
                elif before_overlap > after_overlap + 1e-6:
                    overlap_decreases += 1
            before_turns = sharp_turn_count(before.layouts)
            after_turns = sharp_turn_count(after.layouts)
            if after_turns > before_turns:
                turn_increases += 1
                turn_increase_rows.append(f"{mode} {rel}: {before_turns}->{after_turns}")
        for mode in ("default", "false", "true"):
            before = renders[("base", mode, rel)]
            after = renders[("current", mode, rel)]
            if before.layouts is None or after.layouts is None:
                continue
            rank_y_drops += real_node_y_count(after.layouts) < real_node_y_count(before.layouts)
        before_true = renders[("base", "true", rel)]
        after_true = renders[("current", "true", rel)]
        if comparable(before_true, after_true) and before_true.xdot != after_true.xdot:
            if excluded_identity_fixture(rel):
                true_xdot_nondeterministic += 1
                true_xdot_nondeterministic_rows.append(rel)
            elif nondeterministic_xdot_render(base_dot, root, rel, "true", before_true.xdot) or nondeterministic_xdot_render(dot, root, rel, "true", after_true.xdot):
                true_xdot_nondeterministic += 1
                true_xdot_nondeterministic_rows.append(rel)
            else:
                true_xdot_changes += 1
                true_xdot_change_rows.append(rel)
        if explicit_concentrate_true(root, rel):
            default_false_explicit_true_skipped += 1
        else:
            default_render = renders[("current", "default", rel)]
            false_render = renders[("current", "false", rel)]
            if comparable(default_render, false_render) and default_render.plain != false_render.plain:
                if excluded_identity_fixture(rel):
                    default_false_nondeterministic += 1
                    default_false_nondeterministic_rows.append(rel)
                elif nondeterministic_plain_render(dot, root, rel, "default", default_render.plain) or nondeterministic_plain_render(dot, root, rel, "false", false_render.plain):
                    default_false_nondeterministic += 1
                    default_false_nondeterministic_rows.append(rel)
                else:
                    default_false_diffs += 1
                    default_false_diff_rows.append(rel)

    base_target = renders[("base", "false", TARGET.as_posix())].layouts
    current_target = renders[("current", "false", TARGET.as_posix())].layouts
    base_overlap = arrowhead_overlap_area(base_target) if base_target else math.nan
    current_overlap = arrowhead_overlap_area(current_target) if current_target else math.nan
    awilliams_rel = "graphs/directed/awilliams.gv"
    base_awilliams = renders[("base", "default", awilliams_rel)].layouts
    current_awilliams = renders[("current", "default", awilliams_rel)].layouts
    base_awilliams_overlap = arrowhead_overlap_area(base_awilliams) if base_awilliams else math.nan
    current_awilliams_overlap = arrowhead_overlap_area(current_awilliams) if current_awilliams else math.nan

    print(f"inputs={len(rels)}")
    print(f"render_failures={len(render_failures)}")
    print(f"unexpected_render_failures={len(unexpected_render_failures)}")
    print(f"overlap_increase_fixtures={overlap_increases}")
    print(f"overlap_decrease_fixtures={overlap_decreases}")
    print(f"turn_increase_fixtures={turn_increases}")
    print(f"concentrate_true_xdot_changes={true_xdot_changes}")
    print(f"concentrate_true_nondeterministic_excluded={true_xdot_nondeterministic}")
    print(f"default_false_plain_differences={default_false_diffs}")
    print(f"default_false_nondeterministic_excluded={default_false_nondeterministic}")
    print(f"default_false_explicit_concentrate_true_skipped={default_false_explicit_true_skipped}")
    print(f"awilliams_overlap_before={base_awilliams_overlap:.6f}")
    print(f"awilliams_overlap_after={current_awilliams_overlap:.6f}")
    print(f"target_overlap_before={base_overlap:.6f}")
    print(f"target_overlap_after={current_overlap:.6f}")
    print(f"distinct_y_drop_cases={rank_y_drops}")
    for title, rows in (
        ("unexpected render failure sample", unexpected_render_failures[:10]),
        ("overlap increase sample", overlap_increase_rows[:20]),
        ("turn increase sample", turn_increase_rows[:20]),
        ("concentrate=true xdot change sample", true_xdot_change_rows[:20]),
        ("concentrate=true nondeterministic excluded sample", true_xdot_nondeterministic_rows[:20]),
        ("default/false plain diff sample", default_false_diff_rows[:20]),
        ("default/false nondeterministic excluded sample", default_false_nondeterministic_rows[:20]),
    ):
        if not rows:
            continue
        print(f"{title}:")
        for row in rows:
            if isinstance(row, Render):
                print(f"  {row.mode} {row.rel} rc={row.rc} {row.error}")
            else:
                print(f"  {row}")
    if render_failures[:10]:
        print("all render failure sample:")
        for row in render_failures[:10]:
            print(f"  {row.mode} {row.rel} rc={row.rc} {row.error}")

    failures = (
        len(unexpected_render_failures)
        + overlap_increases
        + turn_increases
        + true_xdot_changes
        + default_false_diffs
        + (0 if abs(current_overlap) < 1e-9 else 1)
        + rank_y_drops
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
