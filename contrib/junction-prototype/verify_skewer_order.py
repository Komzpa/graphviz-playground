#!/usr/bin/env python3
"""Verify DRAKON skewer ordering for the junction prototype."""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "contrib" / "junction-prototype" / "junctionize.py"
# Label of the reference implementation (the former commit b835b3ffa). Its
# source ships in-tree so a clean checkout can run the comparison.
BASELINE = "b835b3ffa"
BASELINE_TOOL = ROOT / "contrib" / "junction-prototype" / "fixtures" / "baseline_junctionize.py"
# The DRBD state graph is not in the tree. Point SKEWER_DRBD at a local copy to
# include it; without it the in-tree fixtures still run.
DRBD_ENV = "SKEWER_DRBD"
_drbd = os.environ.get(DRBD_ENV, "")
DRBD = Path(_drbd) if _drbd else None
FIXTURES = [
    path
    for path in (
        DRBD,
        ROOT / "graphs" / "directed" / "dfa.gv",
        ROOT / "graphs" / "directed" / "fsm.gv",
        ROOT / "graphs" / "directed" / "unix.gv",
    )
    if path is not None
]
GRAPH_DIRS = (ROOT / "graphs", ROOT / "tests" / "graphs")
GRAMMAR = ROOT / "graphs" / "directed" / "grammar.gv"
PAD = 3.0


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class EdgeRow:
    index: int
    tail: str
    head: str
    label: str
    kind: str
    mean_x: float


@dataclass(frozen=True)
class LabelBox:
    edge_name: str
    label: str
    box: Box


@dataclass(frozen=True)
class Metrics:
    edges: list[EdgeRow]
    rank_axis: str
    label_boxes: list[LabelBox]
    label_label_pairs: list[tuple[str, str]]
    median_forward_x: float | None
    reversed_left_of_median: int
    label_crossed: int
    label_label_overlaps: int
    label_node_overlaps: int
    crossings: int


def run(cmd: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def junctionize(path: Path, *, skewer: bool) -> subprocess.CompletedProcess[bytes]:
    cmd = [sys.executable, str(TOOL), str(path)]
    if not skewer:
        cmd.append("--no-skewer-order")
    return run(cmd)


def baseline_tool(tmpdir: Path) -> Path:
    tool = tmpdir / "junctionize-baseline.py"
    tool.write_bytes(BASELINE_TOOL.read_bytes())
    tool.chmod(0o755)
    return tool


def junctionize_with(tool: Path, path: Path) -> subprocess.CompletedProcess[bytes]:
    return run([sys.executable, str(tool), str(path)])


def graph_corpus() -> list[Path]:
    paths: list[Path] = []
    for directory in GRAPH_DIRS:
        paths.extend(path for path in directory.rglob("*.gv") if path.is_file())
    return sorted(paths)


def dot_parse(dot_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
    return run(["dot", "-Tdot"], input_bytes=dot_bytes)


def dot_json(dot_bytes: bytes) -> dict:
    proc = run(["dot", "-Tjson"], input_bytes=dot_bytes)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return json.loads(proc.stdout)


def dot_crossings(dot_bytes: bytes) -> int:
    proc = run(["dot", "-v", "-Tplain"], input_bytes=dot_bytes)
    stderr = proc.stderr.decode("utf-8", "replace")
    matches = re.findall(r"mincross .*?: (\d+) crossings", stderr)
    if not matches:
        raise RuntimeError(f"could not find mincross summary: {stderr[-400:]}")
    return int(matches[-1])


def point(token: str) -> tuple[float, float]:
    if token.startswith(("e,", "s,")):
        _, x, y = token.split(",", 2)
        return float(x), float(y)
    x, y = token.split(",", 1)
    return float(x), float(y)


def edge_points(pos: str) -> list[tuple[float, float]]:
    return [point(token) for token in pos.split() if "," in token]


def bezier_polyline(
    pts: list[tuple[float, float]], start: tuple[float, float] | None, end: tuple[float, float] | None, steps: int = 16
) -> list[tuple[float, float]]:
    """Flatten the cubic B-spline pieces of one `pos` spline into a polyline.

    `pts` holds 3n+1 control points; consecutive groups of four are cubic Bezier
    segments. Arrow tips (`s,`/`e,` points) extend the curve with a straight run.
    """
    line: list[tuple[float, float]] = []
    if start is not None:
        line.append(start)
    if len(pts) >= 4 and (len(pts) - 1) % 3 == 0:
        line.append(pts[0])
        for i in range(0, len(pts) - 1, 3):
            p0, p1, p2, p3 = pts[i : i + 4]
            for step in range(1, steps + 1):
                t = step / steps
                u = 1.0 - t
                line.append(
                    (
                        u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                        u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
                    )
                )
    else:
        line.extend(pts)
    if end is not None:
        line.append(end)
    return line


def edge_polylines(pos: str) -> list[list[tuple[float, float]]]:
    """Rendered curve of an edge: every `;`-separated spline, flattened."""
    lines: list[list[tuple[float, float]]] = []
    for piece in pos.split(";"):
        start = end = None
        pts: list[tuple[float, float]] = []
        for token in piece.split():
            if "," not in token:
                continue
            if token.startswith("s,"):
                start = point(token)
            elif token.startswith("e,"):
                end = point(token)
            else:
                pts.append(point(token))
        if pts:
            lines.append(bezier_polyline(pts, start, end))
    return lines


def segment_hits_box(a: tuple[float, float], b: tuple[float, float], box: Box) -> bool:
    ax, ay = a
    bx, by = b
    if box.x0 <= ax <= box.x1 and box.y0 <= ay <= box.y1:
        return True
    if box.x0 <= bx <= box.x1 and box.y0 <= by <= box.y1:
        return True
    dx = bx - ax
    dy = by - ay
    p = [-dx, dx, -dy, dy]
    q = [ax - box.x0, box.x1 - ax, ay - box.y0, box.y1 - ay]
    u0, u1 = 0.0, 1.0
    for pi, qi in zip(p, q, strict=True):
        if math.isclose(pi, 0.0):
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            u0 = max(u0, t)
        else:
            u1 = min(u1, t)
    return u0 <= u1


def boxes_overlap(a: Box, b: Box) -> bool:
    return a.x0 < b.x1 and a.x1 > b.x0 and a.y0 < b.y1 and a.y1 > b.y0


def box_text(box: Box) -> str:
    return f"{box.x0:.2f},{box.y0:.2f}..{box.x1:.2f},{box.y1:.2f}"


DEFAULT_FONT_SIZE = 14.0
# Head/tail labels get their own xdot draw attribute. Ordinary `label` and
# `xlabel` share `_ldraw_`, split by `ldraw_label_boxes` below.
ENDPOINT_LABEL_DRAW_KEYS = (("headlabel", "_hldraw_"), ("taillabel", "_tldraw_"))


def label_boxes_for(edge: dict, draw_key: str = "_ldraw_", *, expand: float = 0.0) -> list[Box]:
    """One box per rendered text run (`T`), sized by the active `F` font size."""
    boxes: list[Box] = []
    size = DEFAULT_FONT_SIZE
    for op in edge.get(draw_key, []):
        if op.get("op") == "F":
            size = float(op.get("size", DEFAULT_FONT_SIZE))
        elif op.get("op") == "T":
            x, y = op["pt"]
            width = float(op["width"])
            align = op.get("align", "c")
            x0 = x if align == "l" else x - width if align == "r" else x - width / 2
            boxes.append(Box(x0 - expand, y - size / 2 - expand, x0 + width + expand, y + size / 2 + expand))
    return boxes


def _label_anchor(edge: dict, key: str) -> tuple[float, float] | None:
    raw = str(edge.get(key, ""))
    if "," not in raw:
        return None
    x, y = raw.split(",", 1)
    return float(x), float(y)


def ldraw_label_boxes(edge: dict, *, expand: float = 0.0) -> list[tuple[str, Box]]:
    """Attribute `_ldraw_` runs to `label` or `xlabel`, the two owners sharing it.

    dot emits the ordinary label first, then the xlabel (lib/common/emit.c),
    and records their placed centers in `lp`/`xlp`. With both present, the
    run sequence is therefore split at the point that keeps the leading runs
    nearest `lp` and the trailing runs nearest `xlp`.
    """
    boxes = label_boxes_for(edge, "_ldraw_", expand=expand)
    has_label = bool(edge.get("label"))
    has_xlabel = bool(edge.get("xlabel"))
    if not has_xlabel:
        return [("label", box) for box in boxes]
    if not has_label:
        return [("xlabel", box) for box in boxes]
    lp = _label_anchor(edge, "lp")
    xlp = _label_anchor(edge, "xlp")
    if lp is None or xlp is None:
        return [("label", box) for box in boxes]

    def distance(box: Box, anchor: tuple[float, float]) -> float:
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        return (cx - anchor[0]) ** 2 + (cy - anchor[1]) ** 2

    if len(boxes) == 1:
        return [("label" if distance(boxes[0], lp) <= distance(boxes[0], xlp) else "xlabel", boxes[0])]
    split = min(
        range(1, len(boxes)),
        key=lambda k: sum(distance(box, lp) for box in boxes[:k])
        + sum(distance(box, xlp) for box in boxes[k:]),
    )
    return [("label", box) for box in boxes[:split]] + [("xlabel", box) for box in boxes[split:]]


def label_box(edge: dict, *, expand: float = 0.0) -> Box | None:
    boxes = label_boxes_for(edge, expand=expand)
    return boxes[0] if boxes else None


def is_invisible(obj: dict) -> bool:
    return "invis" in str(obj.get("style", "")).replace(" ", "").split(",")


def node_boxes(data: dict) -> dict[int, Box]:
    boxes: dict[int, Box] = {}
    for obj in data.get("objects", []):
        # Invisible nodes (the generated junctions) are never drawn, so nothing
        # can visually collide with them.
        if "pos" not in obj or is_invisible(obj):
            continue
        x, y = (float(v) for v in obj["pos"].split(",", 1))
        width = float(obj.get("width", 0.0)) * 72.0
        height = float(obj.get("height", 0.0)) * 72.0
        boxes[obj["_gvid"]] = Box(x - width / 2, y - height / 2, x + width / 2, y + height / 2)
    return boxes


def node_positions(data: dict) -> dict[int, tuple[float, float]]:
    positions: dict[int, tuple[float, float]] = {}
    for obj in data.get("objects", []):
        if "pos" in obj:
            positions[obj["_gvid"]] = tuple(float(v) for v in obj["pos"].split(",", 1))
    return positions


def edge_kind(edge: dict, positions: dict[int, tuple[float, float]], rankdir: str) -> str:
    if edge.get("dir") == "back":
        return "reversed"
    tail = positions.get(edge["tail"])
    head = positions.get(edge["head"])
    if tail is None or head is None:
        return "forward"
    tx, ty = tail
    hx, hy = head
    if rankdir in {"LR", "RL"}:
        delta = hx - tx
        return "reversed" if delta < -1e-6 else "forward"
    delta = hy - ty
    return "reversed" if delta > 1e-6 else "forward"


def edge_name(
    tail: str, head: str, label: str, *, direction: str = "forward", label_kind: str = "label"
) -> str:
    # Follow logical arrows, not serialization order. Bidirectional/unarrowed
    # edges have no preferred orientation, so use their canonical endpoint order.
    if direction == "back" or (direction in {"both", "none"} and head < tail):
        tail, head = head, tail
        label_kind = {"headlabel": "taillabel", "taillabel": "headlabel"}.get(label_kind, label_kind)
    return json.dumps((tail, head, label_kind, label), ensure_ascii=False)


def metrics(dot_bytes: bytes) -> Metrics:
    data = dot_json(dot_bytes)
    names = {obj["_gvid"]: obj["name"] for obj in data.get("objects", [])}
    nodes = node_boxes(data)
    positions = node_positions(data)
    rankdir = (data.get("rankdir") or "TB").upper()
    rank_axis = "x" if rankdir in {"LR", "RL"} else "y"
    rows: list[EdgeRow] = []
    labels: list[tuple[int, str, Box]] = []
    label_boxes: list[LabelBox] = []
    splines: list[tuple[int, list[list[tuple[float, float]]]]] = []

    for index, edge in enumerate(data.get("edges", [])):
        pts = edge_points(edge.get("pos", ""))
        if not pts:
            continue
        mean_x = statistics.fmean(x for x, _ in pts)
        kind = edge_kind(edge, positions, rankdir)
        tail_name = names[edge["tail"]]
        head_name = names[edge["head"]]
        label = edge.get("label", "")
        rows.append(EdgeRow(index, tail_name, head_name, label, kind, mean_x))
        splines.append((index, edge_polylines(edge.get("pos", ""))))
        drawn = ldraw_label_boxes(edge, expand=PAD) + [
            (label_kind, box)
            for label_kind, draw_key in ENDPOINT_LABEL_DRAW_KEYS
            for box in label_boxes_for(edge, draw_key, expand=PAD)
        ]
        for label_kind, box in drawn:
            label_text = edge.get(label_kind, "")
            name = edge_name(
                tail_name, head_name, label_text,
                direction=edge.get("dir", "forward"), label_kind=label_kind,
            )
            labels.append((index, name, box))
            label_boxes.append(LabelBox(name, label_text, box))

    forward_x = [row.mean_x for row in rows if row.kind == "forward"]
    median = statistics.median(forward_x) if forward_x else None
    left = sum(1 for row in rows if row.kind == "reversed" and median is not None and row.mean_x <= median)

    crossed_labels = set()
    for label_index, label_name, box in labels:
        for spline_index, polylines in splines:
            if spline_index == label_index:
                continue
            if any(
                segment_hits_box(a, b, box) for line in polylines for a, b in zip(line, line[1:], strict=False)
            ):
                crossed_labels.add((label_index, label_name))
                break

    label_label_pairs: list[tuple[str, str]] = []
    for i, (a_index, a_name, a) in enumerate(labels):
        for b_index, b_name, b in labels[i + 1 :]:
            # Lines of one physical label touch by construction; distinct labels
            # must still collide even when their logical identities are equal.
            if (a_index, a_name) != (b_index, b_name) and boxes_overlap(a, b):
                label_label_pairs.append(tuple(sorted((a_name, b_name))))

    label_node = 0
    for _, _, label in labels:
        for node in nodes.values():
            label_node += int(boxes_overlap(label, node))

    return Metrics(
        rows,
        rank_axis,
        label_boxes,
        label_label_pairs,
        median,
        left,
        len(crossed_labels),
        len(label_label_pairs),
        label_node,
        dot_crossings(dot_bytes),
    )


def print_metrics(name: str, side: str, m: Metrics) -> None:
    median = "n/a" if m.median_forward_x is None else f"{m.median_forward_x:.2f}"
    print(f"{name}\t{side}\trank_axis={m.rank_axis}\tmedian_forward_x={median}\treversed_left_of_median={m.reversed_left_of_median}\tlabel_crossed={m.label_crossed}\tlabel_label={m.label_label_overlaps}\tlabel_node={m.label_node_overlaps}\tcrossings={m.crossings}")
    for item in m.label_boxes:
        print(f"{name}\t{side}\tlabel_box\t{item.edge_name}\tpad={PAD:.1f}\tbox={box_text(item.box)}")
    if m.label_label_pairs:
        for a, b in m.label_label_pairs:
            print(f"{name}\t{side}\tlabel_overlap\t{a}\t{b}")
    else:
        print(f"{name}\t{side}\tlabel_overlap_pairs=empty")
    for row in m.edges:
        label = f"\tlabel={row.label}" if row.label else ""
        print(f"{name}\t{side}\tedge\t#{row.index}\t{row.tail}->{row.head}\t{row.kind}\tmean_x={row.mean_x:.2f}{label}")


def refusal_sample() -> tuple[int, int, int]:
    candidates = sorted(
        set(
            list((ROOT / "contrib" / "junction-prototype" / "fixtures" / "original").glob("*.gv"))
            + list((ROOT / "graphs" / "directed").glob("*.gv"))
            + list((ROOT / "tests").glob("*.dot"))
        )
    )[:40]
    matched = 0
    refused = 0
    for path in candidates:
        proc = junctionize(path, skewer=True)
        if proc.stderr.startswith(b"refused:"):
            refused += 1
            if proc.stdout == path.read_bytes():
                matched += 1
    return matched, refused, len(candidates)


def verify_dot_parse_gate() -> tuple[int, int, list[str], bool]:
    failures: list[str] = []
    checked = 0
    parsed = 0
    grammar_passed = False
    for path in graph_corpus():
        checked += 1
        transformed = junctionize(path, skewer=True)
        rel = path.relative_to(ROOT)
        if transformed.returncode != 0:
            failures.append(f"{rel}: junctionize failed: {transformed.stderr.decode('utf-8', 'replace')}")
            continue
        parsed_graph = dot_parse(transformed.stdout)
        if parsed_graph.returncode != 0:
            failures.append(f"{rel}: dot parse failed: {parsed_graph.stderr.decode('utf-8', 'replace')}")
            continue
        parsed += 1
        if path == GRAMMAR:
            grammar_passed = True
    return checked, parsed, failures, grammar_passed


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="junction-baseline-") as tmp:
        base_tool = baseline_tool(Path(tmp))
        checked, parsed, parse_failures, grammar_passed = verify_dot_parse_gate()
        print(f"dot_parse_gate\tchecked={checked}\tparsed={parsed}")
        print(f"dot_parse_gate\tgraphs/directed/grammar.gv={'pass' if grammar_passed else 'missing-or-fail'}")
        failures.extend(parse_failures)
        for fixture in FIXTURES:
            before = junctionize_with(base_tool, fixture)
            after = junctionize(fixture, skewer=True)
            if before.returncode != 0 or after.returncode != 0:
                failures.append(f"{fixture}: junctionize failed")
                continue
            before_m = metrics(before.stdout)
            after_m = metrics(after.stdout)
            name = fixture.name
            print_metrics(name, BASELINE, before_m)
            print_metrics(name, "new-head", after_m)
            for field in ("crossings", "label_crossed", "label_label_overlaps", "label_node_overlaps"):
                if getattr(after_m, field) > getattr(before_m, field):
                    failures.append(f"{name}: {field} regressed {getattr(before_m, field)} -> {getattr(after_m, field)}")
            new_pairs = set(after_m.label_label_pairs) - set(before_m.label_label_pairs)
            if new_pairs:
                failures.append(f"{name}: new label-label overlap pairs {sorted(new_pairs)}")
            if fixture == DRBD:
                median = after_m.median_forward_x
                if median is None:
                    failures.append("DRBD: no forward median")
                elif any(row.kind == "reversed" and row.mean_x <= median for row in after_m.edges):
                    failures.append("DRBD: a reversed edge is not right of the median forward edge")
                by_label = {row.label: row.mean_x for row in after_m.edges if row.label}
                if by_label.get("sending notify to peer", -math.inf) <= by_label.get("resync completed", math.inf):
                    failures.append("DRBD: sending notify to peer is not right of resync completed")
                if by_label.get("sending notify to peer", -math.inf) <= by_label.get("ioctl_replicate", math.inf):
                    failures.append("DRBD: sending notify to peer is not right of ioctl_replicate")
                if by_label.get("ioctl_replicate", -math.inf) <= by_label.get("resync completed", math.inf):
                    failures.append("DRBD: ioctl_replicate is not right of resync completed")

        matched, refused, sample = refusal_sample()
    print(f"refusal_byte_identity_vs_input\tmatched={matched}\trefused_total={refused}\tsample_total={sample}")
    if matched != refused:
        failures.append(f"refusal byte identity failed: {matched}/{refused}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(failure)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
