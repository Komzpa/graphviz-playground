#!/usr/bin/env python3

"""Ratchet source files away from unbounded growth."""

import json
import subprocess
import sys
from pathlib import Path

SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hh",
    ".hpp",
    ".py",
}


def git_files() -> list[Path]:
    output = subprocess.check_output(("git", "ls-files"), text=True)
    return [
        Path(line)
        for line in output.splitlines()
        if Path(line).suffix in SOURCE_SUFFIXES
    ]


def line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def main() -> int:
    config_path = Path("ci/source_length_limits.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    default_limit = int(config["default_limit"])
    path_limits = {Path(path): int(limit) for path, limit in config["paths"].items()}

    failures = []
    for path in git_files():
        limit = path_limits.get(path, default_limit)
        count = line_count(path)
        if count > limit:
            failures.append(f"{path}: {count} lines > limit {limit}")

    if failures:
        print("source-length ratchet failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "raise an existing path limit only with reviewer-approved evidence, "
            "or split the file before adding more code",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
