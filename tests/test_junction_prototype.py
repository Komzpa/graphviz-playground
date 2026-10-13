from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# The prototype drives Graphviz through PyGraphviz, which is not in requirements.txt
# (it needs the Graphviz development headers). Skip instead of failing without it.
pytest.importorskip("pygraphviz")

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "contrib" / "junction-prototype"
SMOKE = PROTO / "smoke.py"
TOOL = PROTO / "junctionize.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


jz = load("junctionize_under_test", TOOL)
verify = load("verify_skewer_order_under_test", PROTO / "verify_skewer_order.py")
overshoot = load("overshoot_under_test", PROTO / "overshoot.py")


def run_tool(tmp_path: Path, text: str, *extra: str) -> subprocess.CompletedProcess[bytes]:
    path = tmp_path / "in.gv"
    path.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOL), str(path), *extra],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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


def test_baseline_fixture_is_in_tree() -> None:
    assert verify.BASELINE_TOOL.is_file()
    assert verify.BASELINE_TOOL.is_relative_to(ROOT)


@pytest.mark.parametrize("value", ["0", "false", "No"])
def test_constraint_false_spellings_refuse(tmp_path: Path, value: str) -> None:
    text = f"digraph G {{\n  a -> b [constraint={value}];\n  a -> c;\n}}\n"
    proc = run_tool(tmp_path, text)
    assert b"refused:constraint-false" in proc.stderr
    assert proc.stdout == text.encode()


@pytest.mark.parametrize(
    "clusters",
    [
        "subgraph ClusterA { a; } subgraph ClusterB { b; }",
        "subgraph x { cluster=true; a; } subgraph y { cluster=true; b; }",
    ],
)
def test_all_cluster_forms_are_recognised(tmp_path: Path, clusters: str) -> None:
    text = f"digraph G {{\n  {clusters}\n  a -> b;\n}}\n"
    proc = run_tool(tmp_path, text)
    assert b"refused:different-clusters" in proc.stderr
    assert proc.stdout == text.encode()


def test_census_fields_are_escaped() -> None:
    assert jz.tsv_field("a\tb\nc\\d\r") == "a\\tb\\nc\\\\d\\r"


def test_census_record_is_one_line_for_multiline_label(tmp_path: Path, capfd) -> None:
    jz.census("fan-out", "a", "x\ty\nz", 2)
    err = capfd.readouterr().err
    assert err == "fan-out\ta\tx\\ty\\nz\t2\n"


def test_reversal_swaps_endpoint_paired_attributes() -> None:
    out = jz.reversed_attrs({"headclip": "false", "tailURL": "u", "headlabel": "h", "dir": "forward"})
    assert out["tailclip"] == "false" and "headclip" not in out
    assert out["headURL"] == "u" and "tailURL" not in out
    assert out["taillabel"] == "h"
    assert out["dir"] == "back"


def test_trunk_has_no_duplicated_id_or_minlen(tmp_path: Path) -> None:
    text = (
        "digraph G {\n"
        "  a -> b [id=e1, minlen=2];\n"
        "  a -> c [id=e2, minlen=2];\n"
        "  b -> d;\n  c -> d;\n"
        "}\n"
    )
    proc = run_tool(tmp_path, text, "--no-fan-in")
    out = proc.stdout.decode()
    assert out.count("id=e1") == 1 and out.count("id=e2") == 1
    import re

    statements = re.findall(r"^\t(\S+) -> (\S+)\t\[(.*?)\];", out, re.S | re.M)
    trunks = [attrs for tail, head, attrs in statements if tail == "a" and head.startswith("__junction")]
    arms = [attrs for tail, head, attrs in statements if tail.startswith("__junction")]
    assert len(trunks) == 1 and len(arms) == 2
    assert "minlen" not in trunks[0] and "id=" not in trunks[0]
    assert all("minlen=1" in attrs for attrs in arms)
    assert jz.FLIP_MARK not in out
    assert subprocess.run(["dot", "-Tdot"], input=proc.stdout, stdout=subprocess.PIPE).returncode == 0


def test_deferred_edges_stay_inside_their_graph_and_cluster() -> None:
    mark = jz.FLIP_MARK
    text = (
        "digraph G {\n"
        "\tsubgraph cluster_1 {\n"
        "\t\tc;\n"
        f"\t\tc -> d\t[{mark}=1];\n"
        "\t}\n"
        f"\ta -> b\t[{mark}=1];\n"
        "}\n"
    )
    out = jz.ordered_dot(text)
    lines = out.splitlines()
    assert lines[-1] == "}"
    assert lines.index("\t\tc -> d\t[" + mark + "=1];") < lines.index("\t}")
    assert lines.index("\ta -> b\t[" + mark + "=1];") < len(lines) - 1


def test_ordering_declaration_stays_in_its_cluster() -> None:
    text = (
        "digraph G {\n"
        "\tsubgraph cluster_1 {\n"
        "\t\tc\t[ordering=out];\n"
        "\t\tc -> d;\n"
        "\t}\n"
        "\ta -> b;\n"
        "}\n"
    )
    out = jz.ordered_dot(text)
    assert out.index("c\t[ordering=out]") < out.index("}")
    assert out.index("c\t[ordering=out]") < out.index("a -> b")


def test_dir_back_alone_does_not_defer_and_dir_none_flip_does() -> None:
    mark = jz.FLIP_MARK
    forward_back = "digraph G {\n\ta -> b\t[dir=back];\n\ta -> c;\n}\n"
    assert jz.ordered_dot(forward_back) == forward_back
    flipped_none = f"digraph G {{\n\ta -> b\t[dir=none, {mark}=1];\n\ta -> c;\n}}\n"
    out = jz.ordered_dot(flipped_none)
    assert out.index("a -> c") < out.index("a -> b")
    assert jz.strip_flip_mark(out) == "digraph G {\n\ta -> c;\n\ta -> b\t[dir=none];\n}\n"


def test_strip_flip_mark_positions() -> None:
    mark = jz.FLIP_MARK
    assert jz.strip_flip_mark(f"a -> b\t[{mark}=1];") == "a -> b;"
    assert jz.strip_flip_mark(f"a -> b\t[{mark}=1,\n\t\tlabel=x];") == "a -> b\t[label=x];"
    assert jz.strip_flip_mark(f"a -> b\t[label=x, {mark}=1];") == "a -> b\t[label=x];"


def test_overshoot_plain_header_is_numeric() -> None:
    assert overshoot.is_plain_header("graph 1 2.5 3")
    assert not overshoot.is_plain_header("graph G { a -- b }")


def test_overshoot_reads_latin1_source(tmp_path: Path) -> None:
    path = tmp_path / "latin1.gv"
    path.write_bytes(b'digraph G { charset=latin1; a -> "\xe9"; }\n')
    assert overshoot.dot_plain(str(path), "dot").startswith("graph ")


def test_verifier_label_boxes_cover_every_run_and_font_size() -> None:
    edge = {
        "_ldraw_": [
            {"op": "F", "size": 20.0},
            {"op": "T", "pt": [10.0, 50.0], "align": "c", "width": 10.0, "text": "a"},
            {"op": "T", "pt": [10.0, 30.0], "align": "c", "width": 10.0, "text": "b"},
        ],
        "_hldraw_": [{"op": "T", "pt": [0.0, 0.0], "align": "l", "width": 4.0, "text": "h"}],
    }
    boxes = verify.label_boxes_for(edge)
    assert len(boxes) == 2
    assert boxes[0].y1 - boxes[0].y0 == 20.0
    head = verify.label_boxes_for(edge, "_hldraw_")
    assert head[0].x0 == 0.0 and head[0].x1 == 4.0 and head[0].y1 - head[0].y0 == 14.0


def test_verifier_flattens_splines_and_skips_invisible_nodes() -> None:
    line = verify.edge_polylines("e,10,0 0,0 0,10 10,10 10,5")[0]
    assert line[-1] == (10.0, 0.0)
    # The control polygon passes through (0,10) but the curve itself stays near x=7.5 there.
    assert max(x for x, _ in line) <= 10.0
    assert not any(abs(x) < 1e-9 and abs(y - 10.0) < 1e-9 for x, y in line)
    data = {
        "objects": [
            {"_gvid": 0, "pos": "0,0", "width": "1", "height": "1", "style": "invis"},
            {"_gvid": 1, "pos": "5,5", "width": "1", "height": "1"},
        ]
    }
    assert set(verify.node_boxes(data)) == {1}
