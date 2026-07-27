#!/usr/bin/env python3
"""Compare corpus layout time against an upstream dot binary."""

from __future__ import annotations

import concurrent.futures as futures
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


MODES = (
    ("absent", ()),
    ("concentrate", ("-Gconcentrate=true",)),
    ("fanin", ("-Gedgejunction=fanin",)),
    ("fanout", ("-Gedgejunction=fanout",)),
    ("both", ("-Gedgejunction=both",)),
)
THRESHOLDS = (5.0, 15.0, 60.0)


def env_path(name: str, required: bool = False) -> str | None:
    value = os.environ.get(name)
    if value:
        return str(Path(value).expanduser())
    if required:
        sys.exit(f"{name} is required")
    return None


def configure_line(label: str, dot: str) -> str:
    env_name = f"GRAPHVIZ_LAYOUT_TIME_{label.upper()}_CONFIGURE"
    configured = os.environ.get(env_name)
    if configured:
        return configured
    path = Path(dot).resolve()
    parts = path.parts
    for index, part in enumerate(parts):
        if part == "cmd" and index > 0:
            build = Path(*parts[:index])
            cache = build / "CMakeCache.txt"
            if cache.is_file():
                return cmake_cache_configure_line(build, cache)
    return "unknown"


def cmake_cache_configure_line(build: Path, cache: Path) -> str:
    values = {}
    for line in cache.read_text(errors="replace").splitlines():
        if line.startswith("//") or ":" not in line or "=" not in line:
            continue
        key_type, value = line.split("=", 1)
        key = key_type.split(":", 1)[0]
        values[key] = value
    source = values.get("CMAKE_HOME_DIRECTORY", ".")
    generator = "Ninja" if values.get("CMAKE_MAKE_PROGRAM", "").endswith("ninja") else ""
    flags = []
    for key in (
        "CMAKE_BUILD_TYPE",
        "BUILD_SHARED_LIBS",
        "BUILD_TESTING",
        "GRAPHVIZ_CLI",
        "CMAKE_C_FLAGS",
        "CMAKE_CXX_FLAGS",
    ):
        if key in values and values[key] != "":
            flags.append(f"-D{key}={values[key]}")
    generator_flag = f" -G {generator!r}" if generator else ""
    return f"cmake -S {source} -B {build}{generator_flag} {' '.join(flags)}".strip()


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        sys.exit(f"{name} must be a number")
    if parsed <= 0:
        sys.exit(f"{name} must be positive")
    return parsed


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        sys.exit(f"{name} must be an integer")
    if parsed < 1:
        sys.exit(f"{name} must be at least 1")
    return parsed


def dot_path() -> str:
    configured = env_path("GRAPHVIZ_LAYOUT_TIME_DOT")
    if configured:
        return configured
    found = shutil.which("dot")
    if found is None:
        sys.exit("dot not found in PATH")
    return found


def corpus_inputs() -> list[Path]:
    corpus = Path(env_path("GRAPHVIZ_LAYOUT_TIME_CORPUS", required=True))
    if not corpus.is_dir():
        sys.exit(f"corpus directory not found: {corpus}")
    return sorted(
        p for p in corpus.rglob("*") if p.suffix in {".dot", ".gv"} and p.is_file()
    )


def run_one(item: tuple[str, tuple[str, ...], str, Path, float]):
    mode, flags, dot, path, timeout = item
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [dot, "-Tdot", "-o", os.devnull, *flags, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        elapsed = time.monotonic() - start
        rc: int | str = proc.returncode
        stderr = proc.stderr.decode("utf-8", "replace").replace("\n", "\\n")[:300]
    except subprocess.TimeoutExpired as err:
        elapsed = time.monotonic() - start
        rc = "TIMEOUT"
        stderr = (err.stderr or b"").decode("utf-8", "replace").replace("\n", "\\n")[:300]
    return mode, path, elapsed, rc, stderr


def measure(label: str, dot: str, inputs: list[Path], timeout: float, jobs: int):
    print(f"measure\t{label}\tdot={dot}\tinputs={len(inputs)}\ttimeout_s={timeout}")
    items = [(mode, flags, dot, path, timeout) for mode, flags in MODES for path in inputs]
    rows = []
    with futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for row in executor.map(run_one, items):
            rows.append(row)
    return rows


def print_counts(label: str, rows) -> None:
    print(f"counts\t{label}")
    print("mode\ttotal\tover_5s\tover_15s\tover_60s\tnonzero")
    for mode, _ in MODES:
        subset = [row for row in rows if row[0] == mode]
        counts = [sum(1 for row in subset if row[2] > threshold) for threshold in THRESHOLDS]
        nonzero = sum(1 for row in subset if row[3] != 0)
        print(f"{mode}\t{len(subset)}\t{counts[0]}\t{counts[1]}\t{counts[2]}\t{nonzero}")


def ratio_report(upstream_rows, current_rows) -> int:
    return ratio_report_for_label("after", upstream_rows, current_rows, 1.5)


def ratio_report_for_label(label: str, upstream_rows, rows, threshold: float) -> int:
    upstream = {(mode, path): elapsed for mode, path, elapsed, rc, _ in upstream_rows if rc == 0}
    failures = []
    ratios = []
    for mode, path, elapsed, rc, stderr in rows:
        base = upstream.get((mode, path))
        if rc != 0 or base is None or base <= 0:
            failures.append((mode, path, rc, elapsed, "missing-upstream" if base is None else stderr))
            continue
        ratio = elapsed / base
        ratios.append((ratio, mode, path, base, elapsed))
        if ratio > threshold:
            failures.append((mode, path, rc, elapsed, f"ratio={ratio:.3f} upstream={base:.3f}"))
    print(f"worst_ratio_to_upstream\t{label}")
    print("ratio\tmode\tupstream_s\tcurrent_s\tinput")
    for ratio, mode, path, base, elapsed in sorted(ratios, reverse=True)[:10]:
        print(f"{ratio:.3f}\t{mode}\t{base:.3f}\t{elapsed:.3f}\t{path.name}")
    if failures:
        print("layout_time_failures")
        print("mode\telapsed_s\trc\tinput\tdetail")
        for mode, path, rc, elapsed, detail in failures[:50]:
            print(f"{mode}\t{elapsed:.3f}\t{rc}\t{path.name}\t{detail}")
    return 1 if failures else 0


def byte_identity_sample(current_dot: str, before_dot: str | None) -> int:
    if before_dot is None:
        print("byte_identity_sample\tskipped\tGRAPHVIZ_LAYOUT_TIME_BEFORE_DOT not set")
        return 0
    base_ref = os.environ.get("GRAPHVIZ_LAYOUT_TIME_BASE_REF", "HEAD^")
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", base_ref, "--", "tests", "graphs"],
        text=True,
    ).splitlines()
    fixtures = []
    for rel in paths:
        if not (rel.endswith(".dot") or rel.endswith(".gv")):
            continue
        text = subprocess.check_output(
            ["git", "show", f"{base_ref}:{rel}"], text=True, errors="ignore"
        )
        if "edgejunction" in text:
            continue
        fixtures.append(rel)
        if len(fixtures) == 60:
            break
    failures_by_mode = {mode: 0 for mode, _ in MODES}
    root = Path.cwd()
    before_root = Path(os.environ.get("GRAPHVIZ_LAYOUT_TIME_BEFORE_ROOT", root))
    for rel in fixtures:
        for mode, flags in MODES:
            old = subprocess.run([before_dot, *flags, "-Txdot", str(before_root / rel)], stdout=subprocess.PIPE)
            new = subprocess.run([current_dot, *flags, "-Txdot", str(root / rel)], stdout=subprocess.PIPE)
            failures_by_mode[mode] += old.returncode != new.returncode or old.stdout != new.stdout
    print(f"byte_identity_sample\tbase={base_ref}\tfixtures={len(fixtures)}")
    print("mode\tmatched\ttotal")
    for mode, _ in MODES:
        failures = failures_by_mode[mode]
        print(f"{mode}\t{len(fixtures) - failures}\t{len(fixtures)}")
    total = len(fixtures) * len(MODES)
    failures = sum(failures_by_mode.values())
    return 1 if failures else 0


def print_buildability_report() -> None:
    path = env_path("GRAPHVIZ_LAYOUT_TIME_BUILDABILITY_REPORT")
    if path is None:
        print("standalone_buildability\tskipped\tGRAPHVIZ_LAYOUT_TIME_BUILDABILITY_REPORT not set")
        return
    report = Path(path)
    if not report.is_file():
        print(f"standalone_buildability\tmissing\t{report}")
        return
    print("standalone_buildability")
    for line in report.read_text(errors="replace").splitlines():
        print(line)


def main() -> int:
    inputs = corpus_inputs()
    timeout = env_float("GRAPHVIZ_LAYOUT_TIME_TIMEOUT", 180.0)
    jobs = env_int("GRAPHVIZ_LAYOUT_TIME_JOBS", 8)
    upstream_dot = env_path("GRAPHVIZ_LAYOUT_TIME_UPSTREAM_DOT", required=True)
    current_dot = dot_path()
    before_dot = env_path("GRAPHVIZ_LAYOUT_TIME_BEFORE_DOT")

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    print(f"commit\t{commit}")
    print(f"configure\tupstream\t{configure_line('upstream', upstream_dot)}")
    if before_dot:
        print(f"configure\tbefore\t{configure_line('before', before_dot)}")
    print(f"configure\tafter\t{configure_line('after', current_dot)}")
    if bisect_result := os.environ.get("GRAPHVIZ_LAYOUT_TIME_BISECT_RESULT"):
        print(f"bisect_result\t{bisect_result}")
    if profile_evidence := os.environ.get("GRAPHVIZ_LAYOUT_TIME_PROFILE_EVIDENCE"):
        print(f"profile_evidence\t{profile_evidence}")

    before_rows = measure("before", before_dot, inputs, timeout, jobs) if before_dot else []
    upstream_rows = measure("upstream", upstream_dot, inputs, timeout, jobs)
    current_rows = measure("after", current_dot, inputs, timeout, jobs)
    if before_rows:
        print_counts("before", before_rows)
    print_counts("after", current_rows)
    rc = 0
    if before_rows:
        rc |= ratio_report_for_label("before", upstream_rows, before_rows, float("inf"))
    rc |= ratio_report(upstream_rows, current_rows)
    rc |= byte_identity_sample(current_dot, before_dot)
    print_buildability_report()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
