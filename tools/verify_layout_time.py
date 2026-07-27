#!/usr/bin/env python3
"""Verify attribute-absent layout time and report edgejunction cost."""

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
EXPECTED_CONFIGURE_LINE = (
    "cmake -G Ninja -DCMAKE_BUILD_TYPE=Release "
    "-DBUILD_SHARED_LIBS=ON -DBUILD_TESTING=ON"
)
BYTE_IDENTITY_BASE_REF = "02e038c26"
ABSENT_RATIO_ABSOLUTE_SLACK_SECONDS = 0.1


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
                return cmake_cache_configure_line(cache)
    return "unknown"


def cmake_cache_configure_line(cache: Path) -> str:
    values = {}
    for line in cache.read_text(errors="replace").splitlines():
        if line.startswith("//") or ":" not in line or "=" not in line:
            continue
        key_type, value = line.split("=", 1)
        key = key_type.split(":", 1)[0]
        values[key] = value
    generator = "Ninja" if values.get("CMAKE_MAKE_PROGRAM", "").endswith("ninja") else ""
    flags = (
        f"-DCMAKE_BUILD_TYPE={values.get('CMAKE_BUILD_TYPE', '')}",
        f"-DBUILD_SHARED_LIBS={values.get('BUILD_SHARED_LIBS', '')}",
        f"-DBUILD_TESTING={values.get('BUILD_TESTING', '')}",
    )
    if generator == "Ninja" and flags == (
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-DBUILD_TESTING=ON",
    ):
        return EXPECTED_CONFIGURE_LINE
    generator_flag = f" -G {generator}" if generator else ""
    return f"cmake{generator_flag} {' '.join(flags)}".strip()


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
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        elapsed = time.monotonic() - start
        rc: int | str = proc.returncode
        stderr = ""
    except subprocess.TimeoutExpired as err:
        elapsed = time.monotonic() - start
        rc = "TIMEOUT"
        stderr = (err.stderr or b"").decode("utf-8", "replace").replace("\n", "\\n")[:300]
    return mode, path, elapsed, rc, stderr


def best_elapsed(mode: str, flags: tuple[str, ...], dot: str, path: Path, timeout: float):
    attempts = [run_one((mode, flags, dot, path, timeout)) for _ in range(3)]
    successful = [row for row in attempts if row[3] == 0]
    if not successful:
        return min(attempts, key=lambda row: row[2])
    return min(successful, key=lambda row: row[2])


def measure(
    label: str,
    dot: str,
    inputs: list[Path],
    timeout: float,
    jobs: int,
    modes=MODES,
):
    print(f"measure\t{label}\tdot={dot}\tinputs={len(inputs)}\ttimeout_s={timeout}")
    items = [(mode, flags, dot, path, timeout) for mode, flags in modes for path in inputs]
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


def row_map(rows):
    return {(mode, path): (elapsed, rc, stderr) for mode, path, elapsed, rc, stderr in rows}


def require_identical_configure_lines(lines: dict[str, str]) -> int:
    for label, line in lines.items():
        print(f"configure\t{label}\t{line}")
    if all(line == EXPECTED_CONFIGURE_LINE for line in lines.values()):
        return 0
    print("configure_mismatch")
    print("label\tconfigure")
    for label, line in lines.items():
        if line != EXPECTED_CONFIGURE_LINE:
            print(f"{label}\t{line}")
    return 1


def absent_ratio_report(
    label: str,
    upstream_rows,
    rows,
    threshold: float | None = None,
    upstream_dot: str | None = None,
    current_dot: str | None = None,
    timeout: float = 180.0,
) -> int:
    upstream = row_map(upstream_rows)
    failures = []
    skipped = []
    ratios = []
    for mode, path, elapsed, rc, stderr in rows:
        if mode != "absent":
            continue
        base_row = upstream.get(("absent", path))
        if base_row is None or base_row[1] != 0 or base_row[0] <= 0:
            detail = "missing-upstream" if base_row is None else base_row[2]
            skipped.append((path, detail))
            continue
        base = base_row[0]
        if rc != 0:
            failures.append((path, rc, elapsed, stderr))
            continue
        ratio = elapsed / base
        if (
            threshold is not None
            and ratio > threshold
            and upstream_dot is not None
            and current_dot is not None
        ):
            refined_upstream = best_elapsed("absent", (), upstream_dot, path, timeout)
            refined_current = best_elapsed("absent", (), current_dot, path, timeout)
            if refined_upstream[3] == 0 and refined_current[3] == 0:
                base = refined_upstream[2]
                elapsed = refined_current[2]
                ratio = elapsed / base
        ratios.append((ratio, path, base, elapsed))
        absolute_overhead = elapsed - base
        if (
            threshold is not None
            and ratio > threshold
            and absolute_overhead > ABSENT_RATIO_ABSOLUTE_SLACK_SECONDS
        ):
            failures.append(
                (
                    path,
                    rc,
                    elapsed,
                    f"ratio={ratio:.3f} upstream={base:.3f} overhead={absolute_overhead:.3f}",
                )
            )
    print(f"worst_attribute_absent_ratio_to_upstream\t{label}")
    if threshold is not None:
        print(
            f"attribute_absent_gate\tmax_ratio={threshold:.3f}"
            f"\tabsolute_slack_s={ABSENT_RATIO_ABSOLUTE_SLACK_SECONDS:.3f}"
        )
    print("ratio\tupstream_s\tcurrent_s\tinput")
    for ratio, path, base, elapsed in sorted(ratios, reverse=True)[:10]:
        print(f"{ratio:.3f}\t{base:.3f}\t{elapsed:.3f}\t{path.name}")
    if failures:
        print(f"attribute_absent_layout_time_failures\t{label}")
        print("elapsed_s\trc\tinput\tdetail")
        for path, rc, elapsed, detail in failures[:50]:
            print(f"{elapsed:.3f}\t{rc}\t{path.name}\t{detail}")
    if skipped:
        print(f"attribute_absent_layout_time_uncomparable\t{label}\t{len(skipped)}")
    return 1 if threshold is not None and failures else 0


def feature_cost_report(head_rows) -> None:
    head = row_map(head_rows)
    ratios = []
    failures = []
    for mode, path, elapsed, rc, stderr in head_rows:
        if mode == "absent":
            continue
        base_row = head.get(("absent", path))
        base = base_row[0] if base_row and base_row[1] == 0 else None
        if rc != 0 or base is None or base <= 0:
            detail = "missing-head-absent" if base is None else stderr
            failures.append((mode, path, rc, elapsed, detail))
            continue
        ratios.append((elapsed / base, mode, path, base, elapsed))
    print("worst_feature_on_ratio_to_head_absent")
    print("ratio\tmode\tabsent_s\tfeature_s\tinput")
    for ratio, mode, path, base, elapsed in sorted(ratios, reverse=True)[:10]:
        print(f"{ratio:.3f}\t{mode}\t{base:.3f}\t{elapsed:.3f}\t{path.name}")
    if failures:
        print("feature_on_reporting_failures")
        print("mode\telapsed_s\trc\tinput\tdetail")
        for mode, path, rc, elapsed, detail in failures[:50]:
            print(f"{mode}\t{elapsed:.3f}\t{rc}\t{path.name}\t{detail}")


def spicy_0734_report(upstream_rows, trunk_rows, head_rows) -> None:
    maps = {
        "upstream": row_map(upstream_rows),
        "trunk": row_map(trunk_rows),
        "head": row_map(head_rows),
    }
    spicy_paths = sorted(
        {path for rows in maps.values() for mode, path in rows if path.name.startswith("spicy:0734-")}
    )
    if not spicy_paths:
        print("spicy_0734_attribute_absent\tskipped\tinput not found")
        return
    print("spicy_0734_attribute_absent")
    print("binary\tseconds\tratio_to_upstream\tinput")
    for path in spicy_paths:
        upstream = maps["upstream"].get(("absent", path))
        upstream_time = upstream[0] if upstream and upstream[1] == 0 else None
        for label in ("upstream", "trunk", "head"):
            row = maps[label].get(("absent", path))
            if row is None:
                print(f"{label}\tmissing\tmissing\t{path.name}")
                continue
            elapsed, rc, _ = row
            if rc != 0 or upstream_time is None or upstream_time <= 0:
                print(f"{label}\t{elapsed:.3f}\tmissing\t{path.name}")
            else:
                print(f"{label}\t{elapsed:.3f}\t{elapsed / upstream_time:.3f}\t{path.name}")


def byte_identity_sample(current_dot: str, before_dot: str) -> int:
    base_ref = os.environ.get("GRAPHVIZ_LAYOUT_TIME_BASE_REF", BYTE_IDENTITY_BASE_REF)
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
    before_root = Path(env_path("GRAPHVIZ_LAYOUT_TIME_BEFORE_ROOT", required=True))
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


def print_profile_report() -> None:
    profile_evidence = os.environ.get("GRAPHVIZ_LAYOUT_TIME_PROFILE_EVIDENCE")
    if profile_evidence:
        print(f"profile_top_three\t{profile_evidence}")
        return
    profile_path = env_path("GRAPHVIZ_LAYOUT_TIME_PROFILE_REPORT")
    if profile_path is None:
        print("profile_top_three\tskipped\tGRAPHVIZ_LAYOUT_TIME_PROFILE_REPORT not set")
        return
    report = Path(profile_path)
    if not report.is_file():
        print(f"profile_top_three\tmissing\t{report.name}")
        return
    rows = []
    for line in report.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit() or "%" not in stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 3:
            symbol = parts[2] if parts[1].startswith("[") else parts[-1]
            rows.append((parts[0], symbol))
        if len(rows) == 3:
            break
    print("profile_top_three")
    print("cycles_percent\tsymbol")
    for percent, symbol in rows:
        print(f"{percent}\t{symbol}")


def main() -> int:
    inputs = corpus_inputs()
    timeout = env_float("GRAPHVIZ_LAYOUT_TIME_TIMEOUT", 180.0)
    jobs = env_int("GRAPHVIZ_LAYOUT_TIME_JOBS", 8)
    upstream_dot = env_path("GRAPHVIZ_LAYOUT_TIME_UPSTREAM_DOT", required=True)
    trunk_dot = env_path("GRAPHVIZ_LAYOUT_TIME_TRUNK_DOT", required=True)
    identity_dot = env_path("GRAPHVIZ_LAYOUT_TIME_BEFORE_DOT", required=True)
    head_dot = dot_path()

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    print(f"commit\t{commit}")
    rc = require_identical_configure_lines(
        {
            "upstream": configure_line("upstream", upstream_dot),
            "trunk": configure_line("trunk", trunk_dot),
            "head": configure_line("head", head_dot),
        }
    )
    upstream_rows = measure("upstream", upstream_dot, inputs, timeout, jobs, MODES[:1])
    trunk_rows = measure("trunk", trunk_dot, inputs, timeout, jobs, MODES[:1])
    head_rows = measure("head", head_dot, inputs, timeout, jobs)
    print_counts("trunk", trunk_rows)
    print_counts("head", head_rows)
    spicy_0734_report(upstream_rows, trunk_rows, head_rows)
    absent_ratio_report("trunk", upstream_rows, trunk_rows)
    rc |= absent_ratio_report(
        "head", upstream_rows, head_rows, 1.5, upstream_dot, head_dot, timeout
    )
    feature_cost_report(head_rows)
    print_profile_report()
    rc |= byte_identity_sample(head_dot, identity_dot)
    print_buildability_report()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
