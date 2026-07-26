#!/usr/bin/env python3
"""Verify edgejunction fan arms fuse through one ranked junction."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


BASE_SHA = "af816a590db24bb86d37edc96efc6928b294492e"
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
DEFAULT_CASES = (
    ("siblings", "fanin", "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"),
    ("drbd", "both", "/home/kom/tmp/graphviz-pr1-cleanup-20260719/lane2bU-anchor-fixtures-frozen/0604-64000479e879bbdc.dot"),
)
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


def baseline_dot(root: Path) -> str:
    base = Path(tempfile.gettempdir()) / f"graphviz-edgejunction-baseline-{BASE_SHA[:9]}"
    if not base.exists():
        run(["git", "worktree", "add", "--detach", str(base), BASE_SHA], cwd=root)
    head = run(["git", "rev-parse", "HEAD"], cwd=base).stdout.strip()
    if head != BASE_SHA:
        raise RuntimeError(f"baseline worktree {base} is at {head}, expected {BASE_SHA}")
    exe = base / "build" / "cmd" / "dot" / "dot_builtins"
    if not exe.exists():
        run(["cmake", "-S", str(base), "-B", str(base / "build")], cwd=base)
        run(["cmake", "--build", str(base / "build"), "--target", "dot_builtins"], cwd=base)
    return str(exe)


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
            failures += siblings_total_arrowheads(current_dot, path)
        if case_name == "drbd":
            failures += drbd_label_counts(current_dot, path)
    failures += attribute_absent_identity(root, current_dot)
    if failures:
        print(f"FAIL verify_edgejunction_fuse: failures={failures}")
        return 1
    print("OK verify_edgejunction_fuse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
