#!/usr/bin/env python3
"""Does concentrate cost more page than it saves?

`concentrate` exists to make a dense graph readable. If switching it on makes the
drawing BIGGER than leaving it off, the feature has spent the reader's paper and
given nothing back.

This measurement did not exist for two weeks and the failure was invisible to
every other gate in this tree — corners, overlaps, arrow aim, arm order, rank
counts all measure INK, and none of them measures paper. The operator graded
`rowe` broken with concentrate while grading its BASE ideal, and nothing could
say why: our concentrate draws it 2.43x larger than our own concentrate=false.

Compare a build against ITSELF with the flag off, never against upstream:
upstream's concentrate collapses edges wrongly and so draws smaller for a bad
reason. Ours-versus-upstream said "87 fixtures larger" and could not tell our
regression apart from their bug; each-build-against-its-own-false said 79 for us
and 2 for upstream, which is the answer.

Usage:
  verify_concentrate_page_cost.py [--dot BINARY] [--max-ratio 1.05]
                                  [--budget N] [FIXTURE ...]

Exits non-zero when more than --budget fixtures exceed --max-ratio.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 200
NONDETERMINISTIC = {"Latin1.gv", "Symbol.gv", "b34.gv", "b60.gv"}


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def area(binary: str, path: str, flags: list[str]) -> float | None:
    """Drawing area in square inches, from the `graph` line of -Tplain."""
    try:
        out = subprocess.run([binary, "-Tplain", *flags, path],
                             capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        f = line.split()
        if f and f[0] == "graph" and len(f) >= 4:
            try:
                return float(f[2]) * float(f[3])
            except ValueError:
                return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dot", default=None)
    ap.add_argument("--max-ratio", type=float, default=1.05)
    ap.add_argument("--budget", type=int, default=10)
    ap.add_argument("fixtures", nargs="*")
    args = ap.parse_args()

    root = repo_root()
    dot = args.dot or os.path.join(root, "build", "cmd", "dot", "dot_builtins")
    files = args.fixtures or sorted(set(
        glob.glob(f"{root}/tests/graphs/*.gv")
        + glob.glob(f"{root}/graphs/directed/*.gv")
        + glob.glob(f"{root}/graphs/undirected/*.gv")))
    files = [f for f in files if os.path.basename(f) not in NONDETERMINISTIC]

    def one(path: str):
        off = area(dot, path, ["-Gconcentrate=false"])
        on = area(dot, path, ["-Gconcentrate=true"])
        return os.path.basename(path), off, on

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(one, files))

    inflated = [(on / off, n, off, on) for n, off, on in rows
                if off and on and on > off * args.max_ratio]
    inflated.sort(reverse=True)

    print(f"fixtures_measured={sum(1 for _, o, n in rows if o and n)}")
    print(f"inflated_fixtures={len(inflated)}")
    for r, n, off, on in inflated[:25]:
        print(f"  x{r:.2f}  {n}  {off:.1f} -> {on:.1f} sq in")
    if len(inflated) > args.budget:
        print(f"FAILED verify_concentrate_page_cost: {len(inflated)} fixtures draw "
              f"larger with concentrate on than off (budget {args.budget})")
        return 1
    print("OK verify_concentrate_page_cost")
    return 0


if __name__ == "__main__":
    sys.exit(main())
