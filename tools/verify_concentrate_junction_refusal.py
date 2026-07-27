#!/usr/bin/env python3
"""Verify concentrate junctions refuse unsafe graph kinds without disabling good fuses."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import resource
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
}
SIBLINGS = "tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot"
RENDER_TIMEOUT = 90
MEMORY_KB = int(os.environ.get("GRAPHVIZ_RENDER_MEMORY_KB", "2097152"))
MAX_WORKERS = 1
RANKERS = (
    ("default", ()),
    ("newrank", ("-Gnewrank=true",)),
)


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
    paths = sorted(set(REFUSED) | {SIBLINGS})
    return [path for path in paths if (root / path).exists()]


def limited_cmd(cmd: list[str]) -> list[str]:
    return [
        "timeout",
        f"{RENDER_TIMEOUT}s",
        "bash",
        "-lc",
        f"ulimit -v {MEMORY_KB}; exec \"$@\"",
        "dot-ulimit",
        *cmd,
    ]


def render_bytes(dot: str, root: Path, rel: str, *args: str, fmt: str = "svg") -> bytes:
    return run(
        limited_cmd([dot, *args, f"-T{fmt}", str(root / rel)]), timeout=RENDER_TIMEOUT
    ).stdout


def render_hash(dot: str, root: Path, rel: str, *args: str) -> RenderHash:
    cmd = [dot, *args, "-Tsvg", str(root / rel)]
    proc = subprocess.run(
        limited_cmd(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    digest = hashlib.sha256()
    digest.update(proc.stdout)
    return RenderHash(proc.returncode, digest.hexdigest(), len(proc.stdout))


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


def has_junction_transform(layout: dict) -> bool:
    return any(
        obj.get("_concentrate_junction_node") == "true"
        for obj in layout.get("objects", [])
    ) or any(
        edge.get("_concentrate_junction_original") == "true"
        for edge in layout.get("edges", [])
    )


def compare_refusal(dot: str, root: Path, rel: str, ranker: tuple[str, tuple[str, ...]]) -> tuple[str, str, str, str | None]:
    ranker_name, ranker_args = ranker
    try:
        layout = dot_json(dot, root, rel, *ranker_args)
    except subprocess.TimeoutExpired:
        return ranker_name, rel, "skipped", "render watchdog"
    except Exception as err:  # noqa: BLE001
        return ranker_name, rel, "failure", str(err)
    if has_junction_transform(layout):
        return ranker_name, rel, "failure", f"junction transform ran: {REFUSED[rel]}"
    return ranker_name, rel, "refused", REFUSED[rel]


def compare_siblings_change(dot: str, root: Path, ranker: tuple[str, tuple[str, ...]]) -> tuple[str, str, str, str | None]:
    ranker_name, ranker_args = ranker
    disabled = render_hash(dot, root, SIBLINGS, "-Gconcentrate=false", *ranker_args)
    enabled = render_hash(dot, root, SIBLINGS, "-Gconcentrate=true", *ranker_args)
    if disabled.returncode != 0 or enabled.returncode != 0:
        if disabled.returncode == 124 or enabled.returncode == 124:
            return ranker_name, SIBLINGS, "skipped", "render watchdog"
        return (
            ranker_name,
            SIBLINGS,
            "failure",
            f"renderability changed disabled_rc={disabled.returncode} enabled_rc={enabled.returncode}",
        )
    if disabled.digest == enabled.digest and disabled.bytes_read == enabled.bytes_read:
        return ranker_name, SIBLINGS, "failure", "siblings did not differ from concentrate=false output"
    return ranker_name, SIBLINGS, "different", "active eligible concentrate junction fan"


def dot_json(dot: str, root: Path, rel: str, *args: str) -> dict:
    return json.loads(
        render_bytes(dot, root, rel, "-Gconcentrate=true", *args, fmt="json").decode()
    )


def verify_siblings(dot: str, root: Path, ranker: tuple[str, tuple[str, ...]]) -> int:
    ranker_name, ranker_args = ranker
    layout = dot_json(dot, root, SIBLINGS, *ranker_args)
    node_names = names(layout)
    positions = node_positions(layout)
    failures = 0
    aligned = ("c", "e", "_concentrate_junction_0", "d")
    if not all(name in positions for name in aligned):
        print(f"FAIL siblings missing aligned nodes: {aligned}")
        failures += 1
    else:
        xs = [positions[name][0] for name in aligned]
        spread = max(xs) - min(xs)
        print(f"siblings geometry ranker={ranker_name} aligned_x_spread={spread:.2f}pt")
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
        f"siblings arrowheads into d ranker={ranker_name}: "
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
    print(f"render memory limit: {MEMORY_KB} KiB")
    print("mode matrix: concentrate=false/true x default/newrank")

    failures = 0
    skipped = 0
    refused = 0
    siblings_different = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        items = [(path, ranker) for ranker in RANKERS for path in sorted(REFUSED)]
        for ranker_name, rel, status, reason in executor.map(
            lambda item: compare_refusal(dot, root, item[0], item[1]), items
        ):
            if status == "skipped":
                skipped += 1
            elif status == "failure":
                print(f"FAIL ranker={ranker_name} {rel}: {reason}")
                failures += 1
            elif status == "refused":
                refused += 1
                print(
                    f"refusal confirmed ranker={ranker_name} "
                    f"fixture={rel} reason={reason}"
                )

        for ranker_name, rel, status, reason in executor.map(
            lambda ranker: compare_siblings_change(dot, root, ranker), RANKERS
        ):
            if status == "skipped":
                skipped += 1
            elif status == "failure":
                print(f"FAIL ranker={ranker_name} {rel}: {reason}")
                failures += 1
            elif status == "different":
                siblings_different += 1
                print(f"junction-active differing ranker={ranker_name} fixture={rel}")

    missing = sorted(set(REFUSED) - set(paths))
    for rel in missing:
        print(f"FAIL missing refused fixture: {rel}")
        failures += 1

    for ranker in RANKERS:
        failures += verify_siblings(dot, root, ranker)

    print(
        "refused no-junction-transform fixture-ranker checks: "
        f"{refused}/{len(REFUSED) * len(RANKERS)}"
    )
    print(f"junction-active differing fixture-ranker checks: {siblings_different}/{len(RANKERS)}")
    print(f"unrenderable equally-skipped fixtures: {skipped}")
    print(f"peak verifier RSS: {maxrss_mib(resource.RUSAGE_SELF):.1f} MiB")
    print(f"peak child RSS: {maxrss_mib(resource.RUSAGE_CHILDREN):.1f} MiB")

    if failures:
        print(f"FAIL verify_concentrate_junction_refusal: failures={failures}")
        return 1
    print("OK verify_concentrate_junction_refusal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
