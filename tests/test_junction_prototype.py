from __future__ import annotations

import importlib.util
import json
import xml.etree.ElementTree as ET
from collections import Counter
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


def render_json(source: bytes) -> dict:
    proc = subprocess.run(
        ["dot", "-Tjson"], input=source, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
    )
    return json.loads(proc.stdout)


def drawn_text(data: dict, draw_key: str) -> Counter[str]:
    return Counter(
        op["text"] for edge in data.get("edges", [])
        for op in edge.get(draw_key, []) if op.get("op") == "T"
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


@pytest.mark.parametrize("kind", ["fan-out", "fan-in"])
@pytest.mark.parametrize("minlen", [None, "0", "1", "2", "5"])
def test_trunk_zero_minlen_and_arms_keep_rank_span(tmp_path: Path, kind: str, minlen: str | None) -> None:
    attrs = f", minlen={minlen}" if minlen is not None else ""
    edges = (("a", "b"), ("a", "c")) if kind == "fan-out" else (("b", "a"), ("c", "a"))
    text = "digraph G {\n" + "".join(
        f"  {tail} -> {head} [id=e{i}{attrs}];\n" for i, (tail, head) in enumerate(edges)
    ) + "}\n"
    proc = run_tool(tmp_path, text, "--no-preserve-ranks")
    assert proc.returncode == 0 and kind.encode() in proc.stderr
    data = render_json(proc.stdout)
    names = {obj["_gvid"]: obj["name"] for obj in data["objects"]}
    trunks = [edge for edge in data["edges"] if names[edge["tail"] if kind == "fan-out" else edge["head"]] == "a"]
    arms = [edge for edge in data["edges"] if edge not in trunks]
    assert len(trunks) == 1 and len(arms) == 2
    assert trunks[0]["minlen"] == "0" and not trunks[0].get("id")
    assert {edge["id"] for edge in arms} == {"e0", "e1"}
    assert all(edge.get("minlen", "1") == (minlen or "1") for edge in arms)
    assert jz.FLIP_MARK.encode() not in proc.stdout
    original = render_json(text.encode())
    old_y = {obj["name"]: float(obj["pos"].split(",")[1]) for obj in original["objects"]}
    new_y = {obj["name"]: float(obj["pos"].split(",")[1]) for obj in data["objects"]}
    assert abs(new_y["a"] - new_y["b"]) == pytest.approx(abs(old_y["a"] - old_y["b"]))


@pytest.mark.parametrize("kind", ["fan-out", "fan-in"])
def test_inherited_labels_have_one_correct_carrier(tmp_path: Path, kind: str) -> None:
    edges = "a -> b; a -> c;" if kind == "fan-out" else "b -> a; c -> a;"
    text = (
        'digraph G { edge [label="group", xlabel="external", headlabel="head", taillabel="tail"]; '
        + edges + ' edge [label="", xlabel="", headlabel="", taillabel=""]; x -> y [label="neighbor"]; }\n'
    )
    proc = run_tool(tmp_path, text)
    assert proc.returncode == 0 and kind.encode() in proc.stderr
    data = render_json(proc.stdout)
    names = {obj["_gvid"]: obj["name"] for obj in data["objects"]}
    trunks = [edge for edge in data["edges"] if names[edge["tail"] if kind == "fan-out" else edge["head"]] == "a"]
    arms = [edge for edge in data["edges"] if names[edge["tail"] if kind == "fan-out" else edge["head"]].startswith("__junction")]
    assert len(trunks) == 1 and len(arms) == 2
    shared = "taillabel" if kind == "fan-out" else "headlabel"
    distinct = "headlabel" if kind == "fan-out" else "taillabel"
    assert trunks[0]["label"] == "group" and trunks[0]["xlabel"] == "external"
    assert trunks[0][shared] == ("tail" if kind == "fan-out" else "head")
    assert not trunks[0].get(distinct)
    assert all(not edge.get(name) for edge in arms for name in ("label", "xlabel", shared))
    assert all(edge[distinct] == ("head" if kind == "fan-out" else "tail") for edge in arms)
    assert drawn_text(data, "_ldraw_") == Counter({"group": 1, "external": 1, "neighbor": 1})
    assert drawn_text(data, "_hldraw_") == Counter({"head": 2 if kind == "fan-out" else 1})
    assert drawn_text(data, "_tldraw_") == Counter({"tail": 1 if kind == "fan-out" else 2})


@pytest.mark.parametrize("name", sorted(jz.INTERACTION_ATTRS))
def test_shared_trunk_clears_interaction_defaults_but_arms_keep_values(tmp_path: Path, name: str) -> None:
    text = f'digraph G {{ edge [{name}="inherited"]; a -> b [{name}="first"]; a -> c; }}\n'
    proc = run_tool(tmp_path, text)
    assert proc.returncode == 0 and b"fan-out" in proc.stderr
    graph = jz.pgv.AGraph(string=proc.stdout.decode())
    assert not graph.get_edge("a", "__junction0").attr.get(name)
    assert graph.get_edge("__junction0", "b").attr.get(name) == "first"
    assert graph.get_edge("__junction0", "c").attr.get(name) == "inherited"
    render_json(proc.stdout)


@pytest.mark.parametrize("name", ["URL", "href", "edgeURL", "edgehref"])
def test_svg_links_remain_on_the_correct_arms(tmp_path: Path, name: str) -> None:
    text = (
        f'digraph G {{ edge [{name}="inherited"]; a -> b [{name}="first"]; a -> c; '
        f'edge [{name}=""]; x -> y [label="neighbor", {name}="neighbor-url"]; }}\n'
    )
    proc = run_tool(tmp_path, text)
    assert proc.returncode == 0 and b"fan-out" in proc.stderr
    svg = subprocess.run(
        ["dot", "-Tsvg"], input=proc.stdout, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
    )
    ns = {"svg": "http://www.w3.org/2000/svg", "xlink": "http://www.w3.org/1999/xlink"}
    links = {}
    for group in ET.fromstring(svg.stdout).findall(".//svg:g[@class='edge']", ns):
        title = group.find("svg:title", ns)
        assert title is not None
        links[title.text] = {a.attrib.get("{" + ns["xlink"] + "}href") for a in group.findall(".//svg:a", ns)}
    assert links["a->__junction0"] == set()
    assert links["__junction0->b"] == {"first"}
    assert links["__junction0->c"] == {"inherited"}
    assert links["x->y"] == {"neighbor-url"}


@pytest.mark.parametrize("name", sorted(jz.LABEL_ATTRS))
@pytest.mark.parametrize("label", [r"\E", r"\H", r"\T"])
@pytest.mark.parametrize("inherited", [False, True])
def test_context_dependent_labels_refuse_byte_exactly(tmp_path: Path, name: str, label: str, inherited: bool) -> None:
    attrs = f'[{name}="{label}"]'
    text = f"digraph G {{ edge {attrs}; a -> b; a -> c; }}\n" if inherited else f"digraph G {{ a -> b {attrs}; a -> c {attrs}; }}\n"
    proc = run_tool(tmp_path, text)
    assert proc.returncode == 0 and b"refused:label-substitution" in proc.stderr
    assert proc.stdout == text.encode()
    data = render_json(proc.stdout)
    key = {"label": "_ldraw_", "xlabel": "_ldraw_", "headlabel": "_hldraw_", "taillabel": "_tldraw_"}[name]
    assert sum(drawn_text(data, key).values()) == 2


@pytest.mark.parametrize("label, expected", [(r"\\H", [r"\\H"]), (r"line1\nline2", ["line1", "line2"]), ("ordinary", ["ordinary"])])
def test_literal_and_multiline_labels_still_concentrate(tmp_path: Path, label: str, expected: list[str]) -> None:
    text = f'digraph G {{ a -> b [label="{label}"]; a -> c [label="{label}"]; }}\n'
    proc = run_tool(tmp_path, text)
    assert proc.returncode == 0 and b"fan-out" in proc.stderr and b"refused:" not in proc.stderr
    assert drawn_text(render_json(proc.stdout), "_ldraw_") == Counter(expected)


def test_ported_labels_and_distinct_ordinary_labels_remain_unchanged(tmp_path: Path) -> None:
    ported = 'digraph G { a [shape=record, label="<p>port"]; a:p -> b [label="ordinary"]; a:p -> c [label="ordinary"]; }\n'
    proc = run_tool(tmp_path, ported)
    assert proc.returncode == 0 and b"refused:port" in proc.stderr
    assert proc.stdout == ported.encode()
    render_json(proc.stdout)
    distinct = 'digraph G { a -> b [label="first"]; a -> c [label="second"]; }\n'
    proc = run_tool(tmp_path, distinct)
    assert proc.returncode == 0 and proc.stdout == distinct.encode()
    assert drawn_text(render_json(proc.stdout), "_ldraw_") == Counter({"first": 1, "second": 1})


@pytest.mark.parametrize("kind", ["fan-out", "fan-in"])
def test_record_shared_endpoint_refuses_zero_length_trunk(tmp_path: Path, kind: str) -> None:
    edges = "a -> b; a -> c;" if kind == "fan-out" else "b -> a; c -> a;"
    text = 'digraph G { a [shape=record, label="{a|b}"]; ' + edges + ' }\n'
    proc = run_tool(tmp_path, text)
    assert proc.returncode == 0 and b"refused:record-endpoint" in proc.stderr
    assert proc.stdout == text.encode()
    render_json(proc.stdout)


def test_b36_record_hierarchy_remains_parseable() -> None:
    fixture = ROOT / "tests" / "graphs" / "b36.gv"
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(fixture)],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    assert proc.returncode == 0 and b"refused:record-endpoint" in proc.stderr
    assert proc.stdout == fixture.read_bytes()
    render_json(proc.stdout)


@pytest.mark.parametrize("kind", ["fan-out", "fan-in"])
def test_record_arm_endpoints_still_concentrate(tmp_path: Path, kind: str) -> None:
    records = 'b [shape=record, label="{b|field}"]; c [shape=record, label="{c|field}"]; '
    edges = "a -> b; a -> c;" if kind == "fan-out" else "b -> a; c -> a;"
    proc = run_tool(tmp_path, 'digraph G { ' + records + edges + ' }\n')
    assert proc.returncode == 0 and kind.encode() in proc.stderr
    assert b"refused:record-endpoint" not in proc.stderr
    data = render_json(proc.stdout)
    names = {obj["_gvid"]: obj["name"] for obj in data["objects"]}
    endpoints = {(names[edge["tail"]], names[edge["head"]]) for edge in data["edges"]}
    junction = next(name for name in names.values() if name.startswith("__junction"))
    expected = (
        {("a", junction), (junction, "b"), (junction, "c")}
        if kind == "fan-out"
        else {("b", junction), ("c", junction), (junction, "a")}
    )
    assert endpoints == expected


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


def test_verifier_overlap_identity_survives_reordering_and_reversal() -> None:
    before = (
        'digraph G { edge [headlabel="shared endpoint label", labelangle=0, labeldistance=0, labelfontsize=30]; '
        'a; b; c; a -> z; b -> z; c -> z; }'
    )
    after = (
        'digraph G { edge [dir=back, taillabel="shared endpoint label", labelangle=0, labeldistance=0, labelfontsize=30]; '
        'c; b; a; z -> c; z -> b; z -> a; }'
    )
    old = verify.metrics(before.encode())
    new = verify.metrics(after.encode())
    assert old.label_label_overlaps > 0
    assert new.label_label_overlaps == old.label_label_overlaps
    assert set(new.label_label_pairs) == set(old.label_label_pairs)
    assert len({box.edge_name for box in old.label_boxes}) == 3


def test_verifier_detects_new_overlap_pair_even_when_count_is_unchanged() -> None:
    template = (
        'digraph G { edge [labelangle=0, labeldistance=0, labelfontsize=30]; '
        'a -> z [headlabel="first endpoint label"]; '
        'b -> z [headlabel="other endpoint label", labeldistance=%s]; '
        'c -> z [headlabel="third endpoint label", labeldistance=%s]; }'
    )
    old = verify.metrics((template % (0, 100)).encode())
    new = verify.metrics((template % (100, 0)).encode())
    assert old.label_label_overlaps == new.label_label_overlaps == 1
    assert set(new.label_label_pairs) - set(old.label_label_pairs)


def test_verifier_distinguishes_label_kind_direction_and_equal_text_owners() -> None:
    ordinary = verify.edge_name("a", "b", "same")
    assert ordinary == verify.edge_name("b", "a", "same", direction="back")
    assert ordinary != verify.edge_name("a", "c", "same")
    assert ordinary != verify.edge_name("b", "a", "same")
    assert ordinary != verify.edge_name("a", "b", "same", label_kind="headlabel")
    assert verify.edge_name("a", "b", "same", label_kind="headlabel") == verify.edge_name(
        "b", "a", "same", direction="back", label_kind="taillabel"
    )
