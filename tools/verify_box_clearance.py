#!/usr/bin/env python3
"""Report concentrated splines that run too close to drawn node boxes."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_COMMIT = "73b8ef683bf78ae4f374f508291974daa8839651"
CLEARANCE = 0.5
GRAPH_FLAGS = ("-Gconcentrate=true",)
GRAPHS = (Path("graphs/directed/record2.gv"),)
RENDER_TIMEOUT_SECONDS = 20
SAMPLE_STEPS = 80
ULIMIT_V_KB = 2 * 1024 * 1024


@dataclass(frozen=True)
class Box:
    name: str
    left: float
    bottom: float
    right: float
    top: float


@dataclass(frozen=True)
class CubicPoint:
    edge_index: int
    cubic_index: int
    t: float
    x: float
    y: float


@dataclass(frozen=True)
class Violation:
    graph: Path
    edge_index: int
    cubic_index: int
    t: float
    box: str
    corner: str
    distance: float
    x: float
    y: float

    @property
    def key(self) -> tuple[str, int, int, str, str]:
        return (
            self.graph.as_posix(),
            self.edge_index,
            self.cubic_index,
            self.box,
            self.corner,
        )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def dot_path(root: Path) -> str:
    env_dot = os.environ.get("BOX_CLEARANCE_DOT")
    if env_dot:
        return env_dot
    built = root / "build" / "cmd" / "dot" / "dot_builtins"
    if built.exists():
        return str(built)
    dot = shutil.which("dot")
    if dot is None:
        raise SystemExit("dot not found; build dot_builtins or set BOX_CLEARANCE_DOT")
    return dot


def capped_dot_command(dot: str, output_format: str, graph: Path) -> list[str]:
    argv = [dot, "-Kdot", f"-T{output_format}", *GRAPH_FLAGS, str(graph)]
    return ["bash", "-lc", f"ulimit -v {ULIMIT_V_KB}; exec \"$@\"", "dot-ulimit", *argv]


def render_json(dot: str, graph: Path) -> dict[str, Any]:
    proc = subprocess.run(
        capped_dot_command(dot, "json", graph),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"dot failed rc={proc.returncode} for {graph}:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def render_baseline_json(dot: str, root: Path, graph: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        baseline_graph = Path(tmp) / graph.name
        content = subprocess.check_output(
            ["git", "show", f"{BASE_COMMIT}:{graph.as_posix()}"],
            cwd=root,
            text=True,
        )
        baseline_graph.write_text(content)
        return render_json(dot, baseline_graph)


def drawn_node_boxes(layout: dict[str, Any]) -> dict[int, Box]:
    boxes: dict[int, Box] = {}
    for obj in layout.get("objects", []):
        polygon = next(
            (op["points"] for op in obj.get("_draw_", []) if op.get("op") == "p"),
            None,
        )
        if polygon is None:
            continue
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        boxes[int(obj["_gvid"])] = Box(
            name=obj["name"],
            left=min(xs),
            bottom=min(ys),
            right=max(xs),
            top=max(ys),
        )
    return boxes


def cubic_point(points: list[tuple[float, float]], t: float) -> tuple[float, float]:
    u = 1.0 - t
    x = (
        u**3 * points[0][0]
        + 3.0 * u**2 * t * points[1][0]
        + 3.0 * u * t**2 * points[2][0]
        + t**3 * points[3][0]
    )
    y = (
        u**3 * points[0][1]
        + 3.0 * u**2 * t * points[1][1]
        + 3.0 * u * t**2 * points[2][1]
        + t**3 * points[3][1]
    )
    return x, y


def edge_points(edge: dict[str, Any]) -> list[CubicPoint]:
    out: list[CubicPoint] = []
    for op in edge.get("_draw_", []):
        if op.get("op") != "b":
            continue
        points = [(float(x), float(y)) for x, y in op["points"]]
        for cubic_index, start in enumerate(range(0, len(points) - 3, 3)):
            cubic = points[start : start + 4]
            for step in range(SAMPLE_STEPS + 1):
                t = step / SAMPLE_STEPS
                x, y = cubic_point(cubic, t)
                out.append(CubicPoint(0, cubic_index, t, x, y))
    return out


def endpoint_t(edge_point_count: int, point_index: int) -> float:
    if edge_point_count <= 1:
        return 0.0
    return point_index / (edge_point_count - 1)


def is_endpoint_entry_or_exit(
    edge: dict[str, Any], node_id: int, point_index: int, point_count: int
) -> bool:
    # Only suppress the actual drawn departure/arrival neighborhoods. A spline
    # that touches a head or tail node elsewhere is still a box-grazing defect.
    progress = endpoint_t(point_count, point_index)
    if node_id == int(edge["tail"]) and progress <= 0.05:
        return True
    if node_id == int(edge["head"]) and progress >= 0.95:
        return True
    return False


def distance_to_box_outline(point: CubicPoint, box: Box) -> float:
    x, y = point.x, point.y
    if box.left <= x <= box.right and box.bottom <= y <= box.top:
        return min(x - box.left, box.right - x, y - box.bottom, box.top - y)
    dx = max(box.left - x, 0.0, x - box.right)
    dy = max(box.bottom - y, 0.0, y - box.top)
    if dx > 0.0 and dy > 0.0:
        return math.hypot(dx, dy)
    return max(dx, dy)


def nearest_corner(point: CubicPoint, box: Box) -> str:
    corners = {
        "bottom-left": (box.left, box.bottom),
        "top-left": (box.left, box.top),
        "bottom-right": (box.right, box.bottom),
        "top-right": (box.right, box.top),
    }
    return min(
        corners,
        key=lambda name: math.hypot(point.x - corners[name][0], point.y - corners[name][1]),
    )


def clearance_violations(layout: dict[str, Any], graph: Path) -> list[Violation]:
    boxes = drawn_node_boxes(layout)
    violations: dict[tuple[str, int, int, str, str], Violation] = {}
    for edge_index, edge in enumerate(layout.get("edges", [])):
        points = edge_points(edge)
        points = [
            CubicPoint(edge_index, point.cubic_index, point.t, point.x, point.y)
            for point in points
        ]
        for point_index, point in enumerate(points):
            for node_id, box in boxes.items():
                if is_endpoint_entry_or_exit(edge, node_id, point_index, len(points)):
                    continue
                distance = distance_to_box_outline(point, box)
                if distance >= CLEARANCE:
                    continue
                violation = Violation(
                    graph=graph,
                    edge_index=edge_index,
                    cubic_index=point.cubic_index,
                    t=point.t,
                    box=box.name,
                    corner=nearest_corner(point, box),
                    distance=distance,
                    x=point.x,
                    y=point.y,
                )
                old = violations.get(violation.key)
                if old is None or violation.distance < old.distance:
                    violations[violation.key] = violation
    return sorted(
        violations.values(),
        key=lambda item: (item.graph.as_posix(), item.edge_index, item.cubic_index, item.box, item.distance),
    )


def print_violations(label: str, violations: list[Violation]) -> None:
    print(f"{label}: {len(violations)} point(s) closer than {CLEARANCE:.3f}pt")
    for violation in violations:
        print(
            f"  {violation.graph}: edge#{violation.edge_index} "
            f"cubic#{violation.cubic_index} t={violation.t:.4f} "
            f"point=({violation.x:.3f},{violation.y:.3f}) "
            f"box={violation.box} corner={violation.corner} "
            f"distance={violation.distance:.6f}pt"
        )


def main() -> int:
    root = repo_root()
    dot = dot_path(root)
    current: list[Violation] = []
    baseline: list[Violation] = []
    for graph in GRAPHS:
        current.extend(clearance_violations(render_json(dot, root / graph), graph))
        baseline.extend(clearance_violations(render_baseline_json(dot, root, graph), graph))

    print(
        "required clearance: "
        f"{CLEARANCE:.3f}pt (the record2 tail departure visibly has 0.5pt; "
        "a foreign-node contact at 0.0pt is a graze)"
    )
    print_violations(f"current vs {BASE_COMMIT[:12]}", current)
    print_violations(f"baseline {BASE_COMMIT[:12]}", baseline)

    baseline_keys = {violation.key for violation in baseline}
    new_violations = [violation for violation in current if violation.key not in baseline_keys]
    if new_violations:
        print_violations("new violations", new_violations)
        return 1
    print("no new box-clearance grazes relative to 73b8ef683")
    return 0


if __name__ == "__main__":
    sys.exit(main())
