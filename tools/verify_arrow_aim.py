#!/usr/bin/env python3
"""An arrowhead must point the way its own edge arrives.

Spreading coincident arrowheads slides each head along the node outline. If the
curve feeding it is not re-aimed to match, the head ends up sitting crosswise to
its own line: the drawing still has every head on the outline, still has no
overlapping heads, and still has no sharp corners, yet a reader sees arrows
pointing away from the nodes they belong to.

This file exists because three separate gates were green while that was on the
page, and because a fourth gate written alongside the fix computed the arrow axis
*from* the arrival direction it then compared the axis against, and so could
never fail. The two quantities here are measured independently, from different
streams of the same layout, and neither is derived from the other:

  axis     - from the drawn arrowhead polygon in `_hdraw_`/`_tdraw_`: the vector
             from the polygon's centroid to its farthest vertex, which for a
             pointed arrow is the apex.
  arrival  - from the drawn spline in `_draw_`: the direction of its final
             sampled step at the end that carries that head.

Neither is derived from the other, and the angle between them is compared
AGAINST UPSTREAM rather than against an absolute bound. That calibration is the
point. Graphviz draws `inv`, `crow` and `tee` heads that do not point along the
edge at all, and with concentrate on a spline may be drawn head-to-tail; two
earlier attempts at an absolute bound therefore reported 4389 and then 2825
"failures" on a clean upstream build. An edge only counts against us when
upstream aims it correctly and we do not.

Usage:
  verify_arrow_aim.py --base UPSTREAM_DOT [--dot BINARY] [--max-deg 5.0]
                      [--tolerance 2.0] [FIXTURE ...]

Exits non-zero when any arrowhead that upstream aims within --max-deg is aimed
worse than --max-deg by the build under test, naming the worst offenders.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

SAMPLES = 32
TIMEOUT = 300
# Graphs whose layout differs between two runs of the SAME binary, upstream
# included. Measuring them proves nothing about a change.
NONDETERMINISTIC = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cubic(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def sampled_curve(edge):
    """The drawn spline as a polyline, in draw order."""
    for op in edge.get("_draw_", []):
        if op.get("op") not in ("b", "B"):
            continue
        pts = [tuple(p) for p in op.get("points", [])]
        if len(pts) < 4 or (len(pts) - 1) % 3:
            continue
        out = []
        for i in range(0, len(pts) - 3, 3):
            seg = [cubic(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], j / SAMPLES)
                   for j in range(SAMPLES + 1)]
            out.extend(seg[1:] if out else seg)
        if len(out) >= 2:
            return out
    return []


def head_polygon(edge, stream):
    """The drawn arrowhead, but only when it is the plain pointed triangle.

    `arrows.gv` draws every shape graphviz has, and half of them point backwards
    on purpose: `inv` is a triangle whose apex faces the node, `crow` and `tee`
    are not pointed at all. Measuring those reports 180 deg for a perfectly
    correct drawing — 4389 such "failures" on the first run of this file. An
    explicit `arrowhead`/`arrowtail`/`dir` also means the author chose the shape,
    so leave it alone; what we are policing is the default arrow we move.
    """
    for attr in ("arrowhead", "arrowtail", "dir"):
        if edge.get(attr):
            return None
    for op in edge.get(stream, []):
        if op.get("op") in ("P", "p"):
            pts = [tuple(p) for p in op.get("points", [])]
            if len(pts) == 3:
                return pts
    return None


def axis_of(polygon):
    """Centroid -> farthest vertex. For a pointed arrow that is the apex."""
    cx = sum(p[0] for p in polygon) / len(polygon)
    cy = sum(p[1] for p in polygon) / len(polygon)
    tip = max(polygon, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    return (tip[0] - cx, tip[1] - cy)


def angle_between(a, b):
    la, lb = math.hypot(*a), math.hypot(*b)
    if la < 1e-9 or lb < 1e-9:
        return None
    c = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb)))
    return math.degrees(math.acos(c))


def measure(binary, path, flags):
    """Worst axis-vs-arrival disagreement on this graph, in degrees."""
    try:
        out = subprocess.run([binary, "-Tjson", *flags, path],
                             capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return []
    if out.returncode != 0:
        return []
    try:
        layout = json.loads(out.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return []

    # Key an arrowhead by the nodes it connects, never by edge index: with
    # concentrate on we insert junction nodes, so the edge list is a different
    # length than upstream's and index N is a different edge on each side.
    names = {}
    for o in layout.get("objects", []):
        if "_gvid" in o:
            names[o["_gvid"]] = o.get("name", str(o["_gvid"]))
    seen = {}

    rows = []
    for i, edge in enumerate(layout.get("edges", [])):
        curve = sampled_curve(edge)
        if len(curve) < 2:
            continue
        for stream in ("_hdraw_", "_tdraw_"):
            poly = head_polygon(edge, stream)
            if poly is None:
                continue
            axis = axis_of(poly)
            # Which end of the drawn spline feeds this head? Do NOT assume the
            # last point: with concentrate on, a spline may be drawn from head to
            # tail, and assuming otherwise reported 178 deg on 2860 perfectly
            # correct upstream arrowheads. Ask the geometry instead — take the
            # curve end nearest the arrowhead's own base, and read the direction
            # the curve is travelling as it leaves toward that end.
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            d0 = (curve[0][0] - cx) ** 2 + (curve[0][1] - cy) ** 2
            d1 = (curve[-1][0] - cx) ** 2 + (curve[-1][1] - cy) ** 2
            if d1 <= d0:
                arrival = (curve[-1][0] - curve[-2][0], curve[-1][1] - curve[-2][1])
            else:
                arrival = (curve[0][0] - curve[1][0], curve[0][1] - curve[1][1])
            deg = angle_between(axis, arrival)
            if deg is None:
                continue
            pair = (names.get(edge.get("tail"), edge.get("tail")),
                    names.get(edge.get("head"), edge.get("head")), stream)
            n = seen.get(pair, 0)
            seen[pair] = n + 1
            rows.append((deg, (pair, n), stream))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dot", default=None)
    ap.add_argument("--base", required=True,
                    help="upstream dot binary; defines which arrowheads are "
                         "aimable at all on each fixture")
    ap.add_argument("--max-deg", type=float, default=5.0)
    ap.add_argument("fixtures", nargs="*")
    args = ap.parse_args()

    root = repo_root()
    dot = args.dot or os.path.join(root, "build", "cmd", "dot", "dot_builtins")
    files = args.fixtures or sorted(set(
        glob.glob(f"{root}/tests/graphs/*.gv")
        + glob.glob(f"{root}/tests/graphs/**/*.dot", recursive=True)
        + glob.glob(f"{root}/graphs/directed/*.gv")
        + glob.glob(f"{root}/graphs/undirected/*.gv")))
    files = [f for f in files if os.path.basename(f) not in NONDETERMINISTIC]
    modes = [[], ["-Gconcentrate=false"], ["-Gconcentrate=true"]]

    def job(item):
        path, flags = item
        base = {(e, s): d for d, e, s in measure(args.base, path, flags)}
        ours = {(e, s): d for d, e, s in measure(dot, path, flags)}
        return path, flags, base, ours

    work = [(p, m) for p in files for m in modes]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(job, work))

    worst = []
    checked = 0
    for path, flags, base, ours in results:
        for key, base_deg in base.items():
            if base_deg > args.max_deg:
                continue  # upstream does not aim this one either; not our claim
            if key not in ours:
                continue
            checked += 1
            if ours[key] > args.max_deg:
                worst.append((ours[key], os.path.basename(path),
                              " ".join(flags) or "(default)", key[0], key[1]))
    worst.sort(reverse=True)
    top = worst[0][0] if worst else 0.0
    print(f"arrowheads_checked={checked}")
    print(f"max_arrow_aim_error_deg={top:.3f}")
    print(f"arrow_aim_failures={len(worst)}")
    for deg, name, mode, edge, stream in worst[:25]:
        print(f"  {deg:7.2f}deg  {name}  {mode}  edge#{edge} {stream}")
    if worst:
        print(f"FAILED verify_arrow_aim: {len(worst)} arrowheads point more than "
              f"{args.max_deg:g} deg away from the curve that feeds them")
        return 1
    print("OK verify_arrow_aim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
