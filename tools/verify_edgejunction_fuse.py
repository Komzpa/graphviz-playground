#!/usr/bin/env python3
"""Verify edgejunction fan arms fuse through one ranked junction."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


BASE_SHA = "af816a590db24bb86d37edc96efc6928b294492e"
PLACEMENT_BASE_SHA = "11de5b7e6b82750cf1eab029df40cbc127eebd62"
GROUP_ATTRS = (
    "label",
    "color",
    "style",
    "penwidth",
    "fontname",
    "fontsize",
    "fontcolor",
    "dir",
    "arrowhead",
    "arrowtail",
    "arrowsize",
    "decorate",
    "labelaligned",
    "layer",
    "URL",
    "href",
    "edgeURL",
    "edgehref",
    "labelURL",
    "labelhref",
    "headURL",
    "headhref",
    "tailURL",
    "tailhref",
    "tooltip",
    "edgetooltip",
    "labeltooltip",
    "headtooltip",
    "tailtooltip",
    "target",
    "edgetarget",
    "labeltarget",
    "headtarget",
    "tailtarget",
)
# The DRBD state graph is not in-tree; point EDGEJUNCTION_DRBD at a local copy
# to include it. Without it the in-tree cases still run.
DRBD_ENV = "EDGEJUNCTION_DRBD"
DEFAULT_CASES = tuple(
    case
    for case in (
        ("siblings", "fanin", "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"),
        ("drbd", "both", os.environ.get(DRBD_ENV, "")),
    )
    if case[2]
)
GEOMETRY_CASES = tuple(
    case
    for case in (
        ("siblings", "fanin", "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"),
        ("drbd", "both", os.environ.get(DRBD_ENV, "")),
        ("dfa", "fanin", "graphs/directed/dfa.gv"),
        ("unix", "fanin", "graphs/directed/unix.gv"),
    )
    if case[2]
)
SIBLINGS_NODES = ("a", "b", "c", "e", "_edgejunction_0", "d")
DRBD_LABELS = (
    "ioctl_set_disk()",
    "receive_param()",
    "io completion error",
    "start resync",
)


@dataclass(frozen=True)
class Arm:
    tail: str
    head: str
    identity: tuple[tuple[str, str], ...]
    span: int | None
    junction: str | None


@dataclass(frozen=True)
class Box:
    left: float
    bottom: float
    right: float
    top: float


@dataclass(frozen=True)
class LabelBox:
    owner: str
    text: str
    box: Box


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        **kwargs,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed rc={proc.returncode}: {' '.join(cmd)}\n{proc.stderr}"
        )
    return proc


def dot_path(root: Path) -> str:
    built = root / "build" / "cmd" / "dot" / "dot_builtins"
    if built.exists():
        return str(built)
    dot = shutil.which("dot")
    if dot is None:
        raise RuntimeError("dot not found; build dot_builtins or put dot on PATH")
    return dot


def dot_json(dot: str, mode: str, path: Path, *, phase: bool = False) -> dict:
    edgejunction = "none" if mode == "off" else mode
    args = [dot, f"-Gedgejunction={edgejunction}"]
    if phase:
        args.append("-Gphase=1")
    args += ["-Tjson", str(path)]
    return json.loads(run(args).stdout)


def dot_verbose_crossings(dot: str, mode: str, path: Path) -> int:
    edgejunction = "none" if mode == "off" else mode
    proc = subprocess.run(
        [dot, f"-Gedgejunction={edgejunction}", "-v", "-Tsvg", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"dot -v failed rc={proc.returncode}: {path}\n{proc.stderr}"
        )
    matches = re.findall(r"mincross .*?: (\d+) crossings", proc.stderr)
    if not matches:
        raise RuntimeError(f"no mincross crossing count found for {path}")
    return int(matches[-1])


def names(layout: dict) -> dict[int, str]:
    return {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}


def ranks(layout: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for obj in layout.get("objects", []):
        if "rank" in obj and "name" in obj:
            try:
                out[obj["name"]] = int(obj["rank"])
            except ValueError:
                pass
    return out


def node_positions(layout: dict) -> dict[str, tuple[float, float]]:
    out = {}
    for obj in layout.get("objects", []):
        pos = obj.get("pos")
        if "name" not in obj or not pos:
            continue
        try:
            x, y = pos.split(",", 1)
            out[obj["name"]] = (float(x), float(y))
        except ValueError:
            pass
    return out


def print_siblings_positions(dot: str, path: Path) -> int:
    pos = node_positions(dot_json(dot, "fanin", path))
    print("siblings fixture node positions:")
    failures = 0
    for name in SIBLINGS_NODES:
        if name not in pos:
            print(f"  {name}: <missing>")
            failures += 1
            continue
        print(f"  {name}: x={pos[name][0]:.2f} y={pos[name][1]:.2f}")
    aligned = ("c", "e", "_edgejunction_0", "d")
    if all(name in pos for name in aligned):
        xs = [pos[name][0] for name in aligned]
        spread = max(xs) - min(xs)
        print(f"siblings fixture spine x spread: {spread:.2f}pt")
        if spread > 1.0:
            print(f"FAIL siblings fixture spine spread {spread:.2f}pt exceeds 1pt")
            failures += 1
    return failures


def identity(edge: dict) -> tuple[tuple[str, str], ...]:
    return tuple((key, str(edge.get(key, ""))) for key in GROUP_ATTRS)


def identity_text(ident: tuple[tuple[str, str], ...]) -> str:
    shown = [(key, value) for key, value in ident if value]
    return ",".join(f"{key}={value}" for key, value in shown) or "<default>"


def arrowheads(edge: dict, stream: str) -> int:
    return sum(1 for op in edge.get(stream, []) if op.get("op") in {"P", "p"})


def visible_head_arrow_count(layout: dict, anchor: str, ident: tuple[tuple[str, str], ...]) -> int:
    node_names = names(layout)
    total = 0
    for edge in layout.get("edges", []):
        if node_names[int(edge["head"])] == anchor and identity(edge) == ident:
            total += arrowheads(edge, "_hdraw_")
    return total


def visible_tail_arrow_count(layout: dict, anchor: str, ident: tuple[tuple[str, str], ...]) -> int:
    node_names = names(layout)
    total = 0
    for edge in layout.get("edges", []):
        if node_names[int(edge["tail"])] == anchor and identity(edge) == ident:
            total += arrowheads(edge, "_tdraw_")
    return total


def junction_map(layout: dict, kind: str, anchor: str) -> dict[tuple[str, str], str]:
    node_names = names(layout)
    junctions = {
        obj["name"]
        for obj in layout.get("objects", [])
        if obj.get("_edgejunction_node") == "true"
    }
    mapped: dict[tuple[str, str], str] = {}
    for edge in layout.get("edges", []):
        if edge.get("_edgejunction_internal") != "true":
            continue
        tail = node_names[int(edge["tail"])]
        head = node_names[int(edge["head"])]
        if kind == "fanin" and head in junctions:
            if any(
                e.get("_edgejunction_internal") == "true"
                and node_names[int(e["tail"])] == head
                and node_names[int(e["head"])] == anchor
                for e in layout.get("edges", [])
            ):
                mapped[(tail, anchor)] = head
        elif kind == "fanout" and tail in junctions:
            if any(
                e.get("_edgejunction_internal") == "true"
                and node_names[int(e["tail"])] == anchor
                and node_names[int(e["head"])] == tail
                for e in layout.get("edges", [])
            ):
                mapped[(anchor, head)] = tail
    return mapped


def check_fan_alignment(
    layout: dict, kind: str, anchor: str, arms: list[Arm]
) -> int:
    pos = node_positions(layout)
    failures = 0
    for junction in sorted({arm.junction for arm in arms if arm.junction}):
        if junction not in pos or anchor not in pos:
            print(
                f"    fused_fan_alignment junction={junction} "
                f"shared_endpoint={anchor} junction_x=<missing> "
                f"shared_endpoint_x=<missing> dx=<missing>"
            )
            failures += 1
            continue
        jx = pos[junction][0]
        sx = pos[anchor][0]
        dx = abs(jx - sx)
        print(
            f"    fused_fan_alignment junction={junction} "
            f"shared_endpoint={anchor} junction_x={jx:.2f} "
            f"shared_endpoint_x={sx:.2f} dx={dx:.2f}pt"
        )
        if dx > 1.0:
            print(
                f"FAIL fan={kind} anchor={anchor}: junction {junction} "
                f"and shared endpoint differ by {dx:.2f}pt"
            )
            failures += 1
    return failures


def grouped_after_arms(dot: str, path: Path, mode: str, kind: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[Arm]]:
    after = dot_json(dot, mode, path)
    phase = dot_json(dot, mode, path, phase=True)
    rank = ranks(phase)
    node_names = names(after)
    groups: dict[tuple[str, tuple[tuple[str, str], ...]], list[Arm]] = defaultdict(list)
    for edge in after.get("edges", []):
        if edge.get("_edgejunction_original") != "true":
            continue
        tail = node_names[int(edge["tail"])]
        head = node_names[int(edge["head"])]
        anchor = head if kind == "fanin" else tail
        jmap = junction_map(after, kind, anchor)
        span = None
        if tail in rank and head in rank:
            span = abs(rank[head] - rank[tail])
        groups[(anchor, identity(edge))].append(
            Arm(tail=tail, head=head, identity=identity(edge), span=span, junction=jmap.get((tail, head)))
        )
    return {key: arms for key, arms in groups.items() if len(arms) >= 2}


def verify_fans(dot: str, case_name: str, mode: str, path: Path) -> int:
    before = dot_json(dot, "off", path)
    after = dot_json(dot, mode, path)
    failures = 0
    kinds = ("fanin", "fanout") if mode == "both" else (mode,)
    print(f"fixture={case_name} path={path} mode={mode}")
    for kind in kinds:
        if kind not in {"fanin", "fanout"}:
            continue
        groups = grouped_after_arms(dot, path, mode, kind)
        for (anchor, ident), arms in sorted(groups.items(), key=lambda item: (item[0][0], identity_text(item[0][1]))):
            before_count = (
                visible_head_arrow_count(before, anchor, ident)
                if kind == "fanin"
                else visible_tail_arrow_count(before, anchor, ident)
            )
            after_count = (
                visible_head_arrow_count(after, anchor, ident)
                if kind == "fanin"
                else visible_tail_arrow_count(after, anchor, ident)
            )
            junctions = sorted({arm.junction or "<none>" for arm in arms})
            print(
                f"  fan={kind} anchor={anchor} identity={identity_text(ident)} "
                f"arms={len(arms)} before_entering_arrowheads={before_count} "
                f"after_entering_arrowheads={after_count} junctions={','.join(junctions)}"
            )
            for arm in arms:
                print(
                    f"    arm={arm.tail}->{arm.head} rank_span={arm.span} "
                    f"junction={arm.junction or '<none>'} identity={identity_text(arm.identity)}"
                )
            failures += check_fan_alignment(after, kind, anchor, arms)
            if kind == "fanin" and after_count > 1:
                print(
                    f"FAIL fan={kind} anchor={anchor}: shared identity still enters anchor {after_count} times"
                )
                failures += 1
    return failures


def siblings_total_arrowheads(dot: str, path: Path) -> int:
    layout = dot_json(dot, "fanin", path)
    node_names = names(layout)
    total = 0
    by_color = Counter()
    for edge in layout.get("edges", []):
        if node_names[int(edge["head"])] != "d":
            continue
        count = arrowheads(edge, "_hdraw_")
        total += count
        color = edge.get("color", "black")
        by_color[color] += count
    print(
        "siblings fixture arrowhead count into d: "
        f"total={total} black={by_color['black']} blue={by_color['blue']} red={by_color['red']}"
    )
    return 0 if total == 3 and by_color["black"] == 1 and by_color["blue"] == 1 and by_color["red"] == 1 else 1


def drbd_label_counts(dot: str, path: Path) -> int:
    layout = dot_json(dot, "both", path)
    counts = Counter(
        op.get("text", "")
        for edge in layout.get("edges", [])
        for op in edge.get("_ldraw_", [])
        if op.get("op") == "T"
    )
    failures = 0
    for label in DRBD_LABELS:
        actual = counts[label]
        print(f"DRBD label count {label!r}: {actual}")
        if actual != 1:
            failures += 1
    return failures


def text_boxes(owner: str, draw_ops: list[dict]) -> list[LabelBox]:
    boxes = []
    font_size = 14.0
    for op in draw_ops:
        if op.get("op") == "F":
            font_size = float(op.get("size", font_size))
        if op.get("op") != "T":
            continue
        x, y = (float(v) for v in op["pt"])
        width = float(op.get("width", 0.0))
        height = font_size
        boxes.append(
            LabelBox(
                owner=owner,
                text=op.get("text", ""),
                box=Box(
                    left=x - width / 2.0,
                    bottom=y - height / 2.0,
                    right=x + width / 2.0,
                    top=y + height / 2.0,
                ),
            )
        )
    return boxes


def labels(layout: dict) -> list[LabelBox]:
    out = []
    node_names = names(layout)
    for i, edge in enumerate(layout.get("edges", [])):
        tail = node_names.get(int(edge["tail"]), str(edge["tail"]))
        head = node_names.get(int(edge["head"]), str(edge["head"]))
        out.extend(text_boxes(f"edge:{i}:{tail}->{head}", edge.get("_ldraw_", [])))
    return out


def node_boxes(layout: dict) -> list[tuple[str, Box]]:
    out = []
    for obj in layout.get("objects", []):
        pos = obj.get("pos")
        if "name" not in obj or not pos:
            continue
        try:
            x, y = (float(v) for v in pos.split(",", 1))
            width = float(obj.get("width", 0.0)) * 72.0
            height = float(obj.get("height", 0.0)) * 72.0
        except ValueError:
            continue
        out.append(
            (
                f"node:{obj['name']}",
                Box(x - width / 2.0, y - height / 2.0, x + width / 2.0, y + height / 2.0),
            )
        )
    return out


def expand(box: Box, margin: float) -> Box:
    return Box(box.left - margin, box.bottom - margin, box.right + margin, box.top + margin)


def overlaps(a: Box, b: Box) -> bool:
    return a.left <= b.right and b.left <= a.right and a.bottom <= b.top and b.bottom <= a.top


def point_in_box(x: float, y: float, box: Box) -> bool:
    return box.left <= x <= box.right and box.bottom <= y <= box.top


def ccw(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)


def segment_enters_box(a: tuple[float, float], b: tuple[float, float], box: Box) -> bool:
    if point_in_box(a[0], a[1], box) or point_in_box(b[0], b[1], box):
        return True
    corners = (
        (box.left, box.bottom),
        (box.right, box.bottom),
        (box.right, box.top),
        (box.left, box.top),
    )
    sides = zip(corners, corners[1:] + corners[:1])
    return any(segments_intersect(a, b, c, d) for c, d in sides)


def cubic_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    mt = 1.0 - t
    return (
        mt**3 * p0[0]
        + 3.0 * mt**2 * t * p1[0]
        + 3.0 * mt * t**2 * p2[0]
        + t**3 * p3[0],
        mt**3 * p0[1]
        + 3.0 * mt**2 * t * p1[1]
        + 3.0 * mt * t**2 * p2[1]
        + t**3 * p3[1],
    )


def edge_polylines(edge: dict) -> list[list[tuple[float, float]]]:
    polylines = []
    for piece in edge.get("pos", "").split(";"):
        controls = [
            (float(x), float(y))
            for x, y in re.findall(
                r"(?:[es],)?(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", piece
            )
        ]
        if len(controls) >= 4 and (len(controls) - 1) % 3 == 0:
            sampled = [controls[0]]
            for i in range(0, len(controls) - 1, 3):
                p0, p1, p2, p3 = controls[i : i + 4]
                sampled.extend(
                    cubic_point(p0, p1, p2, p3, step / 10.0)
                    for step in range(1, 11)
                )
            polylines.append(sampled)
        elif controls:
            polylines.append(controls)
    return polylines


def label_metrics(layout: dict) -> tuple[int, int, int]:
    all_labels = labels(layout)
    edge_paths = [
        (f"edge:{i}", edge_polylines(edge)) for i, edge in enumerate(layout.get("edges", []))
    ]
    crossed = 0
    for label in all_labels:
        box = expand(label.box, 3.0)
        hit = False
        for edge_owner, polylines in edge_paths:
            if label.owner.startswith(edge_owner + ":"):
                continue
            if any(
                segment_enters_box(a, b, box)
                for points in polylines
                for a, b in zip(points, points[1:])
            ):
                hit = True
                break
        if hit:
            crossed += 1

    label_overlaps = 0
    for i, left in enumerate(all_labels):
        for right in all_labels[i + 1 :]:
            if overlaps(left.box, right.box):
                label_overlaps += 1

    label_node_overlaps = 0
    nodes = node_boxes(layout)
    for label in all_labels:
        for owner, box in nodes:
            if label.owner == owner:
                continue
            if overlaps(label.box, box):
                label_node_overlaps += 1
    return crossed, label_overlaps, label_node_overlaps


def verify_geometry_metrics(dot: str, root: Path) -> int:
    before_dot = dot_built_at(
        root, PLACEMENT_BASE_SHA, "graphviz-edgejunction-placement-baseline"
    )
    failures = 0
    for case_name, mode, rel in GEOMETRY_CASES:
        path = Path(rel)
        if not path.is_absolute():
            path = root / path
        if not path.exists():
            print(f"FAIL missing geometry fixture {path}")
            failures += 1
            continue
        before_crossings = dot_verbose_crossings(before_dot, mode, path)
        after_crossings = dot_verbose_crossings(dot, mode, path)
        print(
            f"crossings fixture={case_name} mode={mode}: "
            f"before={before_crossings} before_sha={PLACEMENT_BASE_SHA} "
            f"after={after_crossings}"
        )
        if after_crossings > before_crossings:
            print(
                f"FAIL crossings fixture={case_name}: after {after_crossings} "
                f"exceeds before {before_crossings}"
            )
            failures += 1

        before_labels = label_metrics(dot_json(before_dot, mode, path))
        after_labels = label_metrics(dot_json(dot, mode, path))
        metric_names = (
            "foreign_edge_label_crossings",
            "label_label_overlaps",
            "label_node_overlaps",
        )
        for metric_name, before_value, after_value in zip(
            metric_names, before_labels, after_labels
        ):
            print(
                f"label metric fixture={case_name} mode={mode} "
                f"{metric_name}: before={before_value} after={after_value}"
            )
            if after_value > before_value:
                print(
                    f"FAIL label metric fixture={case_name} {metric_name}: "
                    f"after {after_value} exceeds before {before_value}"
                )
                failures += 1
    return failures


def dot_built_at(root: Path, sha: str, name: str) -> str:
    base = Path(tempfile.gettempdir()) / f"{name}-{sha[:9]}"
    if not base.exists():
        run(["git", "worktree", "add", "--detach", str(base), sha], cwd=root)
    head = run(["git", "rev-parse", "HEAD"], cwd=base).stdout.strip()
    if head != sha:
        raise RuntimeError(f"baseline worktree {base} is at {head}, expected {sha}")
    exe = base / "build" / "cmd" / "dot" / "dot_builtins"
    if not exe.exists():
        run(["cmake", "-S", str(base), "-B", str(base / "build")], cwd=base)
    run(["cmake", "--build", str(base / "build"), "--target", "dot_builtins"], cwd=base)
    return str(exe)


def baseline_dot(root: Path) -> str:
    return dot_built_at(root, BASE_SHA, "graphviz-edgejunction-baseline")


def corpus_paths(root: Path) -> list[str]:
    paths = run(
        ["git", "ls-tree", "-r", "--name-only", BASE_SHA, "--", "graphs", "tests"],
        cwd=root,
    ).stdout.splitlines()
    picked: list[str] = []
    for rel in paths:
        if not rel.endswith((".dot", ".gv")):
            continue
        current = root / rel
        if not current.exists():
            continue
        text = current.read_text(errors="ignore")
        if "edgejunction" in text:
            continue
        picked.append(rel)
        if len(picked) == 40:
            break
    return picked


def xdot(dot: str, path: Path) -> bytes:
    proc = subprocess.run(
        [dot, "-Txdot", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"dot -Txdot failed for {path}: {proc.stderr.decode(errors='replace')}")
    return proc.stdout


def git_blob(root: Path, rel: str) -> bytes:
    proc = subprocess.run(
        ["git", "show", f"{BASE_SHA}:{rel}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git show failed for {rel}: {proc.stderr.decode(errors='replace')}")
    return proc.stdout


def attribute_absent_identity(root: Path, current_dot: str) -> int:
    base_dot = baseline_dot(root)
    matched = 0
    paths = corpus_paths(root)
    for rel in paths:
        base_path = Path(tempfile.gettempdir()) / f"edgejunction-base-{os.getpid()}-{len(rel)}.dot"
        base_path.write_bytes(git_blob(root, rel))
        try:
            if xdot(base_dot, base_path) == xdot(current_dot, root / rel):
                matched += 1
            else:
                print(f"attribute-absent mismatch: {rel}")
        finally:
            base_path.unlink(missing_ok=True)
    print(f"attribute-absent byte-identity sample: matched={matched} total={len(paths)} base={BASE_SHA}")
    return 0 if matched == len(paths) == 40 else 1


def main() -> int:
    root = repo_root()
    current_dot = dot_path(root)
    print(f"commit sha: {run(['git', 'rev-parse', 'HEAD'], cwd=root).stdout.strip()}")
    print(f"dot: {current_dot}")
    failures = 0
    for case_name, mode, rel in DEFAULT_CASES:
        path = Path(rel)
        if not path.is_absolute():
            path = root / path
        if not path.exists():
            print(f"FAIL missing fixture {path}")
            failures += 1
            continue
        failures += verify_fans(current_dot, case_name, mode, path)
        if case_name == "siblings":
            failures += print_siblings_positions(current_dot, path)
            failures += siblings_total_arrowheads(current_dot, path)
        if case_name == "drbd":
            failures += drbd_label_counts(current_dot, path)
    failures += verify_geometry_metrics(current_dot, root)
    failures += attribute_absent_identity(root, current_dot)
    if failures:
        print(f"FAIL verify_edgejunction_fuse: failures={failures}")
        return 1
    print("OK verify_edgejunction_fuse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
