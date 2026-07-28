#!/usr/bin/env python3
"""Score open-space spline kinks in the rank-aware junction test set."""

from __future__ import annotations

import json
import math
import os
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BASELINE_SHA = "3cade507e47d2c82832fb5b43d27c5b93fdb84e3"
PROTOTYPE_SHA = "8dd559097"
ANGLE_DEGREES = 35.0
MIN_JUNCTION_RADIUS = 6.0
JUNCTION_MARGIN = 2.0
JOIN_EPSILON = 0.5
MEMORY_KB = 2 * 1024 * 1024
TIMEOUT = 60


@dataclass(frozen=True)
class GraphCase:
    label: str
    path: Path
    prototype: bool = True


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Support:
    distance: float
    kind: str


@dataclass(frozen=True)
class Turn:
    edge_index: int
    tail: str
    head: str
    point: Point
    angle: float
    support: Support
    classification: str


@dataclass(frozen=True)
class Score:
    status: str
    turns: list[Turn]
    error: str = ""

    @property
    def kinks(self) -> int:
        return sum(turn.classification == "kink" for turn in self.turns)

    @property
    def junctions(self) -> int:
        return sum(turn.classification == "junction" for turn in self.turns)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def private_corpus_root() -> Path:
    override = os.environ.get("GRAPHVIZ_KINK_CORPUS_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "tmp" / "graphviz-pr1-cleanup-20260719" / "gallery3" / "sources"


def graph_cases(root: Path) -> list[GraphCase]:
    corpus = private_corpus_root()
    return [
        GraphCase("corpus:0006-a83c54bcf5cbaa8b", corpus / "corpus" / "0006-a83c54bcf5cbaa8b.dot"),
        GraphCase("spicy:0604-64000479e879bbdc", corpus / "spicy" / "0604-64000479e879bbdc.dot"),
        GraphCase("awilliams.gv", root / "graphs" / "directed" / "awilliams.gv"),
        GraphCase("tree:abstract", root / "graphs" / "directed" / "abstract.gv"),
        GraphCase("tree:japanese", root / "graphs" / "directed" / "japanese.gv"),
    ]


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(
    args: list[str],
    cwd: Path,
    *,
    stdout=None,
    text: bool = False,
    memory: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=True,
        timeout=TIMEOUT,
        preexec_fn=cap_memory if memory else None,
    )


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root, memory=False)
    return dot


def configured_dot(root: Path, sha: str, name: str) -> Path:
    override = os.environ.get(name)
    if override:
        return Path(override)
    worktree = root.parent / f"{root.name}-{sha[:9]}"
    if not worktree.exists():
        run(["git", "worktree", "add", "--detach", str(worktree), sha], root, memory=False)
    dot = worktree / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        run(
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
            memory=False,
        )
        run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree, memory=False)
    return dot


def baseline_dot(root: Path) -> Path:
    return configured_dot(root, BASELINE_SHA, "NO_KINKS_BASELINE_DOT")


def upstream_dot(root: Path) -> Path:
    override = os.environ.get("NO_KINKS_UPSTREAM_DOT")
    if override:
        return Path(override)
    sibling = root.parent.parent / "graphviz-upstream-20260728" / "build" / "cmd" / "dot" / "dot_builtins"
    return sibling if sibling.exists() else current_dot(root)


def render_json(dot: Path, graph: Path, root: Path, *extra: str) -> dict:
    proc = run([str(dot), *extra, "-Tjson", str(graph)], root)
    return json.loads(proc.stdout)


def prototype_script(root: Path, outdir: Path) -> Path:
    script = outdir / "junctionize.py"
    if not script.exists():
        text = run(
            ["git", "show", f"{PROTOTYPE_SHA}:contrib/junction-prototype/junctionize.py"],
            root,
            text=True,
            memory=False,
        ).stdout
        script.write_text(text)
        script.chmod(0o755)
    return script


def prototype_graph(root: Path, dot: Path, graph: Path, outdir: Path, label: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "-" for ch in label)
    out = outdir / f"{safe}.prototype.gv"
    script = prototype_script(root, outdir)
    with out.open("wb") as stdout:
        run([str(script), "--dot", str(dot), str(graph)], root, stdout=stdout)
    return out


def point(value: list[float]) -> Point:
    return Point(float(value[0]), float(value[1]))


def distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def turn_angle(incoming: Point, joint: Point, outgoing: Point) -> float:
    vin = (joint.x - incoming.x, joint.y - incoming.y)
    vout = (outgoing.x - joint.x, outgoing.y - joint.y)
    nin = math.hypot(*vin)
    nout = math.hypot(*vout)
    if nin < 1e-6 or nout < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, (vin[0] * vout[0] + vin[1] * vout[1]) / (nin * nout)))
    return math.degrees(math.acos(cos))


def parse_pos(value: str) -> Point:
    x, y = value.split(",", 1)
    return Point(float(x), float(y))


def is_junction_node(node: dict) -> bool:
    name = node.get("name", "")
    return (
        name.startswith("__junction")
        or name.startswith("_concentrate_junction_")
        or node.get("_concentrate_junction_node") == "true"
    )


def node_obstacles(layout: dict) -> list[tuple[str, Point, float, float, bool]]:
    out = []
    for obj in layout.get("objects", []):
        if "pos" not in obj:
            continue
        center = parse_pos(obj["pos"])
        rx = max(float(obj.get("width", 0.0)) * 36.0, 0.01)
        ry = max(float(obj.get("height", 0.0)) * 36.0, 0.01)
        out.append((obj.get("name", "<node>"), center, rx, ry, is_junction_node(obj)))
    return out


def node_distance(obstacle: tuple[str, Point, float, float, bool], p: Point) -> float:
    _name, center, rx, ry, _junction = obstacle
    dx = abs(p.x - center.x)
    dy = abs(p.y - center.y)
    if dx <= rx and dy <= ry:
        return 0.0
    return math.hypot(max(0.0, dx - rx), max(0.0, dy - ry))


def edge_endpoints(layout: dict) -> list[tuple[int, Point]]:
    endpoints = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        for op in edge.get("_draw_", []):
            if op.get("op") != "b":
                continue
            pts = [point(p) for p in op.get("points", [])]
            if pts:
                endpoints.append((edge_index, pts[0]))
                endpoints.append((edge_index, pts[-1]))
    return endpoints


def nearest_support(layout: dict, p: Point, own_edge_index: int) -> Support:
    best = Support(math.inf, "none")
    for obs in node_obstacles(layout):
        d = node_distance(obs, p)
        name, _center, _rx, _ry, junction = obs
        kind = f"{'junction' if junction else 'node'}:{name}"
        if d < best.distance:
            best = Support(d, kind)
    for edge_index, endpoint in edge_endpoints(layout):
        if edge_index == own_edge_index:
            continue
        d = distance(endpoint, p)
        if d < best.distance:
            best = Support(d, "edge-endpoint")
    return best


def raw_turns(layout: dict) -> list[tuple[int, str, str, Point, float, Support]]:
    names = {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}
    turns = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        tail = names[int(edge["tail"])]
        head = names[int(edge["head"])]
        pieces = []
        for op in edge.get("_draw_", []):
            if op.get("op") == "b":
                pts = [point(p) for p in op.get("points", [])]
                if len(pts) >= 4:
                    pieces.append(pts)
        for left, right in zip(pieces, pieces[1:]):
            if distance(left[-1], right[0]) > JOIN_EPSILON:
                continue
            angle = turn_angle(left[-2], left[-1], right[1])
            if angle <= ANGLE_DEGREES:
                continue
            joint = left[-1]
            turns.append((edge_index, tail, head, joint, angle, nearest_support(layout, joint, edge_index)))
    return turns


def calibrated_radius(prototype_layouts: list[dict]) -> float:
    junction_distances = []
    for layout in prototype_layouts:
        for _edge_index, _tail, _head, _point, _angle, support in raw_turns(layout):
            if support.kind.startswith("junction:"):
                junction_distances.append(support.distance)
    if not junction_distances:
        return MIN_JUNCTION_RADIUS
    return max(MIN_JUNCTION_RADIUS, max(junction_distances) + JUNCTION_MARGIN)


def junction_separation_proof(prototype_layouts: list[dict], radius: float) -> dict:
    nodes = 0
    supported_turns = 0
    unsupported_turns = 0
    nearest_unsupported = math.inf
    for layout in prototype_layouts:
        nodes += sum(1 for obj in layout.get("objects", []) if is_junction_node(obj))
        for _edge_index, _tail, _head, _point, _angle, support in raw_turns(layout):
            if support.kind.startswith("junction:") and support.distance <= radius:
                supported_turns += 1
            elif support.kind.startswith("junction:"):
                unsupported_turns += 1
                nearest_unsupported = min(nearest_unsupported, support.distance)
    return {
        "prototype_junction_nodes": nodes,
        "prototype_sharp_turns_supported_by_junctions": supported_turns,
        "prototype_sharp_turns_near_but_outside_radius": unsupported_turns,
        "nearest_outside_radius": None
        if math.isinf(nearest_unsupported)
        else round(nearest_unsupported, 2),
    }


def score(layout: dict, radius: float) -> list[Turn]:
    turns = []
    for edge_index, tail, head, joint, angle, support in raw_turns(layout):
        classification = "junction" if support.distance <= radius else "kink"
        turns.append(Turn(edge_index, tail, head, joint, angle, support, classification))
    return sorted(turns, key=lambda t: (-t.angle, t.tail, t.head, t.point.x, t.point.y))


def key(turn: Turn) -> tuple[str, str, float, float]:
    return (turn.tail, turn.head, round(turn.point.x, 1), round(turn.point.y, 1))


def render_score(dot: Path, graph: Path, root: Path, radius: float, *extra: str) -> Score:
    try:
        layout = render_json(dot, graph, root, *extra)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return Score("error", [], str(exc))
    return Score("ok", score(layout, radius))


def details(turns: list[Turn]) -> list[dict]:
    return [
        {
            "edge_index": turn.edge_index,
            "tail": turn.tail,
            "head": turn.head,
            "x": round(turn.point.x, 2),
            "y": round(turn.point.y, 2),
            "angle": round(turn.angle, 2),
            "nearest": round(turn.support.distance, 2),
            "support": turn.support.kind,
            "classification": turn.classification,
        }
        for turn in turns
    ]


def write_reports(outdir: Path, payload: dict) -> None:
    (outdir / "scores.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    lines = [
        "# No-kinks score",
        "",
        f"baseline: `{BASELINE_SHA}`",
        f"angle threshold: `{ANGLE_DEGREES:g}deg`",
        f"junction radius: `{payload['junction_radius']:.2f}pt`",
        "prototype junction separation: "
        f"`nodes={payload['junction_separation']['prototype_junction_nodes']}`, "
        f"`supported_turns={payload['junction_separation']['prototype_sharp_turns_supported_by_junctions']}`, "
        f"`outside_radius={payload['junction_separation']['prototype_sharp_turns_near_but_outside_radius']}`",
        "",
        "| graph | ours on | ours off | upstream | prototype |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["table"]:
        lines.append(
            f"| {row['graph']} | {row['ours_on']} | {row['ours_off']} | "
            f"{row['upstream']} | {row['prototype']} |"
        )
    lines.append("")
    lines.append("A cell is the number of clear-space kinks; errors are shown as text.")
    (outdir / "scores.md").write_text("\n".join(lines) + "\n")


def cell(score_obj: Score) -> str:
    return str(score_obj.kinks) if score_obj.status == "ok" else "error"


def main() -> int:
    root = repo_root()
    outdir = root / "build" / "no-kinks"
    outdir.mkdir(parents=True, exist_ok=True)

    missing = [case for case in graph_cases(root) if not case.path.exists()]
    if missing:
        for case in missing:
            print(f"FAIL missing graph case {case.label}")
        return 1

    current = current_dot(root)
    baseline = baseline_dot(root)
    upstream = upstream_dot(root)

    prototype_layouts = []
    prototype_graphs: dict[str, Path] = {}
    for case in graph_cases(root):
        try:
            rewritten = prototype_graph(root, upstream, case.path, outdir, case.label)
            prototype_graphs[case.label] = rewritten
            prototype_layouts.append(render_json(upstream, rewritten, root))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            print(f"prototype unavailable for {case.label}: {exc}")

    radius = calibrated_radius(prototype_layouts)
    separation = junction_separation_proof(prototype_layouts, radius)
    print(f"measure: turns>{ANGLE_DEGREES:g}deg; support radius={radius:.2f}pt from prototype junction calibration")
    print(
        "prototype junction separation: "
        f"nodes={separation['prototype_junction_nodes']} "
        f"supported_turns={separation['prototype_sharp_turns_supported_by_junctions']} "
        f"outside_radius={separation['prototype_sharp_turns_near_but_outside_radius']}"
    )

    table = []
    payload = {
        "baseline_sha": BASELINE_SHA,
        "prototype_sha": PROTOTYPE_SHA,
        "angle_degrees": ANGLE_DEGREES,
        "junction_radius": radius,
        "junction_separation": separation,
        "table": table,
        "details": {},
    }
    failures = 0
    for case in graph_cases(root):
        scores = {
            "baseline_on": render_score(baseline, case.path, root, radius, "-Gconcentrate=true"),
            "ours_on": render_score(current, case.path, root, radius, "-Gconcentrate=true"),
            "ours_off": render_score(current, case.path, root, radius, "-Gconcentrate=false"),
            "upstream": render_score(upstream, case.path, root, radius),
        }
        if case.label in prototype_graphs:
            scores["prototype"] = render_score(upstream, prototype_graphs[case.label], root, radius)
        else:
            scores["prototype"] = Score("error", [], "prototype rewrite failed")

        baseline_kinks = {key(turn) for turn in scores["baseline_on"].turns if turn.classification == "kink"}
        current_new = [
            turn
            for turn in scores["ours_on"].turns
            if turn.classification == "kink" and key(turn) not in baseline_kinks
        ]
        if scores["baseline_on"].status != "ok" or scores["ours_on"].status != "ok" or current_new:
            failures += 1

        row = {
            "graph": case.label,
            "ours_on": cell(scores["ours_on"]),
            "ours_off": cell(scores["ours_off"]),
            "upstream": cell(scores["upstream"]),
            "prototype": cell(scores["prototype"]),
        }
        table.append(row)
        payload["details"][case.label] = {
            name: {
                "status": score_obj.status,
                "error": score_obj.error,
                "turns": len(score_obj.turns),
                "junctions": score_obj.junctions,
                "kinks": score_obj.kinks,
                "details": details(score_obj.turns),
            }
            for name, score_obj in scores.items()
        }
        print(
            f"{case.label}: ours_on={row['ours_on']} ours_off={row['ours_off']} "
            f"upstream={row['upstream']} prototype={row['prototype']}"
        )
        if case.label.startswith("corpus:0006"):
            print(f"corpus:0006 explicit concentrate=false survivors: {row['ours_off']}")

    write_reports(outdir, payload)
    print(f"wrote {outdir / 'scores.md'}")
    if failures:
        print(f"FAIL verify_no_kinks: regression failures={failures}")
        return 1
    print("OK verify_no_kinks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
