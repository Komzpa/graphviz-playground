#!/usr/bin/env python3
"""Verify edgejunction refuses unsafe graph kinds without disabling good fuses."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import resource
import selectors
import shutil
import subprocess
import sys
import time


REFUSED = {
    "tests/graphs/concentrate-demo/curved-concentrated-attributed-chain.dot":
        "curved concentrated attributed parallel edges",
    "tests/graphs/concentrate-demo/curved-concentrated-attributed-parallel.dot":
        "curved concentrated attributed parallel edges",
    "graphs/directed/longflat.gv": "flat same-rank edge",
    "tests/graphs/concentrate-demo/self-loop-label-beside-loop.dot": "self loop",
    "graphs/directed/record2.gv": "record endpoint",
    "graphs/directed/honda-tokoro.gv": "port endpoint",
    "tests/drbd-anchor.dot": "labelled concentrated fan",
}
SIBLINGS = "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"
RENDER_TIMEOUT = 90
MAX_WORKERS = 1


@dataclass(frozen=True)
class RenderHash:
    returncode: int
    digest: str
    bytes_read: int


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, **kwargs
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")
        raise RuntimeError(f"command failed rc={proc.returncode}: {' '.join(cmd)}\n{stderr}")
    return proc


def dot_path(root: Path) -> str:
    built = root / "build" / "cmd" / "dot" / "dot_builtins"
    if built.exists():
        return str(built)
    dot = shutil.which("dot")
    if dot is None:
        raise RuntimeError("dot not found; build dot_builtins or put dot on PATH")
    return dot


def corpus_paths(root: Path) -> list[str]:
    paths = run(["git", "ls-files", "graphs", "tests"], cwd=root).stdout.decode()
    return sorted(path for path in paths.splitlines() if path.endswith((".dot", ".gv")))


def render_bytes(dot: str, root: Path, rel: str, *args: str, fmt: str = "svg") -> bytes:
    return run(
        [dot, *args, f"-T{fmt}", str(root / rel)], timeout=RENDER_TIMEOUT
    ).stdout


def render_hash(dot: str, root: Path, rel: str, *args: str) -> RenderHash:
    cmd = [dot, *args, "-Tsvg", str(root / rel)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    os.set_blocking(proc.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + RENDER_TIMEOUT
    digest = hashlib.sha256()
    bytes_read = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            proc.wait()
            selector.close()
            return RenderHash(124, "", bytes_read)
        for key, _ in selector.select(timeout=min(remaining, 1.0)):
            chunk = key.fileobj.read(1024 * 1024)
            if chunk:
                bytes_read += len(chunk)
                digest.update(chunk)
            elif proc.poll() is not None:
                selector.close()
                return RenderHash(proc.returncode, digest.hexdigest(), bytes_read)
        if proc.poll() is not None:
            chunk = proc.stdout.read()
            if chunk:
                bytes_read += len(chunk)
                digest.update(chunk)
            selector.close()
            return RenderHash(proc.returncode, digest.hexdigest(), bytes_read)


def names(layout: dict) -> dict[int, str]:
    return {int(obj["_gvid"]): obj["name"] for obj in layout.get("objects", [])}


def node_positions(layout: dict) -> dict[str, tuple[float, float]]:
    positions = {}
    for obj in layout.get("objects", []):
        pos = obj.get("pos")
        if pos is None:
            continue
        x, y = pos.split(",", 1)
        positions[obj["name"]] = (float(x), float(y))
    return positions


def arrowheads(edge: dict, stream: str) -> int:
    return sum(1 for op in edge.get(stream, []) if op.get("op") in {"P", "p"})


def compare_fixture(dot: str, root: Path, rel: str) -> tuple[str, str, str | None]:
    absent = render_hash(dot, root, rel)
    both = render_hash(dot, root, rel, "-Gedgejunction=both")
    if absent.returncode != 0 or both.returncode != 0:
        if absent.returncode == 124 or both.returncode == 124:
            return rel, "skipped", "render watchdog"
        if absent.returncode == both.returncode:
            return rel, "skipped", None
        return (
            rel,
            "failure",
            f"renderability changed absent_rc={absent.returncode} both_rc={both.returncode}",
        )
    if absent.digest == both.digest and absent.bytes_read == both.bytes_read:
        return rel, "same", None

    if rel in REFUSED:
        return rel, "failure", f"refused fixture differs: {REFUSED[rel]}"
    return rel, "different", "output changed under edgejunction=both"


def dot_json(dot: str, root: Path, rel: str) -> dict:
    return json.loads(
        render_bytes(dot, root, rel, "-Gedgejunction=both", fmt="json").decode()
    )


def verify_siblings(dot: str, root: Path) -> int:
    layout = dot_json(dot, root, SIBLINGS)
    node_names = names(layout)
    positions = node_positions(layout)
    failures = 0
    aligned = ("c", "e", "_edgejunction_0", "d")
    if not all(name in positions for name in aligned):
        print(f"FAIL siblings missing aligned nodes: {aligned}")
        failures += 1
    else:
        xs = [positions[name][0] for name in aligned]
        spread = max(xs) - min(xs)
        print(f"siblings aligned x spread: {spread:.2f}pt")
        if spread > 1.0:
            print(f"FAIL siblings x spread exceeds 1pt: {spread:.2f}pt")
            failures += 1

    by_color = Counter()
    total = 0
    for edge in layout.get("edges", []):
        if node_names[int(edge["head"])] != "d":
            continue
        count = arrowheads(edge, "_hdraw_")
        total += count
        by_color[edge.get("color", "black")] += count
    print(
        "siblings arrowheads into d: "
        f"total={total} black={by_color['black']} blue={by_color['blue']} red={by_color['red']}"
    )
    if total != 3 or by_color["black"] != 1 or by_color["blue"] != 1 or by_color["red"] != 1:
        print("FAIL siblings expected exactly one black, blue, and red arrowhead into d")
        failures += 1
    return failures


def maxrss_mib(who: int) -> float:
    return resource.getrusage(who).ru_maxrss / 1024.0


def main() -> int:
    root = repo_root()
    dot = dot_path(root)
    print(f"dot: {dot}")
    paths = corpus_paths(root)
    print(f"corpus fixtures: {len(paths)}")
    print(f"max concurrent fixture workers: {MAX_WORKERS}")

    failures = 0
    differing: list[tuple[str, str]] = []
    skipped = 0
    refused_same = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for rel, status, reason in executor.map(
            lambda path: compare_fixture(dot, root, path), paths
        ):
            if status == "skipped":
                skipped += 1
            elif status == "failure":
                print(f"FAIL {rel}: {reason}")
                failures += 1
            elif status == "same":
                if rel in REFUSED:
                    refused_same += 1
            elif status == "different":
                differing.append((rel, reason or "active eligible edgejunction fan"))

    missing = sorted(set(REFUSED) - set(paths))
    for rel in missing:
        print(f"FAIL missing refused fixture: {rel}")
        failures += 1

    if SIBLINGS not in [rel for rel, _ in differing]:
        print(f"FAIL siblings fixture did not differ from absent output: {SIBLINGS}")
        failures += 1
    failures += verify_siblings(dot, root)

    print(f"refused byte-identical fixtures: {refused_same}/{len(REFUSED)}")
    print(f"edgejunction-active differing fixtures: {len(differing)}")
    print(f"unrenderable equally-skipped fixtures: {skipped}")
    print(f"peak verifier RSS: {maxrss_mib(resource.RUSAGE_SELF):.1f} MiB")
    print(f"peak child RSS: {maxrss_mib(resource.RUSAGE_CHILDREN):.1f} MiB")
    for rel, reason in differing:
        print(f"DIFF {rel}: {reason}")

    if failures:
        print(f"FAIL verify_edgejunction_refusal: failures={failures}")
        return 1
    print("OK verify_edgejunction_refusal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
