"""Local test harness setup for in-tree CMake builds."""

from __future__ import annotations

import os
from pathlib import Path


def pytest_configure() -> None:
    root = Path(__file__).resolve().parents[1]
    dot_dir = root / "build" / "cmd" / "dot"
    dot_builtins = dot_dir / "dot_builtins"
    if not dot_builtins.exists():
        return
    dot_link = dot_dir / "dot"
    if not dot_link.exists():
        dot_link.symlink_to("dot_builtins")
    os.environ["PATH"] = f"{dot_dir}{os.pathsep}{os.environ.get('PATH', '')}"
