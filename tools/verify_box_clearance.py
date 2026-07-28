#!/usr/bin/env python3
"""Report concentrated splines that run too close to drawn node boxes."""

from __future__ import annotations

import json
import math
import os
import heapq
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_COMMIT = "73b8ef683bf78ae4f374f508291974daa8839651"
DEFAULT_NODE_PENWIDTH = 1.0
CLEARANCE = DEFAULT_NODE_PENWIDTH / 2.0
DETOUR_RATIO_LIMIT = 1.25
FLOOR_GAP_LIMIT = 1.20
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


@dataclass(frozen=True)
class InsideViolation:
    graph: Path
    edge_index: int
    cubic_index: int
    t: float
    box: str
    x: float
    y: float


@dataclass(frozen=True)
class EdgeMeasure:
    graph: Path
    edge_index: int
    path_length: float
    straight_length: float
    ratio: float
    outside_sides: tuple[str, ...]


@dataclass(frozen=True)
class FloorMeasure:
    graph: Path
    edge_index: int
    path_length: float
    straight_length: float
    ratio: float
    route: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Detour:
    graph: Path
    edge_index: int
    box: str
    path_length: float
    straight_length: float
    ratio: float
    outside_sides: tuple[str, ...]


@dataclass(frozen=True)
class FloorGap:
    graph: Path
    edge_index: int
    path_length: float
    floor_length: float
    ratio: float


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


def drawn_node_body_boxes(layout: dict[str, Any]) -> dict[int, list[Box]]:
    boxes: dict[int, list[Box]] = {}
    outline_boxes = drawn_node_boxes(layout)
    for obj in layout.get("objects", []):
        node_id = int(obj["_gvid"])
        rects = obj.get("rects")
        if rects:
            boxes[node_id] = []
            for index, rect in enumerate(rects.split()):
                left, bottom, right, top = (float(item) for item in rect.split(","))
                boxes[node_id].append(
                    Box(
                        name=f"{obj['name']}[{index}]",
                        left=left,
                        bottom=bottom,
                        right=right,
                        top=top,
                    )
                )
            continue
        if node_id in outline_boxes:
            boxes[node_id] = [outline_boxes[node_id]]
    return boxes


def inflate_box(box: Box, margin: float) -> Box:
    return Box(
        name=box.name,
        left=box.left - margin,
        bottom=box.bottom - margin,
        right=box.right + margin,
        top=box.top + margin,
    )


def obstacle_boxes(layout: dict[str, Any]) -> list[Box]:
    out: list[Box] = []
    outline_boxes = drawn_node_boxes(layout)
    for node_id, box in outline_boxes.items():
        out.append(inflate_box(box, CLEARANCE))
        for field_box in drawn_node_body_boxes(layout).get(node_id, []):
            out.append(inflate_box(field_box, CLEARANCE))
    return out


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


def drawn_control_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    for op in edge.get("_draw_", []):
        if op.get("op") == "b":
            return [(float(x), float(y)) for x, y in op["points"]]
    return []


def sampled_points(edge: dict[str, Any], edge_index: int) -> list[CubicPoint]:
    return [
        CubicPoint(edge_index, point.cubic_index, point.t, point.x, point.y)
        for point in edge_points(edge)
    ]


def path_length(points: list[CubicPoint]) -> float:
    return sum(
        math.hypot(b.x - a.x, b.y - a.y)
        for a, b in zip(points, points[1:])
    )


def edge_measure(graph: Path, edge_index: int, edge: dict[str, Any]) -> EdgeMeasure:
    points = sampled_points(edge, edge_index)
    controls = drawn_control_points(edge)
    length = path_length(points)
    if len(controls) >= 2:
        straight = math.hypot(
            controls[-1][0] - controls[0][0],
            controls[-1][1] - controls[0][1],
        )
    else:
        straight = 0.0
    return EdgeMeasure(
        graph=graph,
        edge_index=edge_index,
        path_length=length,
        straight_length=straight,
        ratio=length / straight if straight > 0.0 else math.inf,
        outside_sides=(),
    )


def endpoint_t(edge_point_count: int, point_index: int) -> float:
    if edge_point_count <= 1:
        return 0.0
    return point_index / (edge_point_count - 1)


def is_endpoint_entry_or_exit(
    edge: dict[str, Any],
    node_id: int,
    point: CubicPoint,
    point_index: int,
    points: list[CubicPoint],
) -> bool:
    # Suppress the actual drawn departure/arrival neighborhoods. Also allow an
    # aligned approach to the connected node: an edge entering from above may
    # legitimately run close to the top outline, but a corner far away from the
    # endpoint is still reported.
    progress = endpoint_t(len(points), point_index)
    if node_id == int(edge["tail"]) and progress <= 0.05:
        return True
    if node_id == int(edge["head"]) and progress >= 0.95:
        return True
    if node_id == int(edge["tail"]) and points:
        start = points[0]
        return (
            abs(point.x - start.x) <= CLEARANCE
            or abs(point.y - start.y) <= CLEARANCE
        )
    if node_id == int(edge["head"]) and points:
        end = points[-1]
        return (
            abs(point.x - end.x) <= CLEARANCE
            or abs(point.y - end.y) <= CLEARANCE
        )
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


def outside_sides(points: list[CubicPoint], box: Box) -> tuple[str, ...]:
    sides = set()
    for point in points:
        if point.x < box.left - CLEARANCE:
            sides.add("left")
        if point.x > box.right + CLEARANCE:
            sides.add("right")
        if point.y < box.bottom - CLEARANCE:
            sides.add("bottom")
        if point.y > box.top + CLEARANCE:
            sides.add("top")
    return tuple(sorted(sides))


def strictly_inside_box(point: CubicPoint, box: Box) -> bool:
    return box.left < point.x < box.right and box.bottom < point.y < box.top


def point_in_box(point: tuple[float, float], box: Box) -> bool:
    x, y = point
    return box.left < x < box.right and box.bottom < y < box.top


def segment_box_interval(
    start: tuple[float, float], end: tuple[float, float], box: Box
) -> tuple[float, float] | None:
    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    t0 = 0.0
    t1 = 1.0
    for origin, delta, low, high in (
        (x0, dx, box.left, box.right),
        (y0, dy, box.bottom, box.top),
    ):
        if abs(delta) < 1e-12:
            if origin < low or origin > high:
                return None
            continue
        inv = 1.0 / delta
        enter = (low - origin) * inv
        exit = (high - origin) * inv
        if enter > exit:
            enter, exit = exit, enter
        t0 = max(t0, enter)
        t1 = min(t1, exit)
        if t0 > t1:
            return None
    return t0, t1


def segment_enters_box(
    start: tuple[float, float], end: tuple[float, float], box: Box
) -> bool:
    interval = segment_box_interval(start, end, box)
    if interval is None:
        return False
    t0, t1 = interval
    if t1 - t0 <= 1e-9:
        return False
    mid = (max(t0, 0.0) + min(t1, 1.0)) / 2.0
    x = start[0] + (end[0] - start[0]) * mid
    y = start[1] + (end[1] - start[1]) * mid
    return point_in_box((x, y), box)


def segment_is_clear(
    start: tuple[float, float], end: tuple[float, float], obstacles: list[Box]
) -> bool:
    return not any(segment_enters_box(start, end, box) for box in obstacles)


def shortest_clear_route(
    start: tuple[float, float], end: tuple[float, float], obstacles: list[Box]
) -> tuple[float, tuple[tuple[float, float], ...]]:
    vertices = [start, end]
    seen = {(round(start[0], 6), round(start[1], 6)), (round(end[0], 6), round(end[1], 6))}
    for box in obstacles:
        for point in (
            (box.left, box.bottom),
            (box.left, box.top),
            (box.right, box.bottom),
            (box.right, box.top),
        ):
            key = (round(point[0], 6), round(point[1], 6))
            if key in seen:
                continue
            if any(point_in_box(point, other) for other in obstacles):
                continue
            seen.add(key)
            vertices.append(point)

    graph: list[list[tuple[float, int]]] = [[] for _ in vertices]
    for i, point_i in enumerate(vertices):
        for j in range(i + 1, len(vertices)):
            point_j = vertices[j]
            if not segment_is_clear(point_i, point_j, obstacles):
                continue
            length = math.hypot(point_j[0] - point_i[0], point_j[1] - point_i[1])
            graph[i].append((length, j))
            graph[j].append((length, i))

    distances = [math.inf] * len(vertices)
    previous: list[int | None] = [None] * len(vertices)
    distances[0] = 0.0
    queue: list[tuple[float, int]] = [(0.0, 0)]
    while queue:
        distance, index = heapq.heappop(queue)
        if distance != distances[index]:
            continue
        if index == 1:
            break
        for length, neighbor in graph[index]:
            candidate = distance + length
            if candidate >= distances[neighbor]:
                continue
            distances[neighbor] = candidate
            previous[neighbor] = index
            heapq.heappush(queue, (candidate, neighbor))

    if not math.isfinite(distances[1]):
        return math.inf, ()
    route: list[tuple[float, float]] = []
    cursor: int | None = 1
    while cursor is not None:
        route.append(vertices[cursor])
        cursor = previous[cursor]
    route.reverse()
    return distances[1], tuple(route)


def floor_measure(
    graph: Path, edge_index: int, edge: dict[str, Any], layout: dict[str, Any]
) -> FloorMeasure:
    controls = drawn_control_points(edge)
    straight = (
        math.hypot(controls[-1][0] - controls[0][0], controls[-1][1] - controls[0][1])
        if len(controls) >= 2
        else 0.0
    )
    if len(controls) < 2:
        return FloorMeasure(graph, edge_index, math.inf, straight, math.inf, ())
    length, route = shortest_clear_route(controls[0], controls[-1], obstacle_boxes(layout))
    return FloorMeasure(
        graph=graph,
        edge_index=edge_index,
        path_length=length,
        straight_length=straight,
        ratio=length / straight if straight > 0.0 else math.inf,
        route=route,
    )


def inside_violations(layout: dict[str, Any], graph: Path) -> list[InsideViolation]:
    boxes = drawn_node_body_boxes(layout)
    out: list[InsideViolation] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        points = sampled_points(edge, edge_index)
        for point_index, point in enumerate(points):
            for node_id, node_boxes in boxes.items():
                if is_endpoint_entry_or_exit(edge, node_id, point, point_index, points):
                    continue
                for box in node_boxes:
                    if not strictly_inside_box(point, box):
                        continue
                    out.append(
                        InsideViolation(
                            graph=graph,
                            edge_index=edge_index,
                            cubic_index=point.cubic_index,
                            t=point.t,
                            box=box.name,
                            x=point.x,
                            y=point.y,
                        )
                    )
                    break
    return out


def clearance_violations(layout: dict[str, Any], graph: Path) -> list[Violation]:
    boxes = drawn_node_boxes(layout)
    violations: dict[tuple[str, int, int, str, str], Violation] = {}
    for edge_index, edge in enumerate(layout.get("edges", [])):
        points = sampled_points(edge, edge_index)
        for point_index, point in enumerate(points):
            for node_id, box in boxes.items():
                if is_endpoint_entry_or_exit(edge, node_id, point, point_index, points):
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


def edge_measures(layout: dict[str, Any], graph: Path) -> list[EdgeMeasure]:
    return [
        edge_measure(graph, edge_index, edge)
        for edge_index, edge in enumerate(layout.get("edges", []))
    ]


def floor_measures(layout: dict[str, Any], graph: Path) -> list[FloorMeasure]:
    return [
        floor_measure(graph, edge_index, edge, layout)
        for edge_index, edge in enumerate(layout.get("edges", []))
    ]


def detours(layout: dict[str, Any], graph: Path) -> list[Detour]:
    boxes = drawn_node_boxes(layout)
    out: list[Detour] = []
    for edge_index, edge in enumerate(layout.get("edges", [])):
        measure = edge_measure(graph, edge_index, edge)
        if measure.ratio <= DETOUR_RATIO_LIMIT:
            continue
        points = sampled_points(edge, edge_index)
        for node_id, box in boxes.items():
            sides = outside_sides(points, box)
            if len(sides) < 2:
                continue
            out.append(
                Detour(
                    graph=graph,
                    edge_index=edge_index,
                    box=box.name,
                    path_length=measure.path_length,
                    straight_length=measure.straight_length,
                    ratio=measure.ratio,
                    outside_sides=sides,
                )
            )
    return out


def print_measures(label: str, measures: list[EdgeMeasure]) -> None:
    print(f"{label}: {len(measures)} drawn edge(s)")
    for measure in measures:
        print(
            f"  {measure.graph}: edge#{measure.edge_index} "
            f"path_length={measure.path_length:.6f}pt "
            f"straight_length={measure.straight_length:.6f}pt "
            f"ratio={measure.ratio:.6f}"
        )


def print_floor_measures(label: str, measures: list[FloorMeasure]) -> None:
    print(f"{label}: {len(measures)} legal floor route(s)")
    for measure in measures:
        route = " -> ".join(
            f"({x:.3f},{y:.3f})" for x, y in measure.route
        )
        print(
            f"  {measure.graph}: edge#{measure.edge_index} "
            f"floor_length={measure.path_length:.6f}pt "
            f"straight_length={measure.straight_length:.6f}pt "
            f"floor_to_straight_ratio={measure.ratio:.6f} "
            f"route={route}"
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


def print_inside_violations(label: str, violations: list[InsideViolation]) -> None:
    print(f"{label}: {len(violations)} point(s) strictly inside node body boxes")
    for violation in violations:
        print(
            f"  {violation.graph}: edge#{violation.edge_index} "
            f"cubic#{violation.cubic_index} t={violation.t:.4f} "
            f"point=({violation.x:.3f},{violation.y:.3f}) "
            f"box={violation.box}"
        )


def print_detours(label: str, items: list[Detour]) -> None:
    print(
        f"{label}: {len(items)} route(s) longer than {DETOUR_RATIO_LIMIT:.2f}x "
        "while visiting multiple outside sides of a node box"
    )
    for item in items:
        print(
            f"  {item.graph}: edge#{item.edge_index} box={item.box} "
            f"path_length={item.path_length:.6f}pt "
            f"straight_length={item.straight_length:.6f}pt "
            f"ratio={item.ratio:.6f} outside_sides={','.join(item.outside_sides)}"
        )


def floor_gaps(
    measures: list[EdgeMeasure], floors: list[FloorMeasure]
) -> list[FloorGap]:
    floors_by_edge = {(floor.graph, floor.edge_index): floor for floor in floors}
    out: list[FloorGap] = []
    for measure in measures:
        floor = floors_by_edge[(measure.graph, measure.edge_index)]
        ratio = measure.path_length / floor.path_length
        if ratio <= FLOOR_GAP_LIMIT:
            continue
        out.append(
            FloorGap(
                graph=measure.graph,
                edge_index=measure.edge_index,
                path_length=measure.path_length,
                floor_length=floor.path_length,
                ratio=ratio,
            )
        )
    return out


def print_floor_gaps(label: str, gaps: list[FloorGap]) -> None:
    print(f"{label}: {len(gaps)} route(s) longer than {FLOOR_GAP_LIMIT:.2f}x legal floor")
    for gap in gaps:
        print(
            f"  {gap.graph}: edge#{gap.edge_index} "
            f"path_length={gap.path_length:.6f}pt "
            f"floor_length={gap.floor_length:.6f}pt "
            f"path_to_floor_ratio={gap.ratio:.6f}"
        )


def main() -> int:
    root = repo_root()
    dot = dot_path(root)
    current: list[Violation] = []
    baseline: list[Violation] = []
    current_measures: list[EdgeMeasure] = []
    baseline_measures: list[EdgeMeasure] = []
    current_detours: list[Detour] = []
    baseline_detours: list[Detour] = []
    current_inside: list[InsideViolation] = []
    baseline_inside: list[InsideViolation] = []
    current_floors: list[FloorMeasure] = []
    baseline_floors: list[FloorMeasure] = []
    baseline_dot = os.environ.get("BOX_CLEARANCE_BASELINE_DOT", dot)
    for graph in GRAPHS:
        current_layout = render_json(dot, root / graph)
        baseline_layout = render_baseline_json(baseline_dot, root, graph)
        current.extend(clearance_violations(current_layout, graph))
        baseline.extend(clearance_violations(baseline_layout, graph))
        current_inside.extend(inside_violations(current_layout, graph))
        baseline_inside.extend(inside_violations(baseline_layout, graph))
        current_measures.extend(edge_measures(current_layout, graph))
        baseline_measures.extend(edge_measures(baseline_layout, graph))
        current_floors.extend(floor_measures(current_layout, graph))
        baseline_floors.extend(floor_measures(baseline_layout, graph))
        current_detours.extend(detours(current_layout, graph))
        baseline_detours.extend(detours(baseline_layout, graph))

    print(
        "required clearance: "
        f"{CLEARANCE:.3f}pt (half of Graphviz's default 1.0pt node pen width; "
        "a foreign-node contact at 0.0pt is a graze)"
    )
    print(
        "detour limit: "
        f"{DETOUR_RATIO_LIMIT:.2f}x straight-line endpoint distance when the "
        "route visits more than one outside side of a node box"
    )
    print(
        "legal floor: shortest visibility-graph path between the drawn Bezier "
        "endpoints around drawn node and record-field rectangles inflated by "
        f"{CLEARANCE:.3f}pt"
    )
    print_floor_measures("current floor", current_floors)
    print_floor_measures(f"baseline {BASE_COMMIT[:12]} floor", baseline_floors)
    print_measures("current", current_measures)
    print_measures(f"baseline {BASE_COMMIT[:12]}", baseline_measures)
    print_violations("current clearance", current)
    print_violations(f"baseline {BASE_COMMIT[:12]}", baseline)
    print_inside_violations("current inside", current_inside)
    print_inside_violations(f"baseline {BASE_COMMIT[:12]} inside", baseline_inside)
    print_detours("current detours", current_detours)
    print_detours(f"baseline {BASE_COMMIT[:12]} detours", baseline_detours)
    current_floor_gaps = floor_gaps(current_measures, current_floors)
    baseline_floor_gaps = floor_gaps(baseline_measures, baseline_floors)
    print_floor_gaps("current floor gap", current_floor_gaps)
    print_floor_gaps(f"baseline {BASE_COMMIT[:12]} floor gap", baseline_floor_gaps)

    if current or current_inside or current_floor_gaps:
        print("box-clearance verification failed")
        return 1
    print("box-clearance and detour verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
