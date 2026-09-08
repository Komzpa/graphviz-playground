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
BASELINE = "b835b3ffa"
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
    proc = run(["git", "show", f"{BASELINE}:contrib/junction-prototype/junctionize.py"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    tool = tmpdir / "junctionize-baseline.py"
    tool.write_bytes(proc.stdout)
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


def label_box(edge: dict, *, expand: float = 0.0) -> Box | None:
    for op in edge.get("_ldraw_", []):
        if op.get("op") == "T":
            x, y = op["pt"]
            width = float(op["width"])
            height = 14.0
            return Box(
                x - width / 2 - expand,
                y - height / 2 - expand,
                x + width / 2 + expand,
                y + height / 2 + expand,
            )
    return None


def node_boxes(data: dict) -> dict[int, Box]:
    boxes: dict[int, Box] = {}
    for obj in data.get("objects", []):
        if "pos" not in obj:
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


def edge_name(index: int, tail: str, head: str, label: str) -> str:
    suffix = f" label={label}" if label else ""
    return f"#{index} {tail}->{head}{suffix}"


def metrics(dot_bytes: bytes) -> Metrics:
    data = dot_json(dot_bytes)
    names = {obj["_gvid"]: obj["name"] for obj in data.get("objects", [])}
    nodes = node_boxes(data)
    positions = node_positions(data)
    rankdir = (data.get("rankdir") or "TB").upper()
    rank_axis = "x" if rankdir in {"LR", "RL"} else "y"
    rows: list[EdgeRow] = []
    labels: list[tuple[int, str, Box]] = []
    expanded_labels: list[tuple[int, Box]] = []
    label_boxes: list[LabelBox] = []
    splines: list[tuple[int, list[tuple[float, float]]]] = []

    for index, edge in enumerate(data.get("edges", [])):
        pts = edge_points(edge.get("pos", ""))
        if not pts:
            continue
        mean_x = statistics.fmean(x for x, _ in pts)
        kind = edge_kind(edge, positions, rankdir)
        tail_name = names[edge["tail"]]
        head_name = names[edge["head"]]
        label = edge.get("label", "")
        name = edge_name(index, tail_name, head_name, label)
        rows.append(EdgeRow(index, tail_name, head_name, label, kind, mean_x))
        splines.append((index, pts))
        box = label_box(edge)
        if box is not None and label:
            expanded = label_box(edge, expand=PAD)
            assert expanded is not None
            labels.append((index, name, expanded))
            expanded_labels.append((index, expanded))
            label_boxes.append(LabelBox(name, label, expanded))

    forward_x = [row.mean_x for row in rows if row.kind == "forward"]
    median = statistics.median(forward_x) if forward_x else None
    left = sum(1 for row in rows if row.kind == "reversed" and median is not None and row.mean_x <= median)

    crossed_labels = set()
    for label_index, box in expanded_labels:
        for spline_index, pts in splines:
            if spline_index == label_index:
                continue
            if any(segment_hits_box(a, b, box) for a, b in zip(pts, pts[1:], strict=False)):
                crossed_labels.add(label_index)
                break

    label_label_pairs: list[tuple[str, str]] = []
    for i, (_, a_name, a) in enumerate(labels):
        for _, b_name, b in labels[i + 1 :]:
            if boxes_overlap(a, b):
                label_label_pairs.append((a_name, b_name))

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


def refusal_sample(base_tool: Path) -> tuple[int, int, int]:
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
            baseline = junctionize_with(base_tool, path)
            if baseline.returncode == 0 and proc.stdout == baseline.stdout:
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

        matched, refused, sample = refusal_sample(base_tool)
    print(f"refusal_byte_identity_vs_{BASELINE}\tmatched={matched}\trefused_total={refused}\tsample_total={sample}")
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
