#!/usr/bin/env python3
"""Prototype concentration as an explicit DOT graph transformation."""

from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

import pygraphviz as pgv


IDENTITY_ATTRS = (
    "label",
    "xlabel",
    "headlabel",
    "taillabel",
    "color",
    "style",
    "penwidth",
    "arrowhead",
    "arrowtail",
    "dir",
    "fontname",
    "fontsize",
    "fontcolor",
    "labelfontname",
    "labelfontsize",
    "labelfontcolor",
    "class",
)

EDGE_LABEL_ATTRS = {"label", "xlabel"}
ENDPOINT_LABEL_ATTRS = {"taillabel", "headlabel"}
LABEL_ATTRS = EDGE_LABEL_ATTRS | ENDPOINT_LABEL_ATTRS
ROUTING_ATTRS = {"pos", "lp", "head_lp", "tail_lp", "_draw_", "_hdraw_", "_tdraw_", "_ldraw_"}


@dataclass(frozen=True)
class NodeRank:
    y: float
    height: float


@dataclass(frozen=True)
class EdgeInfo:
    index: int
    tail: str
    head: str
    attrs: tuple[tuple[str, str], ...]

    def attrdict(self) -> dict[str, str]:
        return dict(self.attrs)

    def attr(self, name: str, default: str = "") -> str:
        return self.attrdict().get(name, default)

    def identity(self) -> tuple[str, ...]:
        attrs = self.attrdict()
        return tuple(attrs.get(name, "") for name in IDENTITY_ATTRS)


@dataclass
class Candidate:
    kind: str
    endpoint: str
    label: str
    identity: tuple[str, ...]
    edges: list[EdgeInfo]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite equivalent fan-in/fan-out edges through point junctions."
    )
    parser.add_argument("input", metavar="IN.gv")
    parser.add_argument("--fan-in", dest="fan_in", action="store_true", default=True)
    parser.add_argument("--no-fan-in", dest="fan_in", action="store_false")
    parser.add_argument("--fan-out", dest="fan_out", action="store_true", default=True)
    parser.add_argument("--no-fan-out", dest="fan_out", action="store_false")
    parser.add_argument("--min-group", type=int, default=2)
    parser.add_argument("--sum-weights", dest="sum_weights", action="store_true", default=True)
    parser.add_argument("--no-sum-weights", dest="sum_weights", action="store_false")
    parser.add_argument("--penwidth-by-count", action="store_true", default=False)
    parser.add_argument("--preserve-ranks", dest="preserve_ranks", action="store_true", default=True)
    parser.add_argument("--no-preserve-ranks", dest="preserve_ranks", action="store_false")
    parser.add_argument("--visible-junctions", action="store_true", default=False)
    parser.add_argument("--dot", default="dot", help="dot executable used to read original ranks.")
    return parser.parse_args()


def edge_attrs(edge) -> dict[str, str]:
    return {str(k): str(v) for k, v in edge.attr.items()}


def node_clusters(graph: pgv.AGraph) -> dict[str, str]:
    result: dict[str, str] = {}

    def walk(subgraph: pgv.AGraph, current: str = "") -> None:
        name = subgraph.name or ""
        cluster = name if name.startswith("cluster") else current
        for node in subgraph.nodes():
            result[str(node)] = cluster
        for child in subgraph.subgraphs():
            walk(child, cluster)

    for subgraph in graph.subgraphs():
        walk(subgraph)
    return result


def has_port(attrs: dict[str, str]) -> bool:
    return any(attrs.get(name) for name in ("headport", "tailport"))


def refusal(kind: str, endpoint: str, label: str, size: int, reason: str) -> None:
    print(f"refused:{reason}\t{kind}\t{endpoint}\t{label}\t{size}", file=sys.stderr)


def census(kind: str, endpoint: str, label: str, size: int) -> None:
    print(f"{kind}\t{endpoint}\t{label}\t{size}", file=sys.stderr)


def copy_without(attrs: dict[str, str], names: set[str]) -> dict[str, str]:
    return {k: v for k, v in attrs.items() if k not in names and k not in ROUTING_ATTRS}


def common_cluster(candidate: Candidate, clusters: dict[str, str]) -> str:
    names = {clusters.get(candidate.endpoint, "")}
    for edge in candidate.edges:
        names.add(clusters.get(edge.tail, ""))
        names.add(clusters.get(edge.head, ""))
    return names.pop() if len(names) == 1 else ""


def output_scope(graph: pgv.AGraph, cluster: str) -> pgv.AGraph:
    if not cluster:
        return graph
    stack = list(graph.subgraphs())
    while stack:
        subgraph = stack.pop()
        if subgraph.name == cluster:
            return subgraph
        stack.extend(subgraph.subgraphs())
    return graph


def fresh_node_name(graph: pgv.AGraph, prefix: str = "__junction") -> str:
    existing = {str(n) for n in graph.nodes()}
    index = 0
    while True:
        name = f"{prefix}{index}"
        if name not in existing:
            return name
        index += 1


def weight_sum(edges: list[EdgeInfo]) -> str:
    total = 0.0
    for edge in edges:
        try:
            total += float(edge.attr("weight", "1"))
        except ValueError:
            total += 1.0
    if total.is_integer():
        return str(int(total))
    return f"{total:g}"


def plain_ranks(path: str, dot: str) -> dict[str, NodeRank]:
    proc = subprocess.run(
        [dot, "-Tplain", path],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ranks: dict[str, NodeRank] = {}
    for line in proc.stdout.splitlines():
        fields = shlex.split(line)
        if len(fields) >= 6 and fields[0] == "node":
            ranks[fields[1]] = NodeRank(float(fields[3]), float(fields[5]))
    return ranks


def endpoint_relation(candidate: Candidate, ranks: dict[str, NodeRank]) -> int | None:
    signs: set[int] = set()
    for edge in candidate.edges:
        if edge.tail not in ranks or edge.head not in ranks:
            return None
        anchor = edge.tail if candidate.kind == "fan-out" else edge.head
        other = edge.head if candidate.kind == "fan-out" else edge.tail
        delta = ranks[other].y - ranks[anchor].y
        if math.isclose(delta, 0.0, abs_tol=1e-6):
            return None
        signs.add(1 if delta > 0 else -1)
    if len(signs) != 1:
        return None
    return signs.pop()


def add_junction(graph: pgv.AGraph, name: str, visible: bool) -> None:
    attrs = {
        "shape": "point",
        "width": "0.035" if visible else "0.02",
        "height": "0.035" if visible else "0.02",
        "label": "",
    }
    if not visible:
        attrs["style"] = "invis"
    graph.add_node(name, **attrs)


def trunk_attrs(candidate: Candidate, args: argparse.Namespace) -> dict[str, str]:
    source = candidate.edges[0].attrdict()
    attrs = copy_without(source, LABEL_ATTRS)
    shared_endpoint_label = "taillabel" if candidate.kind == "fan-out" else "headlabel"
    attrs.update(
        (name, value)
        for name, value in source.items()
        if name in EDGE_LABEL_ATTRS or name == shared_endpoint_label
    )
    if args.sum_weights:
        attrs["weight"] = weight_sum(candidate.edges)
    if args.penwidth_by_count:
        attrs["penwidth"] = str(max(1, len(candidate.edges)))
    return attrs


def arm_attrs(candidate: Candidate, edge: EdgeInfo) -> dict[str, str]:
    source = edge.attrdict()
    attrs = copy_without(source, LABEL_ATTRS)
    distinct_endpoint_label = "headlabel" if candidate.kind == "fan-out" else "taillabel"
    attrs.update((name, value) for name, value in source.items() if name == distinct_endpoint_label)
    return attrs


def reversed_attrs(attrs: dict[str, str]) -> dict[str, str]:
    out = dict(attrs)
    direction = out.get("dir", "forward").lower()
    out["dir"] = {
        "forward": "back",
        "back": "forward",
        "both": "both",
        "none": "none",
    }.get(direction, "back")
    head = out.pop("arrowhead", None)
    tail = out.pop("arrowtail", None)
    if head is not None:
        out["arrowtail"] = head
    if tail is not None:
        out["arrowhead"] = tail
    head_label = out.pop("headlabel", None)
    tail_label = out.pop("taillabel", None)
    if head_label is not None:
        out["taillabel"] = head_label
    if tail_label is not None:
        out["headlabel"] = tail_label
    return out


def endpoint_arrows(attrs: dict[str, str], *, keep_head: bool, keep_tail: bool) -> dict[str, str]:
    out = dict(attrs)
    direction = out.get("dir", "forward").lower()
    has_head = keep_head and direction in {"forward", "both"}
    has_tail = keep_tail and direction in {"back", "both"}
    if has_head and has_tail:
        out["dir"] = "both"
    elif has_head:
        out["dir"] = "forward"
    elif has_tail:
        out["dir"] = "back"
    else:
        out["dir"] = "none"
    if not has_head:
        out.pop("arrowhead", None)
    if not has_tail:
        out.pop("arrowtail", None)
    return out


def logical_trunk_attrs(candidate: Candidate, args: argparse.Namespace) -> dict[str, str]:
    if candidate.kind == "fan-out":
        return endpoint_arrows(trunk_attrs(candidate, args), keep_head=False, keep_tail=True)
    return endpoint_arrows(trunk_attrs(candidate, args), keep_head=True, keep_tail=False)


def logical_arm_attrs(candidate: Candidate, edge: EdgeInfo) -> dict[str, str]:
    if candidate.kind == "fan-out":
        return endpoint_arrows(arm_attrs(candidate, edge), keep_head=True, keep_tail=False)
    return endpoint_arrows(arm_attrs(candidate, edge), keep_head=False, keep_tail=True)


def add_group_naive(
    graph: pgv.AGraph, scope: pgv.AGraph, candidate: Candidate, node: str, args: argparse.Namespace
) -> None:
    add_junction(scope, node, args.visible_junctions)
    if candidate.kind == "fan-out":
        graph.add_edge(candidate.endpoint, node, **logical_trunk_attrs(candidate, args))
        for edge in candidate.edges:
            graph.add_edge(node, edge.head, **logical_arm_attrs(candidate, edge))
    else:
        for edge in candidate.edges:
            graph.add_edge(edge.tail, node, **logical_arm_attrs(candidate, edge))
        graph.add_edge(node, candidate.endpoint, **logical_trunk_attrs(candidate, args))


def add_group_preserving(
    graph: pgv.AGraph,
    scope: pgv.AGraph,
    candidate: Candidate,
    node: str,
    args: argparse.Namespace,
    ranks: dict[str, NodeRank],
) -> bool:
    relation = endpoint_relation(candidate, ranks)
    if relation is None:
        refusal(candidate.kind, candidate.endpoint, candidate.label, len(candidate.edges), "rank-straddles-anchor")
        return False

    add_junction(scope, node, args.visible_junctions)
    if candidate.kind == "fan-out":
        tail = candidate.endpoint
        if relation < 0:
            graph.add_edge(tail, node, **logical_trunk_attrs(candidate, args))
            for edge in candidate.edges:
                graph.add_edge(node, edge.head, **logical_arm_attrs(candidate, edge))
        else:
            graph.add_edge(node, tail, **reversed_attrs(logical_trunk_attrs(candidate, args)))
            for edge in candidate.edges:
                graph.add_edge(edge.head, node, **reversed_attrs(logical_arm_attrs(candidate, edge)))
    else:
        head = candidate.endpoint
        if relation > 0:
            for edge in candidate.edges:
                graph.add_edge(edge.tail, node, **logical_arm_attrs(candidate, edge))
            graph.add_edge(node, head, **logical_trunk_attrs(candidate, args))
        else:
            graph.add_edge(head, node, **reversed_attrs(logical_trunk_attrs(candidate, args)))
            for edge in candidate.edges:
                graph.add_edge(node, edge.tail, **reversed_attrs(logical_arm_attrs(candidate, edge)))
    return True


def delete_original_edges(graph: pgv.AGraph, candidate: Candidate) -> None:
    for edge in candidate.edges:
        graph.delete_edge(edge.tail, edge.head)


def orient_remaining_edges(graph: pgv.AGraph, edges: list[EdgeInfo], ranks: dict[str, NodeRank]) -> None:
    for edge in edges:
        tail = ranks.get(edge.tail)
        head = ranks.get(edge.head)
        if tail is None or head is None or head.y <= tail.y or math.isclose(head.y, tail.y, abs_tol=1e-6):
            continue
        attrs = reversed_attrs(copy_without(edge.attrdict(), set()))
        graph.delete_edge(edge.tail, edge.head)
        graph.add_edge(edge.head, edge.tail, **attrs)


def main() -> int:
    args = parse_args()
    if args.min_group < 2:
        print("--min-group must be at least 2", file=sys.stderr)
        return 2

    graph = pgv.AGraph(args.input)
    if not graph.is_directed():
        print("refused:undirected-graph\t*\t\t0", file=sys.stderr)
        print(graph.string(), end="")
        return 0

    if graph.strict:
        print("refused:strict-graph\t*\t\t0", file=sys.stderr)
        print(graph.string(), end="")
        return 0

    rankdir = (graph.graph_attr.get("rankdir") or "TB").upper()
    if rankdir not in {"TB", ""}:
        print(f"refused:rankdir-{rankdir}-not-tb\t*\t\t0", file=sys.stderr)

    ranks: dict[str, NodeRank] = {}
    if args.preserve_ranks:
        try:
            ranks = plain_ranks(args.input, args.dot)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"refused:rank-read-failed\t*\t\t0\t{exc}", file=sys.stderr)
            print(graph.string(), end="")
            return 1

    clusters = node_clusters(graph)
    raw_edges: list[EdgeInfo] = []
    for index, edge in enumerate(graph.edges()):
        raw_edges.append(
            EdgeInfo(
                index=index,
                tail=str(edge[0]),
                head=str(edge[1]),
                attrs=tuple(sorted(edge_attrs(edge).items())),
            )
        )

    pair_counts = Counter((edge.tail, edge.head) for edge in raw_edges)
    eligible: list[EdgeInfo] = []
    skipped: set[int] = set()
    for edge in raw_edges:
        attrs = edge.attrdict()
        label = attrs.get("label", "")
        reason = ""
        if edge.tail == edge.head:
            reason = "self-loop"
        elif has_port(attrs):
            reason = "port"
        elif attrs.get("constraint", "").lower() == "false":
            reason = "constraint-false"
        elif clusters.get(edge.tail, "") != clusters.get(edge.head, ""):
            reason = "different-clusters"
        elif pair_counts[(edge.tail, edge.head)] > 1:
            reason = "true-multiedge"

        if reason:
            skipped.add(edge.index)
            refusal("edge", f"{edge.tail}->{edge.head}", label, 1, reason)
        else:
            eligible.append(edge)

    candidates: list[Candidate] = []
    if args.fan_out:
        buckets: dict[tuple[str, tuple[str, ...]], list[EdgeInfo]] = defaultdict(list)
        for edge in eligible:
            buckets[(edge.tail, edge.identity())].append(edge)
        for (endpoint, identity), edges in buckets.items():
            if len(edges) >= args.min_group:
                candidates.append(Candidate("fan-out", endpoint, edges[0].attr("label"), identity, edges))
    if args.fan_in:
        buckets = defaultdict(list)
        for edge in eligible:
            buckets[(edge.head, edge.identity())].append(edge)
        for (endpoint, identity), edges in buckets.items():
            if len(edges) >= args.min_group:
                candidates.append(Candidate("fan-in", endpoint, edges[0].attr("label"), identity, edges))

    candidates.sort(key=lambda c: (-len(c.edges), c.kind, c.endpoint, c.label))
    selected: list[Candidate] = []
    transformed: set[int] = set()
    for candidate in candidates:
        if args.preserve_ranks and endpoint_relation(candidate, ranks) is None:
            refusal(candidate.kind, candidate.endpoint, candidate.label, len(candidate.edges), "rank-straddles-anchor")
            continue
        overlap = sorted(edge.index for edge in candidate.edges if edge.index in transformed)
        if overlap:
            refusal(candidate.kind, candidate.endpoint, candidate.label, len(candidate.edges), "overlaps-selected-group")
            continue
        selected.append(candidate)
        transformed.update(edge.index for edge in candidate.edges)

    out = pgv.AGraph(string=graph.string())
    out.graph_attr["concentrate"] = "false"
    really_transformed: set[int] = set()
    for candidate in selected:
        node = fresh_node_name(out)
        scope = output_scope(out, common_cluster(candidate, clusters))
        if args.preserve_ranks:
            ok = add_group_preserving(out, scope, candidate, node, args, ranks)
        else:
            ok = True
            add_group_naive(out, scope, candidate, node, args)
        if not ok:
            continue
        delete_original_edges(out, candidate)
        census(candidate.kind, candidate.endpoint, candidate.label, len(candidate.edges))
        really_transformed.update(edge.index for edge in candidate.edges)

    if args.preserve_ranks:
        remaining = [edge for edge in eligible if edge.index not in really_transformed]
        orient_remaining_edges(out, remaining, ranks)

    print(out.string(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
