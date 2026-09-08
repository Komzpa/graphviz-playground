#!/usr/bin/env python3
"""Verify dot does not crash on the frozen junction/concentrate corpus."""

from __future__ import annotations

import concurrent.futures as futures
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


MODES = (
    ("off-default", ("-Gconcentrate=false",)),
    ("off-newrank", ("-Gconcentrate=false", "-Gnewrank=true")),
    ("on-default", ("-Gconcentrate=true",)),
    ("on-newrank", ("-Gconcentrate=true", "-Gnewrank=true")),
)


def _dot_binary() -> str:
    dot = shutil.which("dot")
    if dot is None:
        sys.exit("dot not found in PATH")
    return dot


def _run_one(dot: str, timeout: float, item: tuple[str, tuple[str, ...], Path]):
    mode, flags, path = item
    start = time.monotonic()
    command = [dot, "-Tdot", "-o", os.devnull, *flags, str(path)]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        elapsed = time.monotonic() - start
        rc: int | str = proc.returncode
        status = "ok" if rc == 0 else "signal" if rc < 0 else "rc"
        stderr = proc.stderr.decode("utf-8", "replace").replace("\n", "\\n")
    except subprocess.TimeoutExpired as err:
        elapsed = time.monotonic() - start
        rc = "TIMEOUT"
        status = "timeout"
        stderr = (err.stderr or b"").decode("utf-8", "replace").replace("\n", "\\n")
    return mode, path, rc, status, elapsed, stderr[:500]


def main() -> int:
    corpus_env = os.environ.get("GRAPHVIZ_JUNCTION_CORPUS")
    if corpus_env is None:
        sys.exit("set GRAPHVIZ_JUNCTION_CORPUS to the frozen corpus directory")
    corpus = Path(corpus_env).expanduser()
    if not corpus.is_dir():
        sys.exit(f"corpus directory not found: {corpus}")

    dot = _dot_binary()
    timeout = float(os.environ.get("GRAPHVIZ_JUNCTION_CORPUS_TIMEOUT", "15"))
    jobs = int(os.environ.get("GRAPHVIZ_JUNCTION_CORPUS_JOBS", "16"))
    inputs = sorted(corpus.glob("*.dot"))
    items = [(mode, flags, path) for mode, flags in MODES for path in inputs]

    counts = {
        mode: {"total": 0, "ok": 0, "rc": 0, "signal": 0, "timeout": 0}
        for mode, _ in MODES
    }
    nonzero = []
    with futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for row in executor.map(lambda item: _run_one(dot, timeout, item), items):
            mode, path, rc, status, elapsed, stderr = row
            counts[mode]["total"] += 1
            counts[mode][status] += 1
            if rc != 0:
                nonzero.append(row)

    print(f"dot\t{dot}")
    print(f"corpus\t{corpus}")
    print(f"inputs\t{len(inputs)}")
    print("mode\ttotal\tok\trc\tsignal\ttimeout")
    for mode, _ in MODES:
        count = counts[mode]
        print(
            f"{mode}\t{count['total']}\t{count['ok']}\t{count['rc']}"
            f"\t{count['signal']}\t{count['timeout']}"
        )

    if nonzero:
        print("nonzero")
        print("mode\tinput\trc\tstatus\telapsed_s\tstderr")
        for mode, path, rc, status, elapsed, stderr in nonzero:
            print(f"{mode}\t{path}\t{rc}\t{status}\t{elapsed:.3f}\t{stderr}")
    return 1 if any(row[3] == "signal" for row in nonzero) else 0


if __name__ == "__main__":
    raise SystemExit(main())
