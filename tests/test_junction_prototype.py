from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "contrib" / "junction-prototype" / "smoke.py"
TOOL = ROOT / "contrib" / "junction-prototype" / "junctionize.py"


def test_junction_prototype_smoke() -> None:
    subprocess.run([sys.executable, str(SMOKE)], check=True)


def test_refused_graph_is_byte_identical_with_and_without_skewer_order() -> None:
    fixture = ROOT / "graphs" / "directed" / "train11.gv"
    original = fixture.read_bytes()
    for extra in ([], ["--no-skewer-order"]):
        proc = subprocess.run(
            [sys.executable, str(TOOL), str(fixture), *extra],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stderr.startswith(b"refused:")
        assert proc.stdout == original
