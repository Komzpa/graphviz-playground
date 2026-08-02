#!/usr/bin/env python3
"""Compare arrow spread regressions against a supplied reference dot binary."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import math
import resource
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ANGLE_DEGREES = 35.0
SAMPLES_PER_CUBIC = 24
MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 60
JOBS = 8

NONDETERMINISTIC_IDENTITY_NAMES = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}


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
    layouts: list[dict[str, Any]] | None = None
    error: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(args: list[Path | str], cwd: Path, *, memory: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=TIMEOUT,
        preexec_fn=cap_memory if memory else None,
    )


def current_dot(root: Path) -> Path:
    return root / "build" / "cmd" / "dot" / "dot_builtins"


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


def mode_flags(mode: str) -> tuple[str, ...]:
    if mode == "default":
        return ()
    if mode == "false":
        return ("-Gconcentrate=false",)
    if mode == "true":
        return ("-Gconcentrate=true",)
    raise ValueError(mode)


def graph_inputs(root: Path) -> list[str]:
    paths: set[str] = set()
    for directory in (
        root / "tests" / "graphs",
        root / "graphs" / "directed",
        root / "graphs" / "undirected",
    ):
        paths.update(str(path.relative_to(root)) for path in directory.rglob("*.gv"))
        paths.update(str(path.relative_to(root)) for path in directory.rglob("*.dot"))
    return sorted(paths)


def render(dot: Path, root: Path, rel: str, mode: str) -> Render:
    flags = mode_flags(mode)
    try:
        xdot = run([dot, *flags, "-Txdot", root / rel], root)
        if xdot.returncode != 0:
            return Render(rel, mode, xdot.returncode, error=xdot.stderr.decode("utf-8", "replace")[:400])
        js = run([dot, *flags, "-Tjson", root / rel], root)
        if js.returncode != 0:
            return Render(rel, mode, js.returncode, xdot=xdot.stdout, error=js.stderr.decode("utf-8", "replace")[:400])
        return Render(rel, mode, 0, xdot=xdot.stdout, layouts=parse_json_stream(js.stdout))
    except subprocess.TimeoutExpired:
        return Render(rel, mode, "TIMEOUT")


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
                if turn_angle(a, b, c) > ANGLE_DEGREES:
                    count += 1
    return count


def real_node_y_count(layouts: list[dict[str, Any]]) -> int:
    ys = set()
    for layout_index, layout in enumerate(layouts):
        for obj in layout.get("objects", []):
            if "pos" not in obj:
                continue
            width = float(obj.get("width", 0.0))
            if width <= 0.05:
                continue
            name = obj.get("name", "")
            if name.startswith("_concentrate_junction_") or "pos" not in obj:
                continue
            try:
                _, y = obj["pos"].split(",", 1)
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
                    endpoint = str(edge.get("head" if stream == "_hdraw_" else "tail", ""))
                    edge_id = f"{layout_index}:{edge_index}:{stream}"
                    polygons.append((edge_id, endpoint, poly))
    return polygons


def bbox(poly: list[Point]) -> tuple[float, float, float, float]:
    xs = [p.x for p in poly]
    ys = [p.y for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def signed_area(poly: list[Point]) -> float:
    return (
        sum(poly[i].x * poly[(i + 1) % len(poly)].y - poly[(i + 1) % len(poly)].x * poly[i].y for i in range(len(poly)))
        / 2.0
    )


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
    heads = [(endpoint, poly, bbox(poly)) for _, endpoint, poly in arrow_polygons(layouts)]
    total = 0.0
    for i, (left_endpoint, left_poly, left_box) in enumerate(heads):
        for right_endpoint, right_poly, right_box in heads[i + 1 :]:
            if left_endpoint == right_endpoint and bbox_overlap_area(left_box, right_box) > 0.0:
                total += convex_intersection_area(left_poly, right_poly)
    return total


def excluded_identity_fixture(rel: str) -> bool:
    return Path(rel).name in NONDETERMINISTIC_IDENTITY_NAMES


def comparable(before: Render, after: Render) -> bool:
    return before.rc == 0 and after.rc == 0


def nondeterministic_render(dot: Path, root: Path, rel: str, mode: str, first: bytes) -> bool:
    second = render(dot, root, rel, mode)
    return second.rc == 0 and second.xdot != first


def machine_readable_fixtures(values: list[str]) -> str:
    return ",".join(sorted(values))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path)
    parser.add_argument("--reference-dot", type=Path, required=True, dest="reference_dot")
    parser.add_argument("--root", type=Path, default=repo_root())
    args = parser.parse_args()

    root = args.root.resolve()
    reference_dot = args.reference_dot.resolve()
    dot = args.dot.resolve() if args.dot else current_dot(root)
    if not dot.exists() or not reference_dot.exists():
        raise SystemExit("missing dot binary for current or --reference-dot")

    rels = graph_inputs(root)
    modes = ("default", "false", "true")
    overlap_modes = modes
    work_items = []
    for binary in (reference_dot, dot):
        for mode in modes:
            for rel in rels:
                work_items.append((binary, mode, rel))

    renders: dict[tuple[str, str, str], Render] = {}
    with futures.ThreadPoolExecutor(max_workers=JOBS) as pool:
        future_map = {
            pool.submit(render, binary, root, rel, mode): (binary, mode, rel)
            for binary, mode, rel in work_items
        }
        total = len(future_map)
        for done, fut in enumerate(futures.as_completed(future_map), 1):
            binary, mode, rel = future_map[fut]
            key = ("ref" if binary == reference_dot else "current", mode, rel)
            renders[key] = fut.result()
            if done % 100 == 0 or done == total:
                print(f"progress_renders={done}/{total}", flush=True)

    overlap_increase_fixtures: set[str] = set()
    turn_increase_fixtures: set[str] = set()
    distinct_y_drop_cases: set[str] = set()
    concentrate_true_changed_fixtures: set[str] = set()

    for rel in rels:
        for mode in overlap_modes:
            before = renders[("ref", mode, rel)]
            after = renders[("current", mode, rel)]
            if not comparable(before, after) or before.layouts is None or after.layouts is None:
                continue
            before_overlap = arrowhead_overlap_area(before.layouts)
            after_overlap = arrowhead_overlap_area(after.layouts)
            if after_overlap > before_overlap + 1e-6:
                overlap_increase_fixtures.add(rel)
            before_turns = sharp_turn_count(before.layouts)
            after_turns = sharp_turn_count(after.layouts)
            if after_turns > before_turns:
                turn_increase_fixtures.add(rel)

        rank_before, rank_after = 0, 0
        for mode in modes:
            before = renders[("ref", mode, rel)]
            after = renders[("current", mode, rel)]
            if not comparable(before, after) or before.layouts is None or after.layouts is None:
                continue
            rank_before += real_node_y_count(before.layouts)
            rank_after += real_node_y_count(after.layouts)
        if rank_after < rank_before:
            distinct_y_drop_cases.add(rel)

        before = renders[("ref", "true", rel)]
        after = renders[("current", "true", rel)]
        if comparable(before, after) and before.xdot != after.xdot:
            if excluded_identity_fixture(rel):
                continue
            if (
                nondeterministic_render(reference_dot, root, rel, "true", before.xdot)
                or nondeterministic_render(dot, root, rel, "true", after.xdot)
            ):
                continue
            concentrate_true_changed_fixtures.add(rel)

    print(f"overlap_increase_fixtures={len(overlap_increase_fixtures)}")
    for rel in sorted(overlap_increase_fixtures):
        print(f"overlap_increase_fixture={rel}")
    print(f"turn_increase_fixtures={len(turn_increase_fixtures)}")
    for rel in sorted(turn_increase_fixtures):
        print(f"turn_increase_fixture={rel}")
    print(f"distinct_y_drop_cases={len(distinct_y_drop_cases)}")
    for rel in sorted(distinct_y_drop_cases):
        print(f"distinct_y_drop_case={rel}")
    changed = sorted(concentrate_true_changed_fixtures)
    print(f"concentrate_true_changed_count={len(changed)}")
    print(f"concentrate_true_changed_fixtures={machine_readable_fixtures(changed)}")
    inherited_render_failures = []
    unexpected_render_failures = []
    for rel in rels:
        for mode in modes:
            before = renders[("ref", mode, rel)]
            after = renders[("current", mode, rel)]
            if before.rc == 0 and after.rc != 0:
                unexpected_render_failures.append((mode, rel, after.rc))
            elif before.rc != 0 and after.rc != 0:
                inherited_render_failures.append((mode, rel, before.rc, after.rc))
    print(f"unexpected_render_failures={len(unexpected_render_failures)}")
    for mode, rel, rc in sorted(unexpected_render_failures)[:50]:
        print(f"unexpected_render_failure={mode},{rel},rc={rc}")
    print(f"inherited_render_failures={len(inherited_render_failures)}")
    for mode, rel, ref_rc, cur_rc in sorted(inherited_render_failures)[:50]:
        print(f"inherited_render_failure={mode},{rel},ref_rc={ref_rc},current_rc={cur_rc}")
    if (
        overlap_increase_fixtures
        or turn_increase_fixtures
        or distinct_y_drop_cases
        or unexpected_render_failures
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
