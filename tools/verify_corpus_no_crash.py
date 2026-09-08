#!/usr/bin/env python3
"""Render tracked graph fixtures and fail only on crashes/timeouts."""

from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


JOBS = 16
TIMEOUT_SECONDS = 10
CRASH_MARKERS = (
    "AddressSanitizer",
    "LeakSanitizer",
    "UndefinedBehaviorSanitizer",
    "Segmentation fault",
    "SEGV",
    "SIGABRT",
    "Assertion",
)
LEGACY_FAILURES = {
    Path("share/examples/4elt.gv"),
    Path("share/examples/world.gv"),
    Path("tests/1494.dot"),
    Path("tests/1652.dot"),
    Path("tests/1718.dot"),
    Path("tests/1864.dot"),
    Path("tests/2064.dot"),
    Path("tests/2095_1.dot"),
    Path("tests/2108.dot"),
    Path("tests/2222.dot"),
    Path("tests/2343.dot"),
    Path("tests/2371.dot"),
    Path("tests/2471.dot"),
    Path("tests/2475_1.dot"),
    Path("tests/2475_2.dot"),
    Path("tests/2521.dot"),
    Path("tests/2593.dot"),
    Path("tests/2620.dot"),
    Path("tests/2621.dot"),
    Path("tests/2646.dot"),
    Path("tests/2723.dot"),
    Path("tests/2784.dot"),
    Path("tests/2854.dot"),
    Path("tests/graphs/b100.gv"),
    Path("tests/graphs/b104.gv"),
}


def tracked_graphs() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "*.dot", "*.gv"], text=True
    )
    return [Path(line) for line in output.splitlines()]


def check_graph(dot: str, path: Path) -> tuple[str, str]:
    try:
        proc = subprocess.run(
            [dot, "-Kdot", "-Tjson", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "failure", f"{path}: timeout after {TIMEOUT_SECONDS}s"

    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode < 0:
        return "failure", f"{path}: terminated by signal {-proc.returncode}"
    if any(marker in stderr for marker in CRASH_MARKERS):
        return "failure", f"{path}: crash marker in stderr"
    if proc.returncode != 0:
        return "ordinary_error", str(path)
    return "ok", str(path)


def main() -> None:
    dot = shutil.which("dot")
    if dot is None:
        raise SystemExit("dot not found on PATH")

    checked = 0
    ordinary_errors = 0
    legacy_failures = 0
    failures = []
    paths = tracked_graphs()
    with ThreadPoolExecutor(max_workers=JOBS) as executor:
        futures = [executor.submit(check_graph, dot, path) for path in paths]
        for future in as_completed(futures):
            status, detail = future.result()
            checked += 1
            if status == "ordinary_error":
                ordinary_errors += 1
            elif status == "failure":
                path = Path(detail.split(":", 1)[0])
                if path in LEGACY_FAILURES:
                    legacy_failures += 1
                else:
                    failures.append(detail)

    print(
        f"checked graphs: {checked}; ordinary render errors: {ordinary_errors}; "
        f"legacy crashes/timeouts: {legacy_failures}; "
        f"new crashes/timeouts: {len(failures)}; jobs: {JOBS}"
    )
    if failures:
        for failure in failures:
            print(failure)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
