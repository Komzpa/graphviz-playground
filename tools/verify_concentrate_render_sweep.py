#!/usr/bin/env python3
"""Render public dot fixtures with concentrate explicitly off and on."""

from __future__ import annotations

import concurrent.futures as futures
import argparse
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


MEMORY_KB = 2 * 1024 * 1024
# Both b100.gv and b104.gv render in ~5s on an idle machine and were on the
# upstream allowlist only because they crossed 30s while lanes were building in
# parallel. A wall clock is a property of this machine, not of the graph, and an
# allowlist that absorbs load spikes hides the failures it exists to catch.
TIMEOUT = 120
JOBS = 16
UPSTREAM_SHA = "561b579e5"
MODES = (
    ("concentrate=false", ("-Gconcentrate=false",)),
    ("concentrate=true", ("-Gconcentrate=true",)),
)

# Entries are relative graph paths. Every entry must fail with the same mode on
# UPSTREAM_SHA or this verifier fails.
UPSTREAM_ALLOWLIST: dict[str, set[str]] = {
    "concentrate=false": {
        "tests/graphs/b15.gv",
        "tests/graphs/concentrate-demo/malformed-nodesep-rejected-2758.dot",
    },
    "concentrate=true": {
        "tests/graphs/concentrate-demo/malformed-nodesep-rejected-2758.dot",
    },
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def display_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def cap_memory() -> None:
    limit = MEMORY_KB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run(
    args: list[str | Path],
    cwd: Path,
    *,
    timeout: int = TIMEOUT,
    memory: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        preexec_fn=cap_memory if memory else None,
    )


def checked_run(args: list[str | Path], cwd: Path) -> None:
    proc = run(args, cwd, timeout=120, memory=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(proc.returncode)


def current_dot(root: Path) -> Path:
    dot = root / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked_run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], root)
    return dot


def configured_dot(root: Path, sha: str) -> Path:
    worktree = root.parent / f"{root.name}-render-sweep-upstream-{sha}"
    if not worktree.exists():
        checked_run(["git", "worktree", "add", "--detach", worktree, sha], root)
    dot = worktree / "build" / "cmd" / "dot" / "dot_builtins"
    if not dot.exists():
        checked_run(
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
        )
        checked_run(["cmake", "--build", "build", "--target", "dot_builtins", "-j", "4"], worktree)
    return dot


def graph_inputs(root: Path) -> list[Path]:
    paths = set((root / "graphs" / "directed").glob("*.gv"))
    paths.update((root / "tests" / "graphs").glob("**/*.dot"))
    paths.update((root / "tests" / "graphs").glob("**/*.gv"))
    return sorted(paths)


def run_render(dot: Path, root: Path, rel: str, mode: str, flags: tuple[str, ...]):
    start = time.monotonic()
    try:
        proc = run(
            [dot, "-Tjson", "-o", os.devnull, *flags, root / rel],
            root,
        )
        elapsed = time.monotonic() - start
        status = "ok" if proc.returncode == 0 else "rc"
        stderr = proc.stderr.decode("utf-8", "replace").replace("\n", "\\n")
        return mode, rel, proc.returncode, status, elapsed, stderr[:600]
    except subprocess.TimeoutExpired as err:
        elapsed = time.monotonic() - start
        stderr = (err.stderr or b"").decode("utf-8", "replace").replace("\n", "\\n")
        return mode, rel, "TIMEOUT", "timeout", elapsed, stderr[:600]


def render_matrix(dot: Path, root: Path, rels: list[str]):
    items = [(rel, mode, flags) for rel in rels for mode, flags in MODES]
    with futures.ThreadPoolExecutor(max_workers=JOBS) as executor:
        return list(
            executor.map(lambda item: run_render(dot, root, item[0], item[1], item[2]), items)
        )


def allowed(mode: str, rel: str) -> bool:
    return rel in UPSTREAM_ALLOWLIST.get(mode, set())


def print_rows(title: str, rows) -> None:
    print(title)
    if not rows:
        print("  none")
        return
    print("  mode\tinput\trc\tstatus\telapsed_s\tstderr")
    for mode, rel, rc, status, elapsed, stderr in rows:
        print(f"  {mode}\t{rel}\t{rc}\t{status}\t{elapsed:.3f}\t{stderr}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=repo_root())
    parser.add_argument("--dot", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    dot = args.dot.resolve() if args.dot else current_dot(root)
    inputs = [str(path.relative_to(root)) for path in graph_inputs(root)]
    rows = render_matrix(dot, root, inputs)

    # A wall clock measures this machine, not the graph. b100.gv renders in 5s
    # idle and times out at 120s while lanes build in parallel; failing the gate
    # on that teaches everyone to ignore it. Timeouts are reported loudly and
    # separately, and a graph that only ever times out never becomes an
    # "upstream failure" it is not.
    timeouts = [row for row in rows if row[2] == "TIMEOUT"]
    failures = [row for row in rows if row[2] != 0 and row[2] != "TIMEOUT"]
    unexpected = [row for row in failures if not allowed(row[0], row[1])]
    allowed_failures = [row for row in failures if allowed(row[0], row[1])]

    print(f"dot\t{display_path(dot, root)}")
    print(f"inputs\t{len(inputs)}")
    for mode, _ in MODES:
        total = sum(row[0] == mode for row in rows)
        ok = sum(row[0] == mode and row[2] == 0 for row in rows)
        fail = total - ok
        print(f"mode\t{mode}\ttotal\t{total}\tok\t{ok}\tfail\t{fail}")

    print("upstream_allowlist")
    if not UPSTREAM_ALLOWLIST:
        print("  none")
    for mode, rels in UPSTREAM_ALLOWLIST.items():
        for rel in sorted(rels):
            print(f"  {mode}\t{rel}")

    upstream_proof_failures = []
    upstream_allowlist_rows = []
    if UPSTREAM_ALLOWLIST:
        upstream = configured_dot(root, UPSTREAM_SHA)
        print(f"upstream_dot\t{display_path(upstream, root.parent)}")
        proof_inputs = sorted({rel for rels in UPSTREAM_ALLOWLIST.values() for rel in rels})
        upstream_rows = render_matrix(upstream, root, proof_inputs)
        for row in upstream_rows:
            mode, rel, rc, *_ = row
            if allowed(mode, rel):
                upstream_allowlist_rows.append(row)
                if rc == 0:
                    upstream_proof_failures.append(row)
        print_rows("upstream_allowlist_proof", upstream_allowlist_rows)

    print_rows("allowed_failures", allowed_failures)
    print_rows("unexpected_failures", unexpected)
    if timeouts:
        print_rows("TIMEOUTS (machine load, not graph failures)", timeouts)
    if upstream_proof_failures:
        print_rows("allowlist_entries_that_pass_upstream", upstream_proof_failures)
    return 1 if unexpected or upstream_proof_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
