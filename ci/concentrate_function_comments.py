#!/usr/bin/env python3

"""Require comments on C functions touched by the concentrate series."""

import re
import sys
from pathlib import Path

REQUIRED_FUNCTIONS = {
    Path("lib/common/arrows.c"): {
        "concentrated_arrow_decoration_record",
        "gv_cleanup_concentrated_edge_arrows",
        "explicit_edge_fillcolor",
        "strip_color_segment_fraction",
        "color_list_endpoint_color",
        "arrow_match_shape",
        "arrow_match_name",
    },
    Path("lib/dotgen/edge_chains.c"): {
        "make_virtual_edge_chain_impl",
    },
}

FUNCTION_RE = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{")

CONTROL_PREFIXES = ("if ", "for ", "while ", "switch ")


def has_comment_before(lines: list[str], definition_index: int) -> bool:
    cursor = definition_index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor < 0:
        return False
    stripped = lines[cursor].strip()
    if stripped.startswith(("/*", "//")):
        return True
    if stripped.endswith("*/"):
        while cursor >= 0:
            if lines[cursor].strip().startswith("/*"):
                return True
            cursor -= 1
    return False


def function_ranges(path: Path) -> list[tuple[str, int, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    ranges = []
    index = 0
    while index < len(lines):
        signature = lines[index].strip()
        stripped = signature.lstrip()
        if not stripped or stripped.startswith(("#", "/*", "*", "//")):
            index += 1
            continue
        if stripped.endswith("*/"):
            index += 1
            continue
        if stripped.startswith(CONTROL_PREFIXES):
            index += 1
            continue
        lookahead = index
        while "{" not in signature and ";" not in signature and lookahead + 1 < len(lines):
            lookahead += 1
            signature += " " + lines[lookahead].strip()
        match = FUNCTION_RE.search(signature)
        if match is None:
            index += 1
            continue
        depth = sum(line.count("{") - line.count("}") for line in lines[index : lookahead + 1])
        end = lookahead + 1
        while depth > 0 and end < len(lines):
            depth += lines[end].count("{") - lines[end].count("}")
            end += 1
        ranges.append((match.group("name"), index + 1, end))
        index = end
    return ranges


def main() -> int:
    failures = []
    for path, required_names in REQUIRED_FUNCTIONS.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        seen = set()
        for name, start, _ in function_ranges(path):
            if name not in required_names:
                continue
            seen.add(name)
            if not has_comment_before(lines, start - 1):
                failures.append(f"{path}:{start}: {name} lacks a context comment")
        missing = sorted(required_names - seen)
        for name in missing:
            failures.append(f"{path}: missing required function {name}")

    if failures:
        print("touched C functions need contextual comments:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
