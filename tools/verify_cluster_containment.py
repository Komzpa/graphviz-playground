#!/usr/bin/env python3
"""Verify that rendered cluster boxes match rendered membership."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


EPSILON = 0.5
BASELINE_REF = "97b7811df5e673ea0727d226eae8c7911abe58b0"
RENDER_LIMIT_KIB = 2 * 1024 * 1024


@dataclass(frozen=True)
class Box:
    left: float
    bottom: float
    right: float
    top: float

    @classmethod
    def parse(cls, text: str) -> "Box":
        left, bottom, right, top = (float(part) for part in text.split(","))
        return cls(left, bottom, right, top)

    def text(self) -> str:
        return f"{self.left:g},{self.bottom:g},{self.right:g},{self.top:g}"


@dataclass(frozen=True)
class Item:
    gvid: int
    name: str
    box: Box
    is_cluster: bool


@dataclass(frozen=True)
class Cluster:
    gvid: int
    name: str
    box: Box
    direct_nodes: tuple[int, ...]
    subgraphs: tuple[int, ...]


@dataclass(frozen=True)
class Violation:
    graph: Path
    ranker: str
    cluster: Cluster
    item: Item
    members: tuple[int, ...]
    kind: str


@dataclass(frozen=True)
class Rendered:
    rc: int
    path: Path | None
    stderr: str


@dataclass(frozen=True)
class CheckResult:
    graph: Path
    ranker: str
    clusters: list[str]
    violations: list[Violation]
    baseline_violations: list[Violation]
    byte_identical: bool | None
    failure: str | None


def find_dot(repo: Path) -> Path:
    candidates = (
        repo / "build" / "cmd" / "dot" / "dot_builtins",
        repo / "build" / "cmd" / "dot" / "dot",
    )
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return Path("dot")


def graph_files(repo: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=repo,
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return sorted(
        repo / line for line in proc.stdout.splitlines() if line.endswith((".dot", ".gv"))
    )


def commit_sha(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def candidate_baseline_dot(repo: Path) -> Path | None:
    env = os.environ.get("CLUSTER_CONTAINMENT_BASELINE_DOT")
    if env:
        path = Path(env)
        if path.exists() and os.access(path, os.X_OK):
            return path

    local = repo / f"build-clusterbox-baseline-{BASELINE_REF[:12]}" / "cmd" / "dot" / "dot_builtins"
    if local.exists() and os.access(local, os.X_OK):
        return local

    sibling = (
        repo.parent.parent
        / "graphviz-port249-20260727"
        / "src"
        / "build"
        / "cmd"
        / "dot"
        / "dot_builtins"
    )
    if sibling.exists() and os.access(sibling, os.X_OK):
        try:
            sha = commit_sha(sibling.parents[3])
        except (subprocess.CalledProcessError, IndexError):
            return None
        if sha == BASELINE_REF:
            return sibling
    return None


def cap_address_space() -> None:
    limit = RENDER_LIMIT_KIB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run_dot(dot: Path, graph: Path, newrank: bool, timeout: float) -> Rendered:
    args = [str(dot), "-Kdot", "-Tjson"]
    if newrank:
        args.append("-Gnewrank=true")
    args.append(str(graph))

    output = tempfile.NamedTemporaryFile(prefix="cluster-layout-", suffix=".json", delete=False)
    output_path = Path(output.name)
    try:
        with output:
            try:
                proc = subprocess.run(
                    args,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=timeout,
                    preexec_fn=cap_address_space,
                )
            except subprocess.TimeoutExpired as exc:
                stderr = (
                    exc.stderr.decode("utf-8", "replace")
                    if isinstance(exc.stderr, bytes)
                    else ""
                )
                output_path.unlink(missing_ok=True)
                return Rendered(124, None, f"timeout after {timeout:g}s {stderr}".strip())
        if proc.returncode != 0:
            output_path.unlink(missing_ok=True)
            stderr = proc.stderr.decode("utf-8", "replace")
            return Rendered(proc.returncode, None, stderr.strip())
        stderr = proc.stderr.decode("utf-8", "replace")
        return Rendered(proc.returncode, output_path, stderr.strip())
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def descendant_nodes(
    cluster: Cluster, clusters: dict[int, Cluster], subgraph_nodes: dict[int, set[int]]
) -> set[int]:
    nodes = set(cluster.direct_nodes)
    for child in descendant_subgraphs(cluster.gvid, clusters, subgraph_nodes):
        nodes.update(subgraph_nodes.get(child, set()))
    return nodes


def descendant_subgraphs(
    gvid: int, clusters: dict[int, Cluster], subgraph_nodes: dict[int, set[int]]
) -> set[int]:
    descendants: set[int] = set()
    children = clusters[gvid].subgraphs if gvid in clusters else ()
    for child in children:
        descendants.add(child)
        descendants.update(descendant_subgraphs(child, clusters, subgraph_nodes))
    return descendants


def descendant_clusters(
    cluster: Cluster, clusters: dict[int, Cluster], subgraph_nodes: dict[int, set[int]]
) -> set[int]:
    return {
        child
        for child in descendant_subgraphs(cluster.gvid, clusters, subgraph_nodes)
        if child in clusters
    }


def ancestors(
    clusters: dict[int, Cluster], subgraph_nodes: dict[int, set[int]]
) -> dict[int, set[int]]:
    result = {gvid: set() for gvid in clusters}
    for cluster in clusters.values():
        for child in descendant_clusters(cluster, clusters, subgraph_nodes):
            result.setdefault(child, set()).add(cluster.gvid)
    return result


def contains(outer: Box, inner: Box) -> bool:
    return (
        inner.left >= outer.left - EPSILON
        and inner.right <= outer.right + EPSILON
        and inner.bottom >= outer.bottom - EPSILON
        and inner.top <= outer.top + EPSILON
    )


def overlaps(left: Box, right: Box) -> bool:
    return (
        max(left.left, right.left) < min(left.right, right.right) - EPSILON
        and max(left.bottom, right.bottom) < min(left.top, right.top) - EPSILON
    )


def bytes_equal(left: Path, right: Path) -> bool:
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(1024 * 1024)
            right_chunk = right_stream.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def node_box(obj: dict) -> Box | None:
    if "pos" not in obj or "width" not in obj or "height" not in obj:
        return None
    x, y = (float(part) for part in obj["pos"].split(","))
    half_width = float(obj["width"]) * 36.0
    half_height = float(obj["height"]) * 36.0
    return Box(x - half_width, y - half_height, x + half_width, y + half_height)


def parse_layout(path: Path) -> tuple[dict[int, Cluster], dict[int, set[int]], list[Item]]:
    with path.open(encoding="utf-8") as stream:
        layout = json.load(stream)

    clusters: dict[int, Cluster] = {}
    subgraph_nodes: dict[int, set[int]] = {}
    items: list[Item] = []
    for obj in layout.get("objects", []):
        gvid = obj.get("_gvid")
        name = obj.get("name", "")
        if not isinstance(gvid, int):
            continue
        subgraph_nodes[gvid] = set(obj.get("nodes", ()))
        if name.startswith("cluster") and "bb" in obj:
            cluster = Cluster(
                gvid=gvid,
                name=name,
                box=Box.parse(obj["bb"]),
                direct_nodes=tuple(obj.get("nodes", ())),
                subgraphs=tuple(obj.get("subgraphs", ())),
            )
            clusters[gvid] = cluster
            items.append(Item(gvid, name, cluster.box, True))
            continue
        box = node_box(obj)
        if box is not None:
            items.append(Item(gvid, name, box, False))
    for obj in layout.get("objects", []):
        gvid = obj.get("_gvid")
        if not isinstance(gvid, int):
            continue
        children = tuple(obj.get("subgraphs", ()))
        if gvid in clusters:
            cluster = clusters[gvid]
            clusters[gvid] = Cluster(
                cluster.gvid, cluster.name, cluster.box, cluster.direct_nodes, children
            )
        elif children:
            clusters[gvid] = Cluster(
                gvid, obj.get("name", ""), Box(0, 0, 0, 0), (), children
            )
    return clusters, subgraph_nodes, items


def verify_layout(graph: Path, ranker: str, path: Path) -> tuple[list[str], list[Violation]]:
    clusters, subgraph_nodes, items = parse_layout(path)
    render_clusters = {gvid: c for gvid, c in clusters.items() if c.name.startswith("cluster")}
    cluster_ancestors = ancestors(clusters, subgraph_nodes)
    cluster_lines: list[str] = []
    violations: list[Violation] = []

    for cluster in sorted(render_clusters.values(), key=lambda c: c.gvid):
        member_nodes = descendant_nodes(cluster, clusters, subgraph_nodes)
        member_clusters = descendant_clusters(cluster, clusters, subgraph_nodes)
        cluster_lines.append(
            f"{ranker} {graph}: cluster {cluster.name} gvid={cluster.gvid} "
            f"box={cluster.box.text()} members={sorted(member_nodes)}"
        )
        for item in items:
            if item.gvid == cluster.gvid:
                continue
            if item.is_cluster:
                if item.gvid in cluster_ancestors.get(cluster.gvid, set()):
                    continue
                is_member = item.gvid in member_clusters
            else:
                is_member = item.gvid in member_nodes

            if is_member and not contains(cluster.box, item.box):
                violations.append(
                    Violation(
                        graph, ranker, cluster, item, tuple(sorted(member_nodes)), "escapes"
                    )
                )
            elif not is_member and overlaps(cluster.box, item.box):
                violations.append(
                    Violation(
                        graph,
                        ranker,
                        cluster,
                        item,
                        tuple(sorted(member_nodes)),
                        "intersects",
                    )
                )
    return cluster_lines, violations


def format_violation(violation: Violation, prefix: str = "") -> str:
    return (
        f"{prefix}{violation.ranker} {violation.graph}: cluster "
        f"{violation.cluster.name} box={violation.cluster.box.text()} "
        f"members={list(violation.members)} {violation.kind} "
        f"{violation.item.name} gvid={violation.item.gvid} "
        f"box={violation.item.box.text()}"
    )


def check_one(
    dot: Path,
    baseline_dot: Path | None,
    repo: Path,
    graph: Path,
    ranker: str,
    timeout: float,
) -> CheckResult:
    rel = graph.relative_to(repo)
    newrank = ranker == "newrank"
    rendered = run_dot(dot, graph, newrank, timeout)
    baseline_rendered = (
        run_dot(baseline_dot, graph, newrank, timeout) if baseline_dot is not None else None
    )

    try:
        if rendered.rc != 0 or rendered.path is None:
            return CheckResult(
                rel,
                ranker,
                [],
                [],
                [],
                None,
                f"{ranker} {rel}: render failed rc={rendered.rc}: {rendered.stderr}",
            )
        clusters, violations = verify_layout(rel, ranker, rendered.path)

        baseline_violations: list[Violation] = []
        byte_identical: bool | None = None
        if baseline_rendered is not None:
            if baseline_rendered.rc != 0 or baseline_rendered.path is None:
                failure = (
                    f"{ranker} {rel}: baseline render failed "
                    f"rc={baseline_rendered.rc}: {baseline_rendered.stderr}"
                )
                return CheckResult(rel, ranker, clusters, violations, [], None, failure)
            _, baseline_violations = verify_layout(rel, ranker, baseline_rendered.path)
            byte_identical = bytes_equal(rendered.path, baseline_rendered.path)
            if not byte_identical:
                current_samples = [rendered.path]
                baseline_samples = [baseline_rendered.path]
                extra_samples: list[Path] = []
                try:
                    for _ in range(2):
                        current = run_dot(dot, graph, newrank, timeout)
                        if current.rc == 0 and current.path is not None:
                            current_samples.append(current.path)
                            extra_samples.append(current.path)
                    for _ in range(2):
                        baseline = run_dot(baseline_dot, graph, newrank, timeout)
                        if baseline.rc == 0 and baseline.path is not None:
                            baseline_samples.append(baseline.path)
                            extra_samples.append(baseline.path)

                    if any(
                        not bytes_equal(current_samples[0], sample)
                        for sample in current_samples[1:]
                    ):
                        return CheckResult(
                            rel,
                            ranker,
                            clusters,
                            violations,
                            baseline_violations,
                            None,
                            f"{ranker} {rel}: byte identity skipped; "
                            "current renderer output is nondeterministic",
                        )
                    if any(
                        not bytes_equal(baseline_samples[0], sample)
                        for sample in baseline_samples[1:]
                    ):
                        return CheckResult(
                            rel,
                            ranker,
                            clusters,
                            violations,
                            baseline_violations,
                            None,
                            f"{ranker} {rel}: byte identity skipped; "
                            "baseline renderer output is nondeterministic",
                        )
                    if any(
                        bytes_equal(current, baseline)
                        for current in current_samples
                        for baseline in baseline_samples
                    ):
                        return CheckResult(
                            rel,
                            ranker,
                            clusters,
                            violations,
                            baseline_violations,
                            None,
                            f"{ranker} {rel}: byte identity skipped; "
                            "renderer output is nondeterministic",
                        )
                finally:
                    for sample in extra_samples:
                        sample.unlink(missing_ok=True)

        return CheckResult(
            rel, ranker, clusters, violations, baseline_violations, byte_identical, None
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return CheckResult(rel, ranker, [], [], [], None, f"{ranker} {rel}: invalid json: {exc}")
    finally:
        if rendered.path is not None:
            rendered.path.unlink(missing_ok=True)
        if baseline_rendered is not None and baseline_rendered.path is not None:
            baseline_rendered.path.unlink(missing_ok=True)


def run_checks(
    dot: Path, baseline_dot: Path | None, repo: Path, graphs: list[Path], timeout: float
) -> list[CheckResult]:
    tasks = [(graph, ranker) for graph in graphs for ranker in ("default", "newrank")]
    results: list[CheckResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_to_task = {
            executor.submit(check_one, dot, baseline_dot, repo, graph, ranker, timeout): (
                graph,
                ranker,
            )
            for graph, ranker in tasks
        }
        total = len(future_to_task)
        for completed, future in enumerate(concurrent.futures.as_completed(future_to_task), 1):
            results.append(future.result())
            if completed % 100 == 0 or completed == total:
                print(f"checked {completed}/{total}", file=sys.stderr)
    return sorted(results, key=lambda item: (str(item.graph), item.ranker))


def print_summary(results: list[CheckResult], baseline_dot: Path | None) -> None:
    for result in results:
        for line in result.clusters:
            print(line)
        for violation in result.baseline_violations:
            print(format_violation(violation, "BASELINE_VIOLATION "))
        for violation in result.violations:
            print(format_violation(violation, "VIOLATION "))
        if result.failure is not None:
            print(f"RENDER_FINDING {result.failure}")

    for ranker in ("default", "newrank"):
        current = [v for r in results if r.ranker == ranker for v in r.violations]
        baseline = [v for r in results if r.ranker == ranker for v in r.baseline_violations]
        print(
            f"baseline_violations_{ranker}={len(baseline)} "
            f"graphs={len({v.graph for v in baseline})}"
        )
        print(
            f"current_violations_{ranker}={len(current)} "
            f"graphs={len({v.graph for v in current})}"
        )

    if baseline_dot is not None:
        comparable = [r for r in results if r.byte_identical is not None]
        matched = [r for r in comparable if r.byte_identical]
        changed = [r for r in comparable if not r.byte_identical]
        allowed = [
            r
            for r in changed
            if len(r.baseline_violations) > len(r.violations)
        ]
        unexpected = [r for r in changed if r not in allowed]
        unaffected = [r for r in comparable if not r.baseline_violations]
        unaffected_matched = [r for r in unaffected if r.byte_identical]
        print(f"byte_identity_matched={len(matched)}/{len(comparable)}")
        print(
            "byte_identity_unaffected_matched="
            f"{len(unaffected_matched)}/{len(unaffected)}"
        )
        for result in changed:
            status = "removed_violation" if result in allowed else "unexpected_change"
            print(f"CHANGED_OUTPUT {status} {result.ranker} {result.graph}")
            for violation in result.baseline_violations:
                print(format_violation(violation, "  before "))
            for violation in result.violations:
                print(format_violation(violation, "  after "))
        print(f"byte_identity_unexpected_changes={len(unexpected)}")
    else:
        print("byte_identity_matched=not_measured baseline_dot=missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dot", type=Path, default=None)
    parser.add_argument("--baseline-dot", type=Path, default=None)
    parser.add_argument("--graph", action="append", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    dot = args.dot or find_dot(repo)
    baseline_dot = args.baseline_dot or candidate_baseline_dot(repo)
    graphs = (
        [path if path.is_absolute() else repo / path for path in args.graph]
        if args.graph
        else graph_files(repo)
    )

    print(f"commit={commit_sha(repo)}")
    print(f"dot={dot}")
    print(f"baseline_ref={BASELINE_REF}")
    print(f"baseline_dot={baseline_dot if baseline_dot is not None else 'not_found'}")
    print(f"render_concurrency=2")
    print(f"render_address_space_limit_kib={RENDER_LIMIT_KIB}")
    print(
        "invariant=for every cluster C and rendered node or descendant cluster n, "
        "if n is a member of C its box must be fully inside C; if n is not a "
        "member of C its box must not intersect C. Descendant clusters are "
        "members of their ancestor clusters; ancestor cluster boxes are not "
        "tested as non-members of descendants."
    )

    results = run_checks(dot, baseline_dot, repo, graphs, args.timeout)
    print_summary(results, baseline_dot)

    self_peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child_peak_kib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    print(f"peak_memory_kib_self={self_peak_kib}")
    print(f"peak_memory_kib_children={child_peak_kib}")

    total_violations = sum(len(result.violations) for result in results)
    unexpected_changes = [
        result
        for result in results
        if result.byte_identical is False
        and len(result.baseline_violations) <= len(result.violations)
    ]
    if total_violations > 0 or unexpected_changes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
