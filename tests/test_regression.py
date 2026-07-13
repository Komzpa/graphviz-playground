"""
Graphviz regression tests

The test cases in this file relate to previously observed bugs. A failure of one
of these indicates that a past bug has been reintroduced.
"""

import dataclasses
import hashlib
import io
import itertools
import json
import math
import operator
import os
import platform
import re
import shlex
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator, Optional, Union

import pexpect
import pytest
from PIL import Image

sys.path.append(os.path.dirname(__file__))
from gvtest import (  # pylint: disable=wrong-import-position
    compile_c,
    dot,
    gvpr,
    is_asan_instrumented,
    is_autotools,
    is_cmake,
    is_fedora,
    is_macos,
    is_mingw,
    is_rocky,
    is_rocky_8,
    is_rocky_10,
    is_static_build,
    is_ubuntu_2604,
    pexpect_spawn_tclsh,
    plugin_version,
    remove_asan_summary,
    remove_xtype_warnings,
    run,
    run_c,
    run_raw,
    which,
)


def is_ndebug_defined() -> bool:
    """
    are assertions disabled in the Graphviz build under test?
    """

    # the Windows release builds set NDEBUG
    if os.environ.get("configuration") == "Release":
        return True

    return False


def test_14():
    """
    using ortho and twopi in combination should not cause an assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/14
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "14.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("svg", input)


@pytest.mark.skipif(which("neato") is None, reason="neato not available")
def test_42():
    """
    check for a former crash in neatogen
    https://gitlab.com/graphviz/graphviz/-/issues/42
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "42.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    neato = which("neato")
    run_raw(neato, "-n2", "-Tpng", input, stdout=subprocess.DEVNULL)


def test_56():
    """
    parsing a particular graph should not cause a Trapezoid-table overflow
    assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/56
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "56.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("svg", input)


def test_121():
    """
    test a graph that previously caused an assertion failure in `merge_chain`
    https://gitlab.com/graphviz/graphviz/-/issues/121
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "121.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("pdf", input)


def test_131():
    """
    PIC back end should produce valid output
    https://gitlab.com/graphviz/graphviz/-/issues/131
    """

    # a basic graph
    src = "digraph { a -> b; c -> d; }"

    # ask Graphviz to process this to PIC
    pic = dot("pic", source=src)

    if which("gpic") is None:
        pytest.skip("GNU PIC not available")

    # ask GNU PIC to process the Graphviz output
    run("gpic", input=pic, stdout=subprocess.DEVNULL)


@pytest.mark.parametrize("testcase", ("144_no_ortho.dot", "144_ortho.dot"))
def test_144(testcase: str):
    """
    using ortho should not result in head/tail confusion
    https://gitlab.com/graphviz/graphviz/-/issues/144
    """

    # locate our associated test cases in this directory
    input = Path(__file__).parent / testcase
    assert input.exists(), "unexpectedly missing test case"

    # process the non-ortho one into JSON
    out = dot("json", input)
    data = json.loads(out)

    # find the nodes “A”, “B” and “C”
    A = [x for x in data["objects"] if x["name"] == "A"][0]
    B = [x for x in data["objects"] if x["name"] == "B"][0]
    C = [x for x in data["objects"] if x["name"] == "C"][0]

    # find the straight A→B and the angular A→C edges
    straight_edge = [
        x for x in data["edges"] if x["tail"] == A["_gvid"] and x["head"] == B["_gvid"]
    ][0]
    angular_edge = [
        x for x in data["edges"] if x["tail"] == A["_gvid"] and x["head"] == C["_gvid"]
    ][0]

    # the A→B edge should have been routed vertically down
    straight_points = straight_edge["_draw_"][1]["points"]
    xs = [x for x, _ in straight_points]
    ys = [y for _, y in straight_points]
    assert all(x == xs[0] for x in xs), "A->B not routed vertically"
    assert ys == sorted(ys, reverse=True), "A->B is not routed down"

    # determine Graphviz’ idea of head and tail ends
    straight_head_point = straight_edge["_hdraw_"][3]["points"][0]
    straight_tail_point = straight_edge["_tdraw_"][3]["points"][0]
    assert straight_head_point[1] < straight_tail_point[1], "A->B head/tail confusion"

    # the A→C edge should have been routed in zigzag down and right
    angular_points = angular_edge["_draw_"][1]["points"]
    xs = [x for x, _ in angular_points]
    ys = [y for _, y in angular_points]
    assert xs == sorted(xs), "A->B is not routed down"
    assert ys == sorted(ys, reverse=True), "A->B is not routed right"

    # determine Graphviz’ idea of head and tail ends
    angular_head_point = angular_edge["_hdraw_"][3]["points"][0]
    angular_tail_point = angular_edge["_tdraw_"][3]["points"][0]
    assert angular_head_point[0] > angular_tail_point[0], "A->C head/tail confusion"


def test_146():
    """
    dot should respect an alpha channel value of 0 when writing SVG
    https://gitlab.com/graphviz/graphviz/-/issues/146
    """

    # a graph using white text but with 0 alpha
    source = (
        "graph {\n"
        '  n[style="filled", fontcolor="#FFFFFF00", label="hello world"];\n'
        "}"
    )

    # ask Graphviz to process this
    svg = dot("svg", source=source)

    # the SVG should be setting opacity
    opacity = re.search(r'\bfill-opacity="(\d+(\.\d+)?)"', svg)
    assert opacity is not None, "transparency not set for alpha=0 color"

    # it should be zeroed
    assert (
        float(opacity.group(1)) == 0
    ), "alpha=0 color set to something non-transparent"


def test_162():
    """
    `minlen=0` should not duplicate edges
    https://gitlab.com/graphviz/graphviz/-/issues/162
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "162.dot"
    assert input.exists(), "unexpectedly missing test case"

    # lay this out
    layout = dot("dot", input)

    # find an inter-cluster edge
    m = re.search(
        r'\bC\s*->\s*D\s*\[\s*minlen\s*=\s*0\s*,\s*pos\s*=\s*"(?P<position>[^"]*)"',
        layout,
    )
    assert m is not None, "could not locate C->D edge"

    edge_count = len(re.findall(r"\be\b", m.group("position")))
    assert edge_count == 1, "incorrect number of inter-cluster edges"


def test_165():
    """
    dot should be able to produce properly escaped xdot output
    https://gitlab.com/graphviz/graphviz/-/issues/165
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "165.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to translate it to xdot
    output = dot("xdot", input)

    # find the line containing the _ldraw_ attribute
    ldraw = re.search(r"^\s*_ldraw_\s*=(?P<value>.*?)$", output, re.MULTILINE)
    assert ldraw is not None, "no _ldraw_ attribute in graph"

    # this should contain the label correctly escaped
    assert r"hello \\\" world" in ldraw.group("value"), "unexpected ldraw contents"


def test_165_2():
    """
    variant of test_165() that checks a similar problem for edges
    https://gitlab.com/graphviz/graphviz/-/issues/165
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "165_2.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to translate it to xdot
    output = dot("xdot", input)

    # find the lines containing _ldraw_ attributes
    ldraw = re.findall(r"^\s*_ldraw_\s*=(.*?)$", output, re.MULTILINE)
    assert ldraw is not None, "no _ldraw_ attributes in graph"

    # one of these should contain the label correctly escaped
    assert any(r"hello \\\" world" in l for l in ldraw), "unexpected ldraw contents"


def test_165_3():
    """
    variant of test_165() that checks a similar problem for graph labels
    https://gitlab.com/graphviz/graphviz/-/issues/165
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "165_3.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to translate it to xdot
    output = dot("xdot", input)

    # find the lines containing _ldraw_ attributes
    ldraw = re.findall(r"^\s*_ldraw_\s*=(.*?)$", output, re.MULTILINE)
    assert ldraw is not None, "no _ldraw_ attributes in graph"

    # one of these should contain the label correctly escaped
    assert any(r"hello \\\" world" in l for l in ldraw), "unexpected ldraw contents"


def test_167():
    """
    using concentrate=true should not result in a segfault
    https://gitlab.com/graphviz/graphviz/-/issues/167
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "167.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this with dot
    ret = subprocess.call(["dot", "-Tpdf", "-o", os.devnull, input])

    # Graphviz should not have caused a segfault
    assert ret != -signal.SIGSEGV, "Graphviz segfaulted"


def test_191():
    """
    a comma-separated list without quotes should cause a hard error, not a warning
    https://gitlab.com/graphviz/graphviz/-/issues/191
    """

    source = (
        "graph {\n"
        '  "Trackable" [fontcolor=grey45,labelloc=c,fontname=Vera Sans, '
        "DejaVu Sans, Liberation Sans, Arial, Helvetica, sans,shape=box,"
        'height=0.3,align=center,fontsize=10,style="setlinewidth(0.5)"];\n'
        "}"
    )

    with subprocess.Popen(
        ["dot", "-Tdot"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, stderr = p.communicate(source)

        assert "syntax error" in stderr, "missing error message for unquoted list"

        assert p.returncode != 0, "syntax error was only a warning, not an error"


def test_218():
    """
    out-of-spec font names should cause warnings in the core PS renderer
    https://gitlab.com/graphviz/graphviz/-/issues/218
    """

    # a graph using a font name with a space in it
    source = 'graph { a[fontname="PT Sans"]; }'

    # render it to PS
    warnings = run(
        "dot",
        "-Tps",
        "-o",
        os.devnull,
        stderr=subprocess.STDOUT,
        input=source,
    )

    assert warnings.strip() != "", "no warning issued for a font name containing space"


@pytest.mark.parametrize("test_case", ("241_0.dot", "241_1.dot"))
def test_241(test_case: str):
    """
    processing a graph with a `splines=…` setting should not causes warnings
    https://gitlab.com/graphviz/graphviz/-/issues/241
    """
    # locate our associated test case in this directory
    input = Path(__file__).parent / test_case
    assert input.exists(), "unexpectedly missing test case"

    proc = subprocess.run(
        ["dot", "-Tsvg", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )

    assert (
        "Something is probably seriously wrong" not in proc.stderr
    ), "splines setting caused warnings"


def test_258():
    """
    cluster edges should not be duplicated
    https://gitlab.com/graphviz/graphviz/-/issues/258
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "258.dot"
    assert input.exists(), "unexpectedly missing test case"

    # lay this out
    layout = dot("dot", input)

    # find an inter-cluster edge
    m = re.search(
        r'\bB\s*->\s*D\s*\[\s*constraint\s*=\s*none\s*,\s*pos\s*=\s*"(?P<position>[^"]*)"',
        layout,
    )
    assert m is not None, "could not locate B->D edge"

    edge_count = len(re.findall(r"\be\b", m.group("position")))
    assert edge_count == 1, "incorrect number of inter-cluster edges"


def test_358():
    """
    setting xdot version to 1.7 should enable font characteristics
    https://gitlab.com/graphviz/graphviz/-/issues/358
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "358.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this with dot
    xdot = dot("xdot", input)

    for i in range(6):
        m = re.search(f"\\bt {1 << i}\\b", xdot)
        assert m is not None, f"font characteristic {1 << i} not enabled in xdot 1.7"


@pytest.mark.parametrize("attribute", ("samehead", "sametail"))
def test_452(attribute: str):
    """
    more than 5 unique `samehead` and `sametail` values should be usable
    https://gitlab.com/graphviz/graphviz/-/issues/452
    """

    # a graph using more than 5 of the same attribute with the same node on one
    # side of each edge
    graph = io.StringIO()
    graph.write("digraph {\n")
    for i in range(6):
        if attribute == "samehead":
            graph.write(f"  m{i} -> n1")
        else:
            graph.write(f"  n1 -> m{i}")
        graph.write(f'[{attribute}="foo{i}"];\n')
    graph.write("}\n")

    # process this with dot
    dot("svg", source=graph.getvalue())


def test_510():
    """
    HSV colors should also support an alpha channel
    https://gitlab.com/graphviz/graphviz/-/issues/510
    """

    # a graph with a turquoise, partially transparent node
    source = 'digraph { a [color="0.482 0.714 0.878 0.5"]; }'

    # process this with dot
    svg = dot("svg", source=source)

    # see if we can locate an opacity adjustment
    m = re.search(r'\bstroke-opacity="(?P<opacity>\d*.\d*)"', svg)
    assert m is not None, "no stroke-opacity set; alpha channel ignored?"

    # it should be something in-between transparent and opaque
    opacity = float(m.group("opacity"))
    assert opacity > 0, "node set transparent; misinterpreted alpha channel?"
    assert opacity < 1, "node set opaque; misinterpreted alpha channel?"


@pytest.mark.skipif(
    which("gv2gxl") is None or which("gxl2gv") is None, reason="GXL tools not available"
)
def test_517():
    """
    round tripping a graph through gv2gxl should not lose HTML labels
    https://gitlab.com/graphviz/graphviz/-/issues/517
    """

    # our test case input
    input = (
        "digraph{\n"
        "  A[label=<<TABLE><TR><TD>(</TD><TD>A</TD><TD>)</TD></TR></TABLE>>]\n"
        '  B[label="<TABLE><TR><TD>(</TD><TD>B</TD><TD>)</TD></TR></TABLE>"]\n'
        "}"
    )

    # translate it to GXL
    gv2gxl = which("gv2gxl")
    gxl = run(gv2gxl, input=input)

    # translate this back to Dot
    gxl2gv = which("gxl2gv")
    dot_output = run(gxl2gv, input=gxl)

    # the result should have both expected labels somewhere
    assert (
        "label=<<TABLE><TR><TD>(</TD><TD>A</TD><TD>)</TD></TR></TABLE>>" in dot_output
    ), "HTML label missing"
    assert (
        'label="<TABLE><TR><TD>(</TD><TD>B</TD><TD>)</TD></TR></TABLE>"' in dot_output
    ), "regular label missing"


def test_793():
    """
    Graphviz should not crash when using VRML output with a non-writable current
    directory
    https://gitlab.com/graphviz/graphviz/-/issues/793
    """

    # create a non-writable directory
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        t.chmod(t.stat().st_mode & ~stat.S_IWRITE)

        # ask the VRML back end to handle a simple graph, using the above as the
        # current working directory
        with subprocess.Popen(["dot", "-Tvrml", "-o", os.devnull], cwd=t) as p:
            p.communicate("digraph { a -> b; }")

            # Graphviz should not have caused a segfault
            assert p.returncode != -signal.SIGSEGV, "Graphviz segfaulted"


def test_797():
    """
    “&;” should not be considered an XML escape sequence
    https://gitlab.com/graphviz/graphviz/-/issues/797
    """

    # some input containing the invalid escape
    input = 'digraph tree {\n"1" [shape="box", label="&amp; &amp;;", URL="a"];\n}'

    # process this with the client-side imagemap back end
    output = dot("cmapx", source=input)

    # the escape sequences should have been preserved
    assert "&amp; &amp;" in output


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/813"
)
def test_813():
    """
    nodes with multiple peripheries should still have a stable rendering
    https://gitlab.com/graphviz/graphviz/-/issues/813
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "813.dot"
    assert input.exists(), "unexpectedly mising test case"

    # render this to dot
    reference = dot("dot", input)

    # run it through multiple passes
    iterated = reference
    for _ in range(4):
        iterated = dot("dot", source=iterated)

    assert (
        reference == iterated
    ), "rendering of shapes with multiple peripheries is unstable"


def test_827():
    """
    Graphviz should not crash when processing the b15.gv example
    https://gitlab.com/graphviz/graphviz/-/issues/827
    """

    b15gv = Path(__file__).parent / "graphs/b15.gv"
    assert b15gv.exists(), "missing test case file"

    dot("svg", b15gv)


def test_925():
    """
    spaces should be handled correctly in UTF-8-containing labels in record shapes
    https://gitlab.com/graphviz/graphviz/-/issues/925
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "925.dot"
    assert input.exists(), "unexpectedly mising test case"

    # process this with dot
    svg = dot("svg", input)

    # The output should include the correctly spaced UTF-8 label. Note that these
    # are not ASCII capital As in this string, but rather UTF-8 Cyrillic Capital
    # Letter As.
    assert "ААА ААА ААА" in svg, "incorrect spacing in UTF-8 label"


@pytest.mark.parametrize("testcase", ("1213-1.dot", "1213-2.dot"))
@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/1213"
)
def test_1213(testcase: str):
    """
    clustering should not trigger “trouble in init_rank” errors
    https://gitlab.com/graphviz/graphviz/-/issues/1213
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / testcase
    assert input.exists(), "unexpectedly mising test case"

    # process this with dot
    dot("png", input)


def test_1221():
    """
    assigning a node to two clusters with newrank should not cause a crash
    https://gitlab.com/graphviz/graphviz/-/issues/1221
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1221.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this with dot
    dot("svg", input)


@pytest.mark.skipif(which("gv2gml") is None, reason="gv2gml not available")
def test_1276():
    """
    quotes within a label should be escaped in translation to GML
    https://gitlab.com/graphviz/graphviz/-/issues/1276
    """

    # DOT input containing a label with quotes
    src = 'digraph test {\n  x[label=<"Label">];\n}'

    # process this to GML
    gv2gml = which("gv2gml")
    gml = run(gv2gml, input=src)

    # the unescaped label should not appear in the output
    assert '""Label""' not in gml, "quotes not escaped in label"

    # the escaped label should appear in the output
    assert (
        '"&quot;Label&quot;"' in gml or '"&#34;Label&#34;"' in gml
    ), "escaped label not found in GML output"


def test_1308():
    """
    processing a minimized graph found by Google Autofuzz should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/1308
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1308.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


def test_1308_1():
    """
    processing a malformed graph found by Google Autofuzz should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/1308
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1308_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    ret = subprocess.call(["dot", "-Tsvg", "-o", os.devnull, input])

    assert ret in (0, 1), "Graphviz crashed when processing malformed input"
    assert ret == 1, "Graphviz did not reject malformed input"


def test_1314():
    """
    test that a large font size that produces an overflow in Pango is rejected
    https://gitlab.com/graphviz/graphviz/-/issues/1314
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1314.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it, which should fail
    with pytest.raises(subprocess.CalledProcessError):
        dot("svg", input)


def test_1318():
    """
    processing a large number in a comment should not trigger integer overflow
    https://gitlab.com/graphviz/graphviz/-/issues/1318
    """

    # sample input consisting of a large number in a comment
    source = "#8828066547613302784"

    # processing this should succeed
    dot("svg", source=source)


@pytest.mark.parametrize("testcase", ("1323.dot", "1323_1.dot"))
@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/1323"
)
def test_1323(testcase: str):
    """
    these graphs should not generate triangulation warnings/errors
    https://gitlab.com/graphviz/graphviz/-/issues/1323
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / testcase
    assert input.exists(), "unexpectedly missing test case"

    stderr = run("dot", "-Tpng", "-o", os.devnull, input, stderr=subprocess.STDOUT)

    assert (
        re.search(r"\btriangulation failed\b", stderr) is None
    ), "triangulation warnings were produced"
    assert re.search(r"\bError\b", stderr) is None, "error messages were produced"


def test_1328():
    """
    a node with conflicting rank constraints should not cause a crash
    https://gitlab.com/graphviz/graphviz/-/issues/1328
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1328.dot"
    assert input.exists(), "unexpectedly missing test case"

    proc = subprocess.run(
        ["dot", "-Tsvg", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert proc.returncode in (0, 1), "multiple rank constraints caused a crash"


def test_1332():
    """
    Triangulation calculation on the associated example should succeed.

    A prior change that was intended to increase accuracy resulted in the
    example in this test now failing some triangulation calculations. It is not
    clear whether the outcome before or after is correct, but this test ensures
    that the older behavior users are accustomed to is preserved.
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1332.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    warnings = run("dot", "-Tpdf", "-o", os.devnull, input, stderr=subprocess.STDOUT)

    # work around macOS warnings
    warnings = remove_xtype_warnings(warnings).strip()

    # no warnings should have been printed
    assert (
        warnings == ""
    ), "warnings were printed when processing graph involving triangulation"


def test_1367():
    """
    this graph should not generate a null pointer dereference
    https://gitlab.com/graphviz/graphviz/-/issues/1367
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1367.dot"
    assert input.exists(), "unexpectedly missing test case"

    # Pass it through Graphviz. Do not use `dot(…)` because input and output contain
    # invalid UTF-8.
    run_raw("dot", "-Txdot:xdot:core", "-o", os.devnull, input)


def test_1408():
    """
    parsing particular ortho layouts should not cause an assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/1408
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1408.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("svg", input)


def test_1411():
    """
    parsing strings containing newlines should not disrupt line number tracking
    https://gitlab.com/graphviz/graphviz/-/issues/1411
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1411.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz (should fail)
    with subprocess.Popen(
        ["dot", "-Tsvg", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, output = p.communicate()

        assert p.returncode != 0, "Graphviz accepted broken input"

    assert (
        "syntax error in line 17 near '\\'" in output
    ), "error message did not identify correct location"


def test_1425():
    """
    tooltips should propagate to SVG even without an HREF
    https://gitlab.com/graphviz/graphviz/-/issues/1425
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1425.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    assert re.search(r"\btable tip\b", svg) is not None, "tooltip not propagated to SVG"


def test_1425_1():
    """
    tooltips should propagate to SVG even without an HREF
    https://gitlab.com/graphviz/graphviz/-/issues/1425
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1425_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    assert (
        re.search(r"\bgreek to me\b", svg) is not None
    ), "tooltip not propagated to SVG"
    assert re.search(r"\benglish\b", svg) is not None, "tooltip not propagated to SVG"
    assert (
        re.search(r"\bleave a tip\b", svg) is not None
    ), "tooltip not propagated to SVG"
    assert (
        re.search(r"\bcell tool tip\b", svg) is not None
    ), "tooltip not propagated to SVG"
    assert re.search(r"\btd tip\b", svg) is not None, "tooltip not propagated to SVG"
    assert re.search(r"\btable tip\b", svg) is not None, "tooltip not propagated to SVG"


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/1435"
)
def test_1435():
    """
    triangulation paths should be findable on this graph
    https://gitlab.com/graphviz/graphviz/-/issues/1435
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1435.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    err = run("dot", "-Tpng", "-o", os.devnull, input, stderr=subprocess.STDOUT)

    assert err.strip() == "", "errors were printed"


def test_1436():
    """
    test a segfault from https://gitlab.com/graphviz/graphviz/-/issues/1436 has
    not reappeared
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1436.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it, which should generate a segfault if this bug
    # has been reintroduced
    dot("svg", input)


def test_1444():
    """
    specifying 'headport' as an edge attribute should work regardless of what
    order attributes appear in
    https://gitlab.com/graphviz/graphviz/-/issues/1444
    """

    # locate the first of our associated tests
    input1 = Path(__file__).parent / "1444.dot"
    assert input1.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it
    with subprocess.Popen(
        ["dot", "-Tsvg", input1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        stdout1, stderr = p.communicate()

        assert p.returncode == 0, "failed to process a headport edge"

    stderr = remove_xtype_warnings(stderr).strip()
    stderr = remove_asan_summary(stderr)
    assert stderr == "", "emitted an error for a legal graph"

    # now locate our second variant, that simply has the attributes swapped
    input2 = Path(__file__).parent / "1444-2.dot"
    assert input2.exists(), "unexpectedly missing test case"

    # process it identically
    with subprocess.Popen(
        ["dot", "-Tsvg", input2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        stdout2, stderr = p.communicate()

        assert p.returncode == 0, "failed to process a headport edge"

    stderr = remove_xtype_warnings(stderr).strip()
    assert stderr == "", "emitted an error for a legal graph"

    assert stdout1 == stdout2, "swapping edge attributes altered the output graph"


def test_1447():
    """
    graphs should not fail assertions in maze.c
    https://gitlab.com/graphviz/graphviz/-/issues/1447
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1447.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


def test_1447_1():
    """
    graphs should not fail assertions in maze.c
    https://gitlab.com/graphviz/graphviz/-/issues/1447
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1447_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("png", input)


def test_1449():
    """
    using the SVG color scheme should not cause warnings
    https://gitlab.com/graphviz/graphviz/-/issues/1449
    """

    # start Graphviz
    with subprocess.Popen(
        ["dot", "-Tsvg", "-o", os.devnull],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        # pass it some input that uses the SVG color scheme
        _, stderr = p.communicate('graph g { colorscheme="svg"; }')

        assert p.returncode == 0, "Graphviz exited with non-zero status"

    assert stderr.strip() == "", "SVG color scheme use caused warnings"


def test_1453():
    """
    `splines=curved` should not result in segfaults
    https://gitlab.com/graphviz/graphviz/-/issues/1453
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1453.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


def test_1472():
    """
    processing a malformed graph found by Google Autofuzz should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/1472
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1472.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    proc = subprocess.run(
        ["dot", "-o", os.devnull, input], stderr=subprocess.PIPE, check=True
    )

    assert (
        re.search(rb"\bAddressSanitizer: heap-buffer-overflow\b", proc.stderr) is None
    ), "malformed input caused a buffer overflow"
    assert (
        re.search(rb"\bAddressSanitizer: heap-use-after-free\b", proc.stderr) is None
    ), "malformed input caused a use-after-free"


def test_1474():
    """
    processing this input found by fuzzing should not trigger a buffer overflow
    https://gitlab.com/graphviz/graphviz/-/issues/1474
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1474.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    proc = subprocess.run(
        ["dot", "-o", os.devnull, input], stderr=subprocess.PIPE, check=False
    )

    assert proc.returncode != 0, "invalid input was not rejected"

    assert (
        re.search(rb"\bAddressSanitizer: heap-buffer-overflow\b", proc.stderr) is None
    ), "malformed input caused a buffer overflow"


def test_1489():
    """
    processing this input found by fuzzing should not trigger an invalid read
    https://gitlab.com/graphviz/graphviz/-/issues/1489
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1489.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    proc = subprocess.run(
        ["dot", "-o", os.devnull, input], stderr=subprocess.PIPE, check=False
    )

    assert proc.returncode != 0, "invalid input was not rejected"

    assert (
        re.search(rb"\bAddressSanitizer: SEGV\b", proc.stderr) is None
    ), "malformed input caused an invalid memory access"


def test_1494():
    """
    processing this input found by fuzzing should not trigger a double-free
    https://gitlab.com/graphviz/graphviz/-/issues/1494
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1494.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    proc = subprocess.run(
        ["dot", "-o", os.devnull, input], stderr=subprocess.PIPE, check=False
    )

    assert proc.returncode != 0, "invalid input was not rejected"

    assert (
        re.search(rb"\bAddressSanitizer: double-free\b", proc.stderr) is None
    ), "malformed input caused a double free()"


def test_1514():
    """
    processing this input should not trigger an assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/1514
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1514.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    proc = subprocess.run(
        ["dot", "-o", os.devnull, input], stderr=subprocess.PIPE, check=False
    )

    assert (
        re.search(rb"\bAssertion `v' failed\b", proc.stderr) is None
    ), "malformed input caused an assertion failure"


@pytest.mark.xfail(
    is_autotools() and is_rocky_10(),
    strict=False,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2807",
)
def test_1554():
    """
    small distances between nodes should not cause a crash in majorization
    https://gitlab.com/graphviz/graphviz/-/issues/1554
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1554.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    output = dot("svg", input)

    # the output should not have NaN values, indicating out of bounds computation
    assert (
        re.search(r"\bnan\b", output, flags=re.IGNORECASE) is None
    ), "computation exceeded bounds"


def test_1581():
    """
    this example found by fuzzing should not cause an out-of-bounds write
    https://gitlab.com/graphviz/graphviz/-/issues/1581
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1581.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    p = subprocess.run(["dot", "-Tsvg", "-o", os.devnull, input], check=False)

    assert p.returncode != 42, "Address Sanitizer detect memory safety violations"


def test_1585():
    """
    clustering nodes should not reverse their horizontal layout
    https://gitlab.com/graphviz/graphviz/-/issues/1585
    """

    # locate our associated test cases in this directory
    no_cluster = Path(__file__).parent / "1585_0.dot"
    assert no_cluster.exists(), "unexpectedly missing test case"
    cluster = Path(__file__).parent / "1585_1.dot"
    assert cluster.exists(), "unexpectedly missing test case"

    def find_node_xs(svg_output: str) -> Iterator[float]:
        """
        yield 3 floats representing the X positions of nodes b, c, d in the
        given graph
        """

        # parse the SVG
        root = ET.fromstring(svg_output)

        # find `b`
        b = root.findall(
            ".//{http://www.w3.org/2000/svg}title[.='b']../{http://www.w3.org/2000/svg}ellipse"
        )
        assert len(b) == 1, "could not find node 'b'"
        yield float(b[0].attrib["cx"])

        # find `c`
        c = root.findall(
            ".//{http://www.w3.org/2000/svg}title[.='c']../{http://www.w3.org/2000/svg}ellipse"
        )
        assert len(c) == 1, "could not find node 'c'"
        yield float(c[0].attrib["cx"])

        # find `d`
        d = root.findall(
            ".//{http://www.w3.org/2000/svg}title[.='d']../{http://www.w3.org/2000/svg}ellipse"
        )
        assert len(d) == 1, "could not find node 'd'"
        yield float(d[0].attrib["cx"])

    # render the one without clusters and get its nodes’ X positions
    no_cluster_out = dot("svg", no_cluster)
    b, c, d = list(find_node_xs(no_cluster_out))

    # confirm we got a left → right ordering
    assert b < c, "unexpected horizontal node ordering"
    assert c < d, "unexpected horizontal node ordering"

    # now try the same thing with the clustered graph
    cluster_out = dot("svg", cluster)
    b, c, d = list(find_node_xs(cluster_out))
    assert b < c, "clustering altered nodes’ horizontal ordering"
    assert c < d, "clustering altered nodes’ horizontal ordering"


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_1594():
    """
    GVPR should give accurate line numbers in error messages
    https://gitlab.com/graphviz/graphviz/-/issues/1594
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1594.gvpr"

    # run GVPR with our (malformed) input program
    gvprbin = which("gvpr")
    with subprocess.Popen(
        [gvprbin, "-f", input],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, stderr = p.communicate()

        assert p.returncode != 0, "GVPR did not reject malformed program"

    assert "line 3:" in stderr, "GVPR did not identify correct line of syntax error"


@pytest.mark.parametrize(
    "device",
    (
        "png:cairo:gd",
        "png:cairo:gdiplus",
        pytest.param(
            "png:cairo:gdk",
            marks=pytest.mark.xfail(
                is_fedora() or is_ubuntu_2604(),
                strict=True,
                reason="https://gitlab.com/graphviz/graphviz/-/issues/2732",
            ),
        ),
        "png:cairo:quartz",
    ),
)
def test_1617(device: str):
    """
    DPI should be propagated to PNG outputs
    https://gitlab.com/graphviz/graphviz/-/issues/1617
    """

    # check if Graphviz was built with the plugin that provides this device
    p = subprocess.run(
        ["dot", "-Tpng:unrecognized", "-o", os.devnull, os.devnull],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if re.search(rf"\b{device}\b", p.stderr) is None:
        pytest.skip(f'"{device}" output device not supported')

    # run an example with DPI through Graphviz
    graph = 'digraph G { dpi = "300"; B->C; B->D; C->B; D->A; D->C; }'
    png = dot(device, source=graph)

    # interpret this with Pillow
    data = io.BytesIO(png)
    img = Image.open(data)

    # we should see the DPI propagated to the image
    default = 72
    dpi = img.info.get("dpi", (default, default))
    assert math.isclose(dpi[0], 300, abs_tol=1), "DPI not propagated to output"
    assert math.isclose(dpi[1], 300, abs_tol=1), "DPI not propagated to output"


@pytest.mark.parametrize("long,short", (("--help", "-?"), ("--version", "-V")))
def test_1618(long: str, short: str):
    """
    Graphviz should understand `--help` and `--version`
    https://gitlab.com/graphviz/graphviz/-/issues/1618
    """

    # run Graphviz with the short form of the argument
    p1 = subprocess.run(["dot", short], capture_output=True, check=True)

    # run it with the long form of the argument
    p2 = subprocess.run(["dot", long], capture_output=True, check=True)

    # output from both should match
    assert (
        p1.stdout == p2.stdout
    ), f"`dot {long}` wrote differing output than `dot {short}`"
    assert (
        p1.stderr == p2.stderr
    ), f"`dot {long}` wrote differing output than `dot {short}`"


@pytest.mark.parametrize(
    "test_case", ("1622_0.dot", "1622_1.dot", "1622_2.dot", "1622_3.dot")
)
def test_1622(test_case: str):
    """
    Narrow HTML table cells should not cause assertion failures
    https://gitlab.com/graphviz/graphviz/-/issues/1622
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / test_case
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("png:cairo:cairo", input)


def test_1624():
    """
    record shapes should be usable
    https://gitlab.com/graphviz/graphviz/-/issues/1624
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1624.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("svg", input)


def test_1644():
    """
    neato results should be deterministic
    https://gitlab.com/graphviz/graphviz/-/issues/1644
    """

    # get our baseline reference
    input = Path(__file__).parent / "1644.dot"
    assert input.exists(), "unexpectedly missing test case"
    neato = which("neato")
    ref = run(neato, input)

    # now repeat this, expecting it not to change
    for _ in range(20):
        out = run(neato, input)
        assert ref == out, "repeated rendering changed output"


@pytest.mark.parametrize("fmt", ("dot", "gif", "svg", "xdot"))
@pytest.mark.parametrize("layerselect", range(1, 6))
def test_1648(fmt: str, layerselect: int):
    """
    `layerselect` should not cause crashes
    https://gitlab.com/graphviz/graphviz/-/issues/1648

    Args:
        fmt: output format (`-T…`) to test
        layerselect: which layer to choose
    """

    # a graph with layers
    input = Path(__file__).parent / "graphs/layer.gv"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    run("dot", f"-Glayerselect={layerselect}", f"-T{fmt}", "-o", os.devnull, input)


@pytest.mark.parametrize(
    "fmt",
    (
        "bmp",
        "canon",
        "cmap",
        "cmapx",
        "cmapx_np",
        "dot",
        "dot_json",
        "eps",
        "fig",
        "gv",
        pytest.param(
            "ico",
            marks=pytest.mark.skipif(
                platform.system() == "Windows",
                reason="no 'ico'-supporting plugin available on Windows",
            ),
        ),
        "imap",
        "imap_np",
        "ismap",
        "jpe",
        "jpeg",
        "jpg",
        "json",
        "json0",
        "kitty",
        "kittyz",
        "pdf",
        "pic",
        "plain",
        "plain-ext",
        "png",
        "pov",
        "ps",
        "ps2",
        "svg",
        "svg_inline",
        "svgz",
        "tif",
        "tiff",
        "tk",
        "vt",
        "vt-24bit",
        "vt-4up",
        "vt-6up",
        "vt-8up",
        "xdot",
        "xdot1.2",
        "xdot1.4",
        "xdot_json",
    ),
)
def test_1648_1(fmt: str):
    """
    `layerselect` should not cause crashes
    https://gitlab.com/graphviz/graphviz/-/issues/1648
    https://forum.graphviz.org/t/segmentation-fault-when-using-layerselect/3077

    Args:
        fmt: output format (`-T…`) to test
    """

    # a simple arbitrary graph
    source = "graph {}"

    # run this through Graphviz
    run(
        "dot",
        f"-T{fmt}",
        "-Glayers=a, b",
        "-Glayerselect=b",
        "-o",
        os.devnull,
        input=source,
    )


def test_1658():
    """
    the graph associated with this test case should not crash Graphviz
    https://gitlab.com/graphviz/graphviz/-/issues/1658
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1658.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("png", input)


def test_1676():
    """
    https://gitlab.com/graphviz/graphviz/-/issues/1676
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1676.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run Graphviz with this input
    ret = subprocess.call(["dot", "-Tsvg", "-o", os.devnull, input])

    # this malformed input should not have caused Graphviz to crash
    assert ret != -signal.SIGSEGV, "Graphviz segfaulted"


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_1702():
    """
    GVPR library program `depath` should work on arbitrary examples
    https://gitlab.com/graphviz/graphviz/-/issues/1702
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1702.dot"
    assert input.exists(), "unexpectedly missing test case"

    # find the library program
    depath = Path(__file__).parents[1] / "cmd/gvpr/lib/depath"
    assert depath.exists(), "GVPR library program depath missing"

    # run GVPR
    gvpr_bin = which("gvpr")
    proc = subprocess.run(
        [gvpr_bin, "-c", "-o", os.devnull, "-f", depath, input],
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )

    assert proc.stderr.strip() == "", "depath errored on tests/1702.dot"


def test_1724():
    """
    passing malformed node and newrank should not cause segfaults
    https://gitlab.com/graphviz/graphviz/-/issues/1724
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1724.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run Graphviz with this input
    ret = subprocess.call(["dot", "-Tsvg", "-o", os.devnull, input])

    assert ret != -signal.SIGSEGV, "Graphviz segfaulted"


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_1767(tmp_path: Path):
    """
    using the Pango plugin multiple times should produce consistent results
    https://gitlab.com/graphviz/graphviz/-/issues/1767
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "1767.c").resolve()
    assert c_src.exists(), "missing test case"

    # find our co-located dot input
    src = (Path(__file__).parent / "1767.dot").resolve()
    assert src.exists(), "missing test case"

    stdout, _ = run_c(c_src, tmp_path, args=[src], link=["cgraph", "gvc"])

    assert stdout.splitlines() == [
        "Loaded graph:clusters",
        "cluster_0 contains 5 nodes",
        "cluster_1 contains 1 nodes",
        "cluster_2 contains 3 nodes",
        "cluster_3 contains 3 nodes",
        "Loaded graph:clusters",
        "cluster_0 contains 5 nodes",
        "cluster_1 contains 1 nodes",
        "cluster_2 contains 3 nodes",
        "cluster_3 contains 3 nodes",
    ]


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
@pytest.mark.skipif(platform.system() != "Windows", reason="only relevant on Windows")
def test_1780():
    """
    GVPR should accept programs at absolute paths
    https://gitlab.com/graphviz/graphviz/-/issues/1780
    """

    # get absolute path to an arbitrary GVPR program
    clustg = Path(__file__).resolve().parent.parent / "cmd/gvpr/lib/clustg"

    # GVPR should not fail when given this path
    gvpr(clustg)


def test_1783():
    """
    Graphviz should not segfault when passed large edge weights
    https://gitlab.com/graphviz/graphviz/-/issues/1783
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1783.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run Graphviz with this input
    ret = subprocess.call(["dot", "-Tsvg", "-o", os.devnull, input])

    assert ret != 0, "Graphviz accepted illegal edge weight"

    assert ret != -signal.SIGSEGV, "Graphviz segfaulted"


@pytest.mark.skipif(which("gvedit") is None, reason="Gvedit not available")
def test_1813():
    """
    gvedit -? should show usage
    https://gitlab.com/graphviz/graphviz/-/issues/1813
    """

    environ_copy = os.environ.copy()
    environ_copy.pop("DISPLAY", None)
    gvedit = which("gvedit")
    output = run(gvedit, "-?", env=environ_copy)

    assert "Usage" in output, "gvedit -? did not show usage"


def test_1845():
    """
    rendering sequential graphs to PS should not segfault
    https://gitlab.com/graphviz/graphviz/-/issues/1845
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1845.dot"
    assert input.exists(), "unexpectedly missing test case"

    # generate a multipage PS file from this input
    dot("ps", input)


@pytest.mark.xfail(strict=True)  # FIXME
def test_1856():
    """
    headports and tailports should be respected
    https://gitlab.com/graphviz/graphviz/-/issues/1856
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1856.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it into JSON
    out = dot("json", input)
    data = json.loads(out)

    # find the two nodes, “3” and “5”
    three = [x for x in data["objects"] if x["name"] == "3"][0]
    five = [x for x in data["objects"] if x["name"] == "5"][0]

    # find the edge from “3” to “5”
    edge = [
        x
        for x in data["edges"]
        if x["tail"] == three["_gvid"] and x["head"] == five["_gvid"]
    ][0]

    # The edge should look something like:
    #
    #        ┌─┐
    #        │3│
    #        └┬┘
    #    ┌────┘
    #   ┌┴┐
    #   │5│
    #   └─┘
    #
    # but a bug causes port constraints to not be respected and the edge comes out
    # more like:
    #
    #        ┌─┐
    #        │3│
    #        └┬┘
    #         │
    #   ┌─┐   │
    #   ├5̶┼───┘
    #   └─┘
    #
    # So validate that the edge’s path does not dip below the top of the “5” node.

    top_of_five = max(y for _, y in five["_draw_"][1]["points"])

    waypoints_y = [y for _, y in edge["_draw_"][1]["points"]]

    assert all(y >= top_of_five for y in waypoints_y), "edge dips below 5"


@pytest.mark.skipif(which("fdp") is None, reason="fdp not available")
def test_1865():
    """
    fdp should not read out of bounds when processing node names
    https://gitlab.com/graphviz/graphviz/-/issues/1865
    Note, the crash this test tries to provoke may only occur when run under
    Address Sanitizer or Valgrind
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1865.dot"
    assert input.exists(), "unexpectedly missing test case"

    # fdp should not crash when processing this file
    fdp = which("fdp")
    run_raw(fdp, "-o", os.devnull, input)


@pytest.mark.skipif(which("gv2gml") is None, reason="gv2gml not available")
@pytest.mark.skipif(which("gml2gv") is None, reason="gml2gv not available")
@pytest.mark.parametrize(
    "penwidth",
    (pytest.param("1.0", id="penwidth=1.0"), pytest.param("1", id="pendwidth=1")),
)
def test_1871(penwidth: str):
    """
    round tripping something with either an integer or real `penwidth` through
    gv2gml→gml2gv should return the correct `penwidth`
    """

    # a trivial graph
    input = f"graph {{ a [penwidth={penwidth}] }}"

    # pass it through gv2gml
    gv2gml = which("gv2gml")
    gv = run(gv2gml, input=input)

    # pass this through gml2gv
    gml2gv = which("gml2gv")
    gml = run(gml2gv, input=gv)

    # the result should have a `penwidth` of 1
    has_1 = re.search(r"\bpenwidth\s*=\s*1[^\.]", gml) is not None
    has_1_0 = re.search(r"\bpenwidth\s*=\s*1\.0\b", gml) is not None
    assert (
        has_1 or has_1_0
    ), f"incorrect penwidth from round tripping through GML (output {gml})"


@pytest.mark.skipif(which("fdp") is None, reason="fdp not available")
def test_1876():
    """
    fdp should not rename nodes with internal names
    https://gitlab.com/graphviz/graphviz/-/issues/1876
    """

    # a trivial graph to provoke this issue
    input = "graph { a }"

    # process this with fdp
    fdp = which("fdp")
    try:
        output = run(fdp, input=input)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("fdp failed to process trivial graph") from e

    # we should not see any internal names like "%3"
    assert "%" not in output, "internal name in fdp output"


@pytest.mark.skipif(which("fdp") is None, reason="fdp not available")
def test_1877():
    """
    fdp should not fail an assertion when processing cluster edges
    https://gitlab.com/graphviz/graphviz/-/issues/1877
    """

    # simple input with a cluster edge
    input = "graph {subgraph cluster_a {}; cluster_a -- b}"

    # fdp should be able to process this
    fdp = which("fdp")
    run(fdp, "-o", os.devnull, input=input)


def test_1880():
    """
    parsing a particular graph should not cause a Trapezoid-table overflow
    assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/1880
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1880.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    dot("png", input)


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/1887"
)
def test_1887(tmp_path: Path):
    """
    empty strings as labels should be propagated to dot output
    https://gitlab.com/graphviz/graphviz/-/issues/1887
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "1887.c").resolve()
    assert c_src.exists(), "missing test case"

    # generate a graph and pass it through dot
    stdout, _ = run_c(c_src, tmp_path, link=["cgraph"])

    assert (
        re.search(r'label\s*=\s*""', stdout) is not None
    ), "empty label missing in output"


def test_1896():
    """
    this graph should not crash Graphviz
    https://gitlab.com/graphviz/graphviz/-/issues/1896
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1896.dot"
    assert input.exists(), "unexpectedly missing test case"

    for _ in range(10):
        dot("xdot1.2", input)


def test_1898():
    """
    test a segfault from https://gitlab.com/graphviz/graphviz/-/issues/1898 has
    not reappeared
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1898.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it, which should generate a segfault if this bug
    # has been reintroduced
    dot("svg", input)


def test_1902():
    """
    test a segfault from https://gitlab.com/graphviz/graphviz/-/issues/1902 has
    not reappeared
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1902.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it, which should generate a segfault if this bug
    # has been reintroduced
    dot("svg", input)


# root directory of this checkout
ROOT = Path(__file__).parent.parent.resolve()


def test_1855():
    """
    SVGs should have a scale with sufficient precision
    https://gitlab.com/graphviz/graphviz/-/issues/1855
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "1855.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    svg = dot("svg", src)

    # find the graph element
    root = ET.fromstring(svg)
    graph = root[0]
    assert graph.get("class") == "graph", "could not find graph element"

    # extract its `transform` attribute
    transform = graph.get("transform")

    # this should begin with a scale directive
    m = re.match(r"scale\((?P<x>\d+(\.\d*)?) (?P<y>\d+(\.\d*))\)", transform)
    assert m is not None, f"failed to find 'scale' in '{transform}'"

    x = m.group("x")
    y = m.group("y")

    # the scale should be somewhere in reasonable range of what is expected
    assert float(x) >= 0.32 and float(x) <= 0.34, "inaccurate x scale"
    assert float(y) >= 0.32 and float(y) <= 0.34, "inaccurate y scale"

    # two digits of precision are insufficient for this example, so require a
    # greater number of digits in both scale components
    assert len(x) > 4, "insufficient precision in x scale"
    assert len(y) > 4, "insufficient precision in y scale"


@pytest.mark.parametrize("variant", [1, 2])
@pytest.mark.skipif(which("gml2gv") is None, reason="gml2gv not available")
def test_1869(variant: int):
    """
    gml2gv should be able to parse the style, outlineStyle, width and
    outlineWidth GML attributes and map them to the DOT attributes
    style and penwidth respectively
    https://gitlab.com/graphviz/graphviz/-/issues/1869
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / f"1869-{variant}.gml"
    assert input.exists(), "unexpectedly missing test case"

    # ask gml2gv to translate it to DOT
    gml2gv = which("gml2gv")
    output = run(gml2gv, input)

    assert "style=dashed" in output, "style=dashed not found in DOT output"
    assert "penwidth=2" in output, "penwidth=2 not found in DOT output"


def test_1879():
    """https://gitlab.com/graphviz/graphviz/-/issues/1879"""

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1879.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with DOT
    stdout = run(
        "dot",
        "-Tsvg",
        "-o",
        os.devnull,
        input,
        cwd=Path(__file__).parent,
        stderr=subprocess.STDOUT,
    )

    # check we did not trigger an assertion failure
    assert re.search(r"\bAssertion\b.*\bfailed\b", stdout) is None


def test_1879_2():
    """
    another variant of lhead/ltail + compound
    https://gitlab.com/graphviz/graphviz/-/issues/1879
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1879-2.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with DOT
    run_raw("dot", "-Gmargin=0", "-Tpng", "-o", os.devnull, input)


def test_1893():
    """
    an HTML label containing just a ] should work
    https://gitlab.com/graphviz/graphviz/-/issues/1893
    """

    # a graph containing a node with an HTML label with a ] in a table cell
    input = "digraph { 0 [label=<<TABLE><TR><TD>]</TD></TR></TABLE>>] }"

    # ask Graphviz to process this
    dot("svg", source=input)

    # we should be able to do the same with an escaped ]
    input = "digraph { 0 [label=<<TABLE><TR><TD>&#93;</TD></TR></TABLE>>] }"

    dot("svg", source=input)


def test_1906():
    """
    graphs that generate large rectangles should be accepted
    https://gitlab.com/graphviz/graphviz/-/issues/1906
    """

    # one of the rtest graphs is sufficient to provoke this
    input = Path(__file__).parent / "graphs/root.gv"
    assert input.exists(), "unexpectedly missing test case"

    # use Circo to translate it to DOT
    run_raw("dot", "-Kcirco", "-Tgv", "-o", os.devnull, input)


@pytest.mark.skipif(which("twopi") is None, reason="twopi not available")
def test_1907():
    """
    SVG edges should have title elements that match their names
    https://gitlab.com/graphviz/graphviz/-/issues/1907
    """

    # a trivial graph to provoke this issue
    input = "digraph { A -> B -> C }"

    # generate an SVG from this input with twopi
    twopi = which("twopi")
    output = run(twopi, "-Tsvg", input=input)

    assert "<title>A&#45;&gt;B</title>" in output, "element title not found in SVG"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_1909():
    """
    GVPR should not output internal names
    https://gitlab.com/graphviz/graphviz/-/issues/1909
    """

    # locate our associated test case in this directory
    prog = Path(__file__).parent / "1909.gvpr"
    graph = Path(__file__).parent / "1909.dot"

    # run GVPR with the given input
    gvprbin = which("gvpr")
    output = run(gvprbin, "-c", "-f", prog, graph)

    # we should have produced this graph without names like "%2" in it
    assert re.search(r"%\d+\b", output) is None


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_1910(tmp_path: Path):
    """
    Repeatedly using agmemread() should have consistent results
    https://gitlab.com/graphviz/graphviz/-/issues/1910
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "1910.c").resolve()
    assert c_src.exists(), "missing test case"

    # run the test
    _, _ = run_c(c_src, tmp_path, link=["cgraph", "gvc"])


def test_1913():
    """
    ALIGN attributes in <BR> tags should be parsed correctly
    https://gitlab.com/graphviz/graphviz/-/issues/1913
    """

    # a template of a trivial graph using an ALIGN attribute
    graph = (
        "digraph {{\n"
        '  table1[label=<<table><tr><td align="text">hello world'
        '<br align="{}"/></td></tr></table>>];\n'
        "}}"
    )

    def execute(input):
        """
        run Dot with the given input and return its exit status and stderr
        """
        with subprocess.Popen(
            ["dot", "-Tsvg", "-o", os.devnull],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as p:
            _, stderr = p.communicate(input)
            return p.returncode, remove_asan_summary(remove_xtype_warnings(stderr))

    # Graphviz should accept all legal values for this attribute
    for align in ("left", "right", "center"):
        input = align
        ret, stderr = execute(graph.format(input))
        assert ret == 0
        assert stderr.strip() == ""

        # these attributes should also be valid when title cased
        input = f"{align[0].upper()}{align[1:]}"
        ret, stderr = execute(graph.format(input))
        assert ret == 0
        assert stderr.strip() == ""

        # similarly, they should be valid when upper cased
        input = align.upper()
        ret, stderr = execute(graph.format(input))
        assert ret == 0
        assert stderr.strip() == ""

    # various invalid things that have the same prefix or suffix as a valid
    # alignment should be rejected
    for align in ("lamp", "deft", "round", "might", "circle", "venter"):
        input = align
        _, stderr = execute(graph.format(input))
        assert f"Warning: Illegal value {input} for ALIGN - ignored" in stderr

        # these attributes should also fail when title cased
        input = f"{align[0].upper()}{align[1:]}"
        _, stderr = execute(graph.format(input))
        assert f"Warning: Illegal value {input} for ALIGN - ignored" in stderr

        # similarly, they should fail when upper cased
        input = align.upper()
        _, stderr = execute(graph.format(input))
        assert f"Warning: Illegal value {input} for ALIGN - ignored" in stderr


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/1925"
)
def test_1925():
    """
    GVPR `hasAttr` should work accurately
    https://gitlab.com/graphviz/graphviz/-/issues/1925
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1925.dot"
    assert input.exists(), "unexpectedly missing test case"
    script = Path(__file__).parent / "1925.gvpr"
    assert script.exists(), "unexpectedly missing test case"

    # run GVPR
    gvpr_bin = which("gvpr")
    stdout = run(gvpr_bin, "-c", "-f", script, input)

    # check we got expected results
    styled = set(["L"])
    minlened = set(["S->T"])
    active = None
    for line in stdout.split("\n"):
        if m := re.match("// (NODE|EDGE): (?P<name>.*)$", line):
            active = m.group("name")
            continue
        if m := re.match(r"//\s+style :: (?P<value>0|1)$", line):
            assert active is not None, "style line with no known node/edge"
            if m.group("value") == "0":
                assert (
                    active not in styled
                ), f"{active} incorrectly considered to have 'style' attribute"
            else:
                assert (
                    active in styled
                ), f"{active} incorrectly considered to not have 'style' attribute"
        if m := re.match(r"//\s+minlen :: (?P<value>0|1)$", line):
            assert active is not None, "minlen line with no known node/edge"
            if m.group("value") == "0":
                assert (
                    active not in minlened
                ), f"{active} incorrectly considered to have 'minlen' attribute"
            else:
                assert (
                    active in minlened
                ), f"{active} incorrectly considered to not have 'minlen' attribute"


def test_1931():
    """
    New lines within strings should not be discarded during parsing
    https://gitlab.com/graphviz/graphviz/-/issues/1931
    """

    # a graph with \n inside of strings
    graph = (
        "graph {\n"
        '  node1 [label="line 1\n'
        "line 2\n"
        '"];\n'
        '  node2 [label="line 3\n'
        'line 4"];\n'
        "  node1 -- node2\n"
        '  node2 -- "line 5\n'
        'line 6"\n'
        "}"
    )

    # ask Graphviz to process this to dot output
    xdot = dot("xdot", source=graph)

    # all new lines in strings should have been preserved
    assert "line 1\nline 2\n" in xdot
    assert "line 3\nline 4" in xdot
    assert "line 5\nline 6" in xdot


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/1939"
)
def test_1939():
    """
    clustering should not cause “trouble in init_rank” errors
    https://gitlab.com/graphviz/graphviz/-/issues/1939
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1939.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


def test_1949():
    """
    rankdir=LR + compound=true should not lead to an assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/1949
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1949.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("png", input)


@pytest.mark.skipif(which("edgepaint") is None, reason="edgepaint not available")
def test_1971():
    """
    edgepaint should reject invalid command line options
    https://gitlab.com/graphviz/graphviz/-/issues/1971
    """

    # a basic graph that edgepaint can process
    input = (
        "digraph {\n"
        '  graph [bb="0,0,54,108"];\n'
        '  node [label="\\N"];\n'
        "  a       [height=0.5,\n"
        '           pos="27,90",\n'
        "           width=0.75];\n"
        "  b       [height=0.5,\n"
        '           pos="27,18",\n'
        "           width=0.75];\n"
        '  a -> b  [pos="e,27,36.104 27,71.697 27,63.983 27,54.712 27,46.112"];\n'
        "}"
    )

    # run edgepaint with an invalid option, `-rabbit`, that happens to have the
    # same first character as valid options
    args = [which("edgepaint"), "-rabbit"]
    with subprocess.Popen(args, stdin=subprocess.PIPE, text=True) as p:
        p.communicate(input)

        assert p.returncode != 0, "edgepaint incorrectly accepted '-rabbit'"


def test_1990():
    """
    using ortho and circo in combination should not cause an assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/14
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "1990.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    circo = which("circo")
    run_raw(circo, "-Tsvg", "-o", os.devnull, input)


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_2057(tmp_path: Path):
    """
    gvToolTred should be usable by user code
    https://gitlab.com/graphviz/graphviz/-/issues/2057
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2057.c").resolve()
    assert c_src.exists(), "missing test case"

    # run the test
    _, _ = run_c(c_src, tmp_path, link=["gvc"])


def test_2078():
    """
    Incorrectly using the "layout" attribute on a subgraph should result in a
    sensible error.
    https://gitlab.com/graphviz/graphviz/-/issues/2078
    """

    # our sample graph that incorrectly uses layout
    input = "graph {\n  subgraph {\n    layout=osage\n  }\n}"

    # run it through Graphviz
    with subprocess.Popen(
        ["dot", "-Tcanon", "-o", os.devnull],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, stderr = p.communicate(input)

        assert p.returncode != 0, "layout on subgraph was incorrectly accepted"

    assert (
        "layout attribute is invalid except on the root graph" in stderr
    ), "expected warning not found"

    # a graph that correctly uses layout
    input = "graph {\n  layout=osage\n  subgraph {\n  }\n}"

    # ensure this one does not trigger warnings
    with subprocess.Popen(
        ["dot", "-Tcanon", "-o", os.devnull],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        stdout, stderr = p.communicate(input)

        assert p.returncode == 0, f"correct layout use was rejected: {stdout}{stderr}"

    assert stdout.strip() == "", "unexpected output"
    assert (
        "layout attribute is invalid except on the root graph" not in stderr
    ), "incorrect warning output"


def test_2082():
    """
    Check a bug in inside_polygon has not been reintroduced.
    https://gitlab.com/graphviz/graphviz/-/issues/2082
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2082.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it, which should generate an assertion failure if
    # this bug has been reintroduced
    dot("png", input)


def test_2087():
    """
    spline routing should be aware of and ignore concentrated edges
    https://gitlab.com/graphviz/graphviz/-/issues/2087
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2087.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process it with Graphviz
    warnings = run("dot", "-Tpng", "-o", os.devnull, input, stderr=subprocess.STDOUT)

    # work around macOS warnings
    warnings = remove_xtype_warnings(warnings).strip()

    # work around ASan informational printing
    warnings = remove_asan_summary(warnings)

    # no warnings should have been printed
    assert (
        warnings == ""
    ), "warnings were printed when processing concentrated duplicate edges"


@pytest.mark.parametrize("html_like_first", (False, True))
def test_2089(html_like_first: bool):
    """
    HTML-like and non-HTML-like strings should peacefully coexist
    https://gitlab.com/graphviz/graphviz/-/issues/2089
    """

    # a graph using an HTML-like string and a non-HTML-like string
    if html_like_first:
        graph = 'graph {\n  a[label=<foo>];\n  b[label="foo"];\n}'
    else:
        graph = 'graph {\n  a[label="foo"];\n  b[label=<foo>];\n}'

    # normalize the graph
    canonical = dot("dot", source=graph)

    assert "label=foo" in canonical, "non-HTML-like label not found"
    assert "label=<foo>" in canonical, "HTML-like label not found"


def test_2089_2(tmp_path: Path):
    """
    HTML-like and non-HTML-like strings should peacefully coexist
    https://gitlab.com/graphviz/graphviz/-/issues/2089
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2089.c").resolve()
    assert c_src.exists(), "missing test case"

    # run it
    link = ["cgraph"]
    if is_static_build():
        # in static builds, we also need transitive dependencies
        link += ["cdt"]
    _, _ = run_c(c_src, tmp_path, link=link)


@pytest.mark.skipif(which("dot2gxl") is None, reason="dot2gxl not available")
def test_2092():
    """
    an empty node ID should not cause a dot2gxl NULL pointer dereference
    https://gitlab.com/graphviz/graphviz/-/issues/2092
    """
    dot2gxl = which("dot2gxl")
    p = subprocess.run([dot2gxl, "-d"], input='<node id="">', check=False, text=True)

    assert p.returncode != 0, "dot2gxl accepted invalid input"

    assert p.returncode == 1, "dot2gxl crashed"


@pytest.mark.skipif(which("dot2gxl") is None, reason="dot2gxl not available")
def test_2093():
    """
    dot2gxl should handle elements with no ID
    https://gitlab.com/graphviz/graphviz/-/issues/2093
    """
    dot2gxl = which("dot2gxl")
    with subprocess.Popen([dot2gxl, "-d"], stdin=subprocess.PIPE, text=True) as p:
        p.communicate('<graph x="">')

        assert p.returncode == 1, "dot2gxl did not reject missing ID"


@pytest.mark.skipif(which("dot2gxl") is None, reason="dot2gxl not available")
def test_2094():
    """
    dot2gxl should not crash when decoding a closing node tag after a closing
    graph tag
    https://gitlab.com/graphviz/graphviz/-/issues/2094
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2094.xml"
    assert input.exists(), "unexpectedly missing test case"

    dot2gxl = which("dot2gxl")
    ret = subprocess.call([dot2gxl, "-d", input])

    assert ret in (
        0,
        1,
    ), "dot2gxl crashed when processing a closing node tag after a closing graph tag"
    assert ret == 1, "dot2gxl did not reject malformed XML"


def test_2095():
    """
    Exceeding 1000 boxes during computation should not cause a crash
    https://gitlab.com/graphviz/graphviz/-/issues/2095
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2095.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to process it
    dot("pdf", input)


def test_2095_1():
    """
    more than 1000 boxes should still be processed in reasonable time
    https://gitlab.com/graphviz/graphviz/-/issues/2095
    https://gitlab.com/graphviz/graphviz/-/merge_requests/2854
    https://gitlab.com/graphviz/graphviz/-/merge_requests/2857
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2095.dot"
    assert input.exists(), "unexpectedly missing test case"

    timeout = 60  # seconds
    if platform.system() == "Windows":
        timeout *= 2

    # this typically takes ~1s to run, so give a wide margin of error and require that
    # that Graphviz finishes within that
    run_raw("dot", "-Tpdf", "-o", os.devnull, input, timeout=timeout)


@pytest.mark.skipif(which("gv2gml") is None, reason="gv2gml not available")
def test_2131():
    """
    gv2gml should be able to process basic Graphviz input
    https://gitlab.com/graphviz/graphviz/-/issues/2131
    """

    # a trivial graph
    input = "digraph { a -> b; }"

    # ask gv2gml what it thinks of this
    gv2gml = which("gv2gml")
    try:
        run(gv2gml, input=input)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("gv2gml rejected a basic graph") from e


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
@pytest.mark.parametrize("examine", ("indices", "tokens"))
def test_2138(examine: str):
    """
    gvpr splitting and tokenizing should not result in trailing garbage
    https://gitlab.com/graphviz/graphviz/-/issues/2138
    """

    # find our co-located GVPR program
    script = (Path(__file__).parent / "2138.gvpr").resolve()
    assert script.exists(), "missing test case"

    # run it with NUL input
    gvprbin = which("gvpr")
    out = run_raw(gvprbin, "-f", script, stdin=subprocess.DEVNULL)

    # Decode into text. We do this instead of `text=True` above because the trailing
    # garbage can contain invalid UTF-8 data causing cryptic failures. We want to
    # correctly surface this as trailing garbage, not an obscure UTF-8 decoding error.
    result = out.decode("utf-8", "replace")

    if examine == "indices":
        # check no indices are miscalculated
        index_re = (
            r"^// index of space \(st\) :\s*(?P<index>-?\d+)\s*<< must "
            r"NOT be less than -1$"
        )
        for m in re.finditer(index_re, result, flags=re.MULTILINE):
            index = int(m.group("index"))
            assert index >= -1, "illegal index computed"

    if examine == "tokens":
        # check for text the author of 2138.gvpr expected to find
        assert (
            "// tok[3]    >>3456789<<   should NOT include trailing spaces or "
            "junk chars" in result
        ), "token 3456789 not found or has trailing garbage"
        assert (
            "// tok[7]    >>012<<   should NOT include trailing spaces or "
            "junk chars" in result
        ), "token 012 not found or has trailing garbage"


def test_2159():
    """
    space for HTML TDs should be allocated equally when expanding to fill a TR
    https://gitlab.com/graphviz/graphviz/-/issues/2159
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2159.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    # load it as XML
    root = ET.fromstring(svg)

    # this node is expected to contain:
    #   • 1 polygon for the top column-spanning cell
    #   • 5 polygons for the bottom row’s cells
    #   • 1 polygon for the outer table border
    polygons = root.findall(
        ".//{http://www.w3.org/2000/svg}title[.='node1']/../{http://www.w3.org/2000/svg}polygon"
    )
    assert len(polygons) == 7

    # Polygon emission order is not stable across render backends/changes.
    # Build geometry for each rectangular polygon and identify rows by position.
    rects = []
    for polygon in polygons:
        points = [
            [float(n) for n in p.split(",")] for p in polygon.get("points").split(" ")
        ]
        assert len(points) == 5, "polygon not rectangular"
        (ul_x, _), (ll_x, _), (lr_x, _), (ur_x, _), (orig_x, _) = points
        assert ul_x == ll_x, "polygon left edge is not vertical"
        assert lr_x == ur_x, "polygon right edge is not vertical"
        assert orig_x == ul_x, "polygon is not closed"

        xs = [p[0] for p in points[:-1]]
        ys = [p[1] for p in points[:-1]]
        rects.append(
            {
                "top": min(ys),
                "width": max(xs) - min(xs),
                "area": (max(xs) - min(xs)) * (max(ys) - min(ys)),
            }
        )

    # Exclude the table border (largest area), then identify the single top row
    # cell and the five bottom-row cells by y-position.
    border = max(rects, key=lambda r: r["area"])
    cells = [r for r in rects if r is not border]
    assert len(cells) == 6, "unexpected cell polygon count"

    top_row = min(cells, key=lambda r: r["top"])
    bottom_row = [r for r in cells if r is not top_row]
    assert len(bottom_row) == 5, "unexpected number of bottom-row cells"

    widths = [cell["width"] for cell in bottom_row]

    # these should approximately sum to the width of the top row
    assert math.isclose(
        sum(widths), top_row["width"], abs_tol=10
    ), "bottom row not expanded to fill the space"

    # the width of each cell should be approximately equal
    for width in widths[1:]:
        assert math.isclose(width, widths[0], abs_tol=5), "cells not evenly expanded"


def test_2168():
    """
    using spline routing should not cause fdp/neato to infinite loop
    https://gitlab.com/graphviz/graphviz/-/issues/2168
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2168.dot"
    assert input.exists(), "unexpectedly missing test case"

    timeout = 5
    if platform.system() == "Windows":
        timeout *= 2

    fdp = which("fdp")
    run_raw(fdp, "-o", os.devnull, input, timeout=timeout)


def test_2168_1():
    """
    using spline routing should not cause fdp/neato to infinite loop
    https://gitlab.com/graphviz/graphviz/-/issues/2168
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2168_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    timeout = 5
    if platform.system() == "Windows":
        timeout *= 2

    fdp = which("fdp")
    run_raw(fdp, "-o", os.devnull, input, timeout=timeout)


def test_2168_2():
    """
    using spline routing should not cause fdp/neato to infinite loop
    https://gitlab.com/graphviz/graphviz/-/issues/2168
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2168_2.dot"
    assert input.exists(), "unexpectedly missing test case"

    timeout = 5
    if platform.system() == "Windows":
        timeout *= 2

    fdp = which("fdp")
    run_raw(fdp, "-o", os.devnull, input, timeout=timeout)


def test_2168_3():
    """
    using spline routing should not cause fdp/neato to infinite loop
    https://gitlab.com/graphviz/graphviz/-/issues/2168
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2168_3.dot"
    assert input.exists(), "unexpectedly missing test case"

    timeout = 5
    if platform.system() == "Windows":
        timeout *= 2

    fdp = which("fdp")
    run_raw(fdp, "-o", os.devnull, input, timeout=timeout)


def test_2168_4():
    """
    using spline routing should not cause fdp/neato to infinite loop
    https://gitlab.com/graphviz/graphviz/-/issues/2168
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2168_4.dot"
    assert input.exists(), "unexpectedly missing test case"

    timeout = 5
    if platform.system() == "Windows":
        timeout *= 2

    fdp = which("fdp")
    run_raw(fdp, "-o", os.devnull, input, timeout=timeout)


def test_2168_5():
    """
    using spline routing should not cause fdp/neato to infinite loop
    https://gitlab.com/graphviz/graphviz/-/issues/2168
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2168_5.dot"
    assert input.exists(), "unexpectedly missing test case"

    fdp = which("fdp")
    out = run(fdp, "-o", os.devnull, input, stderr=subprocess.STDOUT)

    assert (
        "Warning: the bounding boxes of some nodes touch - falling back to straight line edges"
        in out
    )


def test_2179():
    """
    processing a label with an empty line should not yield a warning
    https://gitlab.com/graphviz/graphviz/-/issues/2179
    """

    # a graph containing a label with an empty line
    input = 'digraph "" {\n  0 -> 1 [fontname="Lato",label=<<br/>1>]\n}'

    # run a graph with an empty label through Graphviz
    with subprocess.Popen(
        ["dot", "-Tsvg", "-o", os.devnull],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, stderr = p.communicate(input)

        assert p.returncode == 0

    assert (
        "Warning: no hard-coded metrics for" not in stderr
    ), "incorrect warning triggered"


def test_2179_1():
    """
    processing a label with a line containing only a space should not yield a
    warning
    https://gitlab.com/graphviz/graphviz/-/issues/2179
    """

    # a graph containing a label with a line containing only a space
    input = 'digraph "" {\n  0 -> 1 [fontname="Lato",label=< <br/>1>]\n}'

    # run a graph with an empty label through Graphviz
    with subprocess.Popen(
        ["dot", "-Tsvg", "-o", os.devnull],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, stderr = p.communicate(input)

        assert p.returncode == 0

    assert (
        "Warning: no hard-coded metrics for" not in stderr
    ), "incorrect warning triggered"


def test_2183():
    """
    processing `splines=ortho`, `concentrate=true` should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/2183
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2183.dot"
    assert input.exists(), "unexpectedly missing test case"

    run_raw("dot", "-Tsvg", "-G8.5,11!", "-o", os.devnull, input)


@pytest.mark.skipif(which("nop") is None, reason="nop not available")
def test_2184_1():
    """
    nop should not reposition labelled graph nodes
    https://gitlab.com/graphviz/graphviz/-/issues/2184
    """

    # run `nop` on a sample with a labelled graph node at the end
    source = Path(__file__).parent / "2184.dot"
    assert source.exists(), "missing test case"
    nop = which("nop")
    nopped = run(nop, source)

    # the normalized output should have a graph with no label within
    # `clusterSurround1`
    m = re.search(
        r"\bclusterSurround1\b.*\bgraph\b.*\bcluster1\b", nopped, flags=re.DOTALL
    )
    assert m is not None, "nop rearranged a graph in a not-semantically-preserving way"


def test_2184_2():
    """
    canonicalization should not reposition labelled graph nodes
    https://gitlab.com/graphviz/graphviz/-/issues/2184
    """

    # canonicalize a sample with a labelled graph node at the end
    source = Path(__file__).parent / "2184.dot"
    assert source.exists(), "missing test case"
    canonicalized = dot("canon", source)

    # the canonicalized output should have a graph with no label within
    # `clusterSurround1`
    m = re.search(
        r"\bclusterSurround1\b.*\bgraph\b.*\bcluster1\b", canonicalized, flags=re.DOTALL
    )
    assert (
        m is not None
    ), "`dot -Tcanon` rearranged a graph in a not-semantically-preserving way"


def test_2185_1():
    """
    GVPR should deal with strings correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2185
    """

    # find our collocated GVPR program
    script = Path(__file__).parent / "2185.gvpr"
    assert script.exists(), "missing test case"

    # run this with NUL input, checking output is valid UTF-8
    gvpr(script)


def test_2185_2():
    """
    GVPR should deal with strings correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2185
    """

    # find our collocated GVPR program
    script = Path(__file__).parent / "2185.gvpr"
    assert script.exists(), "missing test case"

    # run this with NUL input
    gvprbin = which("gvpr")
    out = run_raw(gvprbin, "-f", script, stdin=subprocess.DEVNULL)

    # decode output in a separate step to gracefully cope with garbage unicode
    out = out.decode("utf-8", "replace")

    # deal with Windows eccentricities
    eol = "\r\n" if platform.system() == "Windows" else "\n"
    expected = f"one two three{eol}"

    # check the first line is as expected
    assert out.startswith(expected), "incorrect GVPR interpretation"


def test_2185_3():
    """
    GVPR should deal with strings correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2185
    """

    # find our collocated GVPR program
    script = Path(__file__).parent / "2185.gvpr"
    assert script.exists(), "missing test case"

    # run this with NUL input
    gvprbin = which("gvpr")
    out = run_raw(gvprbin, "-f", script, stdin=subprocess.DEVNULL)

    # decode output in a separate step to gracefully cope with garbage unicode
    out = out.decode("utf-8", "replace")

    # deal with Windows eccentricities
    eol = "\r\n" if platform.system() == "Windows" else "\n"
    expected = f"one two three{eol}one  five three{eol}"

    # check the first two lines are as expected
    assert out.startswith(expected), "incorrect GVPR interpretation"


def test_2185_4():
    """
    GVPR should deal with strings correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2185
    """

    # find our collocated GVPR program
    script = Path(__file__).parent / "2185.gvpr"
    assert script.exists(), "missing test case"

    # run this with NUL input
    gvprbin = which("gvpr")
    out = run_raw(gvprbin, "-f", script, stdin=subprocess.DEVNULL)

    # decode output in a separate step to gracefully cope with garbage unicode
    out = out.decode("utf-8", "replace")

    # deal with Windows eccentricities
    eol = "\r\n" if platform.system() == "Windows" else "\n"
    expected = f"one two three{eol}one  five three{eol}99{eol}"

    # check the first three lines are as expected
    assert out.startswith(expected), "incorrect GVPR interpretation"


def test_2185_5():
    """
    GVPR should deal with strings correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2185
    """

    # find our collocated GVPR program
    script = Path(__file__).parent / "2185.gvpr"
    assert script.exists(), "missing test case"

    # run this with NUL input
    gvprbin = which("gvpr")
    out = run_raw(gvprbin, "-f", script, stdin=subprocess.DEVNULL)

    # decode output in a separate step to gracefully cope with garbage unicode
    out = out.decode("utf-8", "replace")

    # deal with Windows eccentricities
    eol = "\r\n" if platform.system() == "Windows" else "\n"
    expected = f"one two three{eol}one  five three{eol}99{eol}Constant{eol}"

    # check the first four lines are as expected
    assert out.startswith(expected), "incorrect GVPR interpretation"


def test_2193():
    """
    the canonical format should be stable
    https://gitlab.com/graphviz/graphviz/-/issues/2193
    """

    # find our collocated test case
    input = Path(__file__).parent / "2193.dot"
    assert input.exists(), "unexpectedly missing test case"

    # derive the initial canonicalization
    canonical = dot("canon", input)

    # now canonicalize this again to see if it changes
    new = dot("canon", source=canonical)
    assert canonical == new, "canonical translation is not stable"


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_2211():
    """
    GVPR’s `index` function should return correct results
    https://gitlab.com/graphviz/graphviz/-/issues/2211
    """

    # find our collocated test case
    program = Path(__file__).parent / "2211.gvpr"
    assert program.exists(), "unexpectedly missing test case"

    # run it through GVPR
    output = gvpr(program)

    # it should have found the right string indices for characters
    assert (
        output == "index: 9  should be 9\n"
        "index: 3  should be 3\n"
        "index: -1  should be -1\n"
    )


def test_2215():
    """
    Graphviz should not crash with `-v`
    https://gitlab.com/graphviz/graphviz/-/issues/2215
    """

    # try it on a simple graph
    input = "graph g { a -- b; }"
    run("dot", "-v", input=input)

    # try the same on a labelled version of this graph
    input = 'graph g { node[label=""] a -- b; }'
    run("dot", "-v", input=input)


@pytest.mark.xfail(
    is_rocky(),
    strict=True,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2241",
)
def test_2241():
    """
    a graph with two nodes and one edge in each direction should be rendered
    with two visually distinct edges when using the neato engine and
    splines=true, not two edges on top of each other, visually looking like a
    single edge with both head and tail arrowheads.
    https://gitlab.com/graphviz/graphviz/-/issues/2241
    """

    # find our collocated test case
    input = Path(__file__).parent / "2241.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    svg = dot("svg", input)

    # load this as XML
    root = ET.fromstring(svg)

    # the output is expected to contain two paths which are well separated
    paths = root.findall(".//{http://www.w3.org/2000/svg}path")
    assert len(paths) == 2, "expected two paths in output"
    ellipses = root.findall(".//{http://www.w3.org/2000/svg}ellipse")
    assert len(ellipses) == 2, "expected two ellipses in output"

    # calculate the x coordinate of a vertical line which is equidistant from the two nodes
    x = statistics.mean(float(ellipse.get("cx")) for ellipse in ellipses)

    # for each edge path, get the y coordinate of a point on a line between the edge's endpoints
    # where the line intersects the node equidistant vertical line
    y_coordinates = []
    for path in paths:
        d_attribute = path.get("d")
        points_str = re.split("[ C]", d_attribute.replace("M", ""))
        assert (
            len(points_str) == 4
        ), "expected four points in the 'd' attribute of the 'path' element"
        points = [
            (float(x_str), float(y_str))
            for x_str, y_str in [point_str.split(",") for point_str in points_str]
        ]
        dx = points[3][0] - points[0][0]
        dy = points[3][1] - points[0][1]
        y = points[0][1] + dy / dx * (x - points[0][0])
        y_coordinates.append(y)

    # check that the lines are well separated vertically where they intersect the node equidistant
    # vertical line
    y_coordinates_abs_difference = abs(y_coordinates[1] - y_coordinates[0])
    y_coordinates_abs_difference_when_ok = 11.5004437538844
    y_coordinates_abs_difference_when_not_ok = 0.00658290568043185
    min_y_coordinates_abs_difference = (
        y_coordinates_abs_difference_when_ok + y_coordinates_abs_difference_when_not_ok
    ) / 2
    assert y_coordinates_abs_difference > min_y_coordinates_abs_difference


def test_2242():
    """
    repeated runs of a graph with subgraphs should yield a stable result
    https://gitlab.com/graphviz/graphviz/-/issues/2242
    """

    # get our baseline reference
    input = Path(__file__).parent / "2242.dot"
    assert input.exists(), "unexpectedly missing test case"
    ref = dot("png", input)

    # now repeat this, expecting it not to change
    for _ in range(20):
        png = dot("png", input)
        assert ref == png, "repeated rendering changed output"


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
@pytest.mark.skipif(
    platform.system() == "Windows"
    and which("dot") is not None
    and is_asan_instrumented(which("dot")),
    reason="ASan runs out of memory in its internal pool on Windows",
)
def test_2331(tmp_path: Path):
    """
    the example in this test should not cause a double-free
    https://gitlab.com/graphviz/graphviz/-/issues/2331
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2331.c").resolve()
    assert c_src.exists(), "missing test case"

    # From here, we essentially want to `run_c(c_src, …)`. However we cannot easily do
    # this because we want to directly link against plugins (instead of `dlopen` them),
    # libraries that are not in the linker’s search path. So instead we have to take a
    # more manual approach.

    # find the plugins we need to link against
    core = _find_plugin_so("core")
    assert core is not None, "core plugin library not found"
    dot_layout = _find_plugin_so("dot_layout")
    assert dot_layout is not None, "dot layout plugin library not found"

    # compile the test code
    exe = tmp_path / "a.exe"
    compile_c(c_src, link=["cgraph", "gvc", core, dot_layout], dst=exe)

    # teach the runtime linker how to find the plugins
    env = os.environ.copy()
    ld_library_path = f"{core.parent}:{dot_layout.parent}"
    prefix = ""
    if is_macos():
        if "DYLD_LIBRARY_PATH" in env:
            env["DYLD_LIBRARY_PATH"] = f"{ld_library_path}:{env['DYLD_LIBRARY_PATH']}"
        else:
            env["DYLD_LIBRARY_PATH"] = ld_library_path
        prefix = f"env DYLD_LIBRARY_PATH={env['DYLD_LIBRARY_PATH']} "
    else:
        if "LD_LIBRARY_PATH" in env:
            env["LD_LIBRARY_PATH"] = f"{ld_library_path}:{env['LD_LIBRARY_PATH']}"
        else:
            env["LD_LIBRARY_PATH"] = ld_library_path
        prefix = f"env LD_LIBRARY_PATH={env['LD_LIBRARY_PATH']} "

    # run the test code
    print(f"+ {prefix}{shlex.quote(str(exe))}")
    subprocess.run([exe], env=env, check=True)


def test_2342():
    """
    using an arrow with size 0 should not trigger an assertion failure
    https://gitlab.com/graphviz/graphviz/-/issues/2342
    """

    # find our collocated test case
    input = Path(__file__).parent / "2342.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_2356(tmp_path: Path):
    """
    Using `mindist` programmatically in a loop should not cause Windows crashes
    https://gitlab.com/graphviz/graphviz/-/issues/2356
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2356.c").resolve()
    assert c_src.exists(), "missing test case"

    # run the test
    run_c(c_src, tmp_path, link=["cgraph", "gvc"])


def test_2361():
    """
    using `ortho` and `concentrate` in combination should not cause a crash
    https://gitlab.com/graphviz/graphviz/-/issues/2361
    """

    # find our collocated test case
    input = Path(__file__).parent / "2361.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("png", input)


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2295"
)
def test_2295():
    """
    tooltips should work in PDFs
    https://gitlab.com/graphviz/graphviz/-/issues/2295
    """

    # find our collocated test case
    input = Path(__file__).parent / "2295.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate it to PDF
    pdf = dot("pdf", input)

    assert re.search(rb"\bhi mom\b", pdf) is not None, "tooltip not propagated to PDF"


@pytest.mark.parametrize("arg", ("--filepath", "-Gimagepath"))
def test_2396(arg: str):
    """
    `--filepath` should work as a replacement for `$GV_FILE_PATH`
    https://gitlab.com/graphviz/graphviz/-/issues/2396
    """

    # use an arbitrary image we have in the tree
    image = Path(__file__).parent / "../cmd/gvedit/images/save.png"
    assert image.exists(), "missing test data"

    # a graph that tries to use the image by relative path
    slash = "/" if arg == "--filepath" else ""
    source = f'graph {{ N[image="{slash}save.png"]; }}'

    # run this through Graphviz
    proc = subprocess.run(
        ["dot", "-Tsvg", f"{arg}={image.parent}"],
        capture_output=True,
        input=source,
        cwd=Path(__file__).parent,
        text=True,
        check=True,
    )

    # work around macOS warnings
    stderr = remove_xtype_warnings(proc.stderr).strip()

    # work around ASan informational printing
    stderr = remove_asan_summary(stderr)

    assert stderr == "", "loading an image by relative path produced warnings"

    # whether we used `imagepath` or `filepath` should affect whether we get a leading
    # slash
    if arg == "-Gimagepath":
        assert '"save.png"' in proc.stdout, "incorrect relative path in output"
    else:
        assert '"/save.png"' in proc.stdout, "incorrect relative path in output"


def test_2481():
    """
    `dot` should not exit with a syntax error if keywords are mixed-case
    https://gitlab.com/graphviz/graphviz/-/issues/2481
    """

    # try a simple graph with uppercase characters in 'digraph'
    input = "diGraph { }"

    # ensure this does not trigger warnings
    with subprocess.Popen(
        ["dot"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as p:
        _, stderr = p.communicate(input)
        assert p.returncode == 0, "mixed-case keyword was rejected"

    assert "syntax error" not in stderr, "dot displayed a syntax error message"


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_2484(tmp_path: Path):
    """
    Graphviz context should not preserve state across calls
    https://gitlab.com/graphviz/graphviz/-/issues/2484
    """

    # find our co-located driver
    c_src = (Path(__file__).parent / "2484.c").resolve()
    assert c_src.exists(), "missing test case"

    # find co-located input to the driver
    dot_src = (Path(__file__).parent / "2484.dot").resolve()
    assert dot_src.exists(), "missing test case"

    # compile and run it
    run_c(
        c_src,
        tmp_path,
        args=["-Kdot", "-Tpng", str(dot_src), "-o", os.devnull],
        link=["cgraph", "gvc"],
    )


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2592"
)
def test_2592():
    """
    pack modes should not remove xlabels
    https://gitlab.com/graphviz/graphviz/-/issues/2592
    """

    # find our collocated test case
    input = Path(__file__).parent / "2592.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    svg = dot("svg", input)

    assert "comment not included" in svg, "missing xlabel in packed graph"


def test_package_version(tmp_path: Path):
    """
    The graphviz_version.h header should define a non-empty PACKAGE_VERSION
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "get-package-version.c").resolve()
    assert c_src.exists(), "missing test case"

    # run the test
    package_version, _ = run_c(c_src, tmp_path)

    assert (
        package_version.strip() != ""
    ), "invalid PACKAGE_VERSION in graphviz_version.h"


def test_user_shapes():
    """
    Graphviz should understand how to embed a custom SVG image as a node’s shape
    """

    # find our collocated test case
    input = Path(__file__).parent / "usershape.dot"
    assert input.exists(), "unexpectedly missing test case"

    # ask Graphviz to translate this to SVG
    output = run("dot", "-Tsvg", input, cwd=os.path.dirname(__file__))

    # the external SVG should have been parsed and is now referenced
    assert '<image xlink:href="usershape.svg" width="62px" height="44px" ' in output


def test_xdot_json(tmp_path: Path):
    """
    check the output of xdot’s JSON API
    """

    # find our collocated C helper
    c_src = Path(__file__).parent / "xdot2json.c"

    # some valid xdot commands to process
    input = "c 9 -#fffffe00 C 7 -#ffffff P 4 0 0 0 36 54 36 54 0"

    # ask our C helper to process this
    output, err = run_c(c_src, tmp_path, input=input, link=["xdot"])
    assert err == ""

    # confirm the output was what we expected
    data = json.loads(output)
    assert data == [
        {"c": "#fffffe00"},
        {"C": "#ffffff"},
        {"P": [0.0, 0.0, 0.0, 36.0, 54.0, 36.0, 54.0, 0.0]},
    ]


@pytest.mark.skipif(which("gvmap") is None, reason="gvmap not available")
def test_gvmap_fclose():
    """
    gvmap should not attempt to fclose(NULL). This example will trigger a crash if
    this bug has been reintroduced and Graphviz is built with ASan support.
    """

    # a reasonable input graph
    input = (
        'graph "Alík: Na vlastní oči" {\n'
        '	graph [bb="0,0,128.9,36",\n'
        "		concentrate=true,\n"
        "		overlap=prism,\n"
        "		start=3\n"
        "	];\n"
        '	node [label="\\N"];\n'
        "	{\n"
        "		bob	[height=0.5,\n"
        '			pos="100.95,18",\n'
        "			width=0.77632];\n"
        "	}\n"
        "	{\n"
        "		alice	[height=0.5,\n"
        '			pos="32.497,18",\n'
        "			width=0.9027];\n"
        "	}\n"
        '	alice -- bob	[pos="65.119,18 67.736,18 70.366,18 72.946,18"];\n'
        "	bob -- alice;\n"
        "}"
    )

    # pass this through gvmap
    gvmap = which("gvmap")
    proc = subprocess.run([gvmap], input=input.encode("utf-8"), check=False)

    assert proc.returncode in (0, 1), "gvmap crashed"


@pytest.mark.skipif(which("gvmap") is None, reason="gvmap not available")
def test_gvmap_invalid():
    """gvmap should not crash when processing a file without node positions etc"""

    # an arbitrary test case that was observed to crash gvmap in the past
    src = Path(__file__).parent / "2368_1.dot"
    assert src.exists(), "missing test data"

    # run this through gvmap
    gvmap = which("gvmap")
    proc = subprocess.run([gvmap, src, "-o", os.devnull], check=False)

    assert proc.returncode in (0, 1), "gvmap crashed"


@pytest.mark.skipif(which("gvmap") is None, reason="gvmap not available")
@pytest.mark.xfail(
    is_rocky(),
    strict=True,
    raises=subprocess.CalledProcessError,
    reason="libgts unavailable on Rocky Linux",
)
def test_gvmap_add_coordinate():
    """gvmap should not read out of bounds when processing coordinates"""

    # an arbitrary test case that was observed to crash gvmap in the past
    src = Path(__file__).parent / "2239.dot"
    assert src.exists(), "missing test data"

    # run this through gvmap
    gvmap = which("gvmap")
    run(gvmap, src, "-o", os.devnull)


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_gvpr_usage(tmp_path: Path):
    """
    gvpr usage information should be included when erroring on a malformed command
    """

    # ask GVPR to process a non-existent file
    gvprbin = which("gvpr")
    with subprocess.Popen(
        [gvprbin, "-v", "-f", "nofile"],
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        text=True,
    ) as p:
        _, stderr = p.communicate()

        assert p.returncode != 0, "GVPR accepted a non-existent file"

    # the stderr output should have contained full usage instructions
    assert (
        "-o <ofile> - write output to <ofile>; stdout by default" in stderr
    ), "truncated or malformed GVPR usage information"


@pytest.mark.skipif(which("sfdp") is None, reason="sfdp not available")
def test_2225():
    """
    sfdp should not segfault with curved splines
    https://gitlab.com/graphviz/graphviz/-/issues/2225
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2225.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through sfdp
    sfdp = which("sfdp")
    p = subprocess.run(
        [sfdp, "-Gsplines=curved", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    # if sfdp was built without libgts, it will not handle anything non-trivial
    no_gts_error = "remove_overlap: Graphviz not built with triangulation library"
    if no_gts_error in p.stderr:
        assert p.returncode != 0, "sfdp returned success after an error message"
        return

    p.check_returncode()


def test_2257():
    """
    `$GV_FILE_PATH` being set should prevent Graphviz from running

    `$GV_FILE_PATH` was an environment variable formerly used to implement a file
    system sandboxing policy when Graphviz was exposed to the internet via a web
    server. These days, there are safer and more robust techniques to sandbox
    Graphviz and so `$GV_FILE_PATH` usage has been removed. But if someone
    attempts to use this legacy mechanism, we do not want Graphviz to
    “fail-open,” starting anyway and silently ignoring `$GV_FILE_PATH` giving
    the user the false impression the sandboxing is in force.

    https://gitlab.com/graphviz/graphviz/-/issues/2257
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2257.dot"
    assert input.exists(), "unexpectedly missing test case"

    env = os.environ.copy()
    env["GV_FILE_PATH"] = "/tmp"

    # Graphviz should refuse to process an input file
    with pytest.raises(subprocess.CalledProcessError):
        run_raw("dot", "-Tsvg", input, "-o", os.devnull, env=env)


def test_2258():
    """
    'id' attribute should be propagated to all graph children in output
    https://gitlab.com/graphviz/graphviz/-/issues/2258
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2258.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    # load this as XML
    root = ET.fromstring(svg)

    # the output is expected to contain a number of linear gradients, all of which
    # are semantic children of graph marked `id = "G2"`
    gradients = root.findall(".//{http://www.w3.org/2000/svg}linearGradient")
    assert len(gradients) > 0, "no gradients in output"

    for gradient in gradients:
        assert "G2" in gradient.get("id"), "ID was not applied to linear gradients"


def test_2270(tmp_path: Path):
    """
    `-O` should result in the expected output filename
    https://gitlab.com/graphviz/graphviz/-/issues/2270
    """

    # write a simple graph
    input = tmp_path / "hello.gv"
    input.write_text("digraph { hello -> world }", encoding="utf-8")

    # process it with Graphviz
    run_raw("dot", "-T", "plain:dot:core", "-O", "hello.gv", cwd=tmp_path)

    # it should have produced output in the expected location
    output = tmp_path / "hello.gv.core.dot.plain"
    assert output.exists(), "-O resulted in an unexpected output filename"


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_2272(tmp_path: Path):
    """
    using `agmemread` with an unterminated string should not fail assertions
    https://gitlab.com/graphviz/graphviz/-/issues/2272
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2272.c").resolve()
    assert c_src.exists(), "missing test case"

    # run the test
    run_c(c_src, tmp_path, link=["cgraph", "gvc"])


def test_2272_2():
    """
    An unterminated string in the source should not crash Graphviz. Variant of
    `test_2272`.
    """

    # a graph with an open string
    graph = 'graph { a[label="abc'

    # process it with Graphviz, which should not crash
    p = subprocess.run(["dot", "-o", os.devnull], input=graph, check=False, text=True)
    assert p.returncode != 0, "dot accepted invalid input"
    assert p.returncode == 1, "dot crashed"


def test_2278():
    """
    the shortcut for setting ubiquitous properties should work as expected
    https://gitlab.com/graphviz/graphviz/-/issues/2278
    """

    # a simple graph that will involve fonts
    graph = 'digraph { a->b[label="hello world"]; }'

    # process this, setting the default font
    svg = run(
        "dot",
        "-Tsvg",
        "-Efontname=Arial",
        "-Gfontname=Arial",
        "-Nfontname=Arial",
        input=graph,
    )

    # the output of this should differ from the default output
    default = dot("svg", source=graph)
    assert svg != default, "-E/-G/-N had no effect"

    # the shortcut for setting all of these should behave as expected
    svg_a = run("dot", "-Tsvg", "-Afontname=Arial", input=graph)
    assert svg == svg_a, "-A was not equivalent to -E+-G+-N"


def test_2282():
    """
    using the `fdp` layout with JSON output should result in valid JSON
    https://gitlab.com/graphviz/graphviz/-/issues/2282
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2282.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to JSON
    output = dot("json", input)

    # confirm this is valid JSON
    json.loads(output)


def test_2283():
    """
    `beautify=true` should correctly space nodes
    https://gitlab.com/graphviz/graphviz/-/issues/2283
    """

    # find our collocated test case
    input = Path(__file__).parent / "2283.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    p = subprocess.run(
        ["dot", "-Tsvg", input], capture_output=True, check=False, text=True
    )

    # if sfdp was built without libgts, it will not handle anything non-trivial
    no_gts_error = "remove_overlap: Graphviz not built with triangulation library"
    if no_gts_error in p.stderr:
        assert p.returncode != 0, "sfdp returned success after an error message"
        return
    p.check_returncode()

    svg = p.stdout

    # parse this into something we can inspect
    root = ET.fromstring(svg)

    # find node N0
    n0s = root.findall(
        ".//{http://www.w3.org/2000/svg}title[.='N0']/../{http://www.w3.org/2000/svg}ellipse"
    )
    assert len(n0s) == 1, "failed to locate node N0"
    n0 = n0s[0]

    # find node N1
    n1s = root.findall(
        ".//{http://www.w3.org/2000/svg}title[.='N1']/../{http://www.w3.org/2000/svg}ellipse"
    )
    assert len(n1s) == 1, "failed to locate node N1"
    n1 = n1s[0]

    # find node N6
    n6s = root.findall(
        ".//{http://www.w3.org/2000/svg}title[.='N6']/../{http://www.w3.org/2000/svg}ellipse"
    )
    assert len(n6s) == 1, "failed to locate node N6"
    n6 = n6s[0]

    # N1 and N6 should not have been drawn on top of each other
    n1_x = float(n1.attrib["cx"])
    n1_y = float(n1.attrib["cy"])
    n6_x = float(n6.attrib["cx"])
    n6_y = float(n6.attrib["cy"])

    def sameish(a: float, b: float) -> bool:
        EPSILON = 0.2
        return -EPSILON < abs(a - b) < EPSILON

    assert not (
        sameish(n1_x, n6_x) and sameish(n1_y, n6_y)
    ), "N1 and N6 placed identically"

    # use the Law of Cosines to compute the angle between N0→N1 and N0→N6
    n0_x = float(n0.attrib["cx"])
    n0_y = float(n0.attrib["cy"])
    n0_n1_dist = math.dist((n0_x, n0_y), (n1_x, n1_y))
    n0_n6_dist = math.dist((n0_x, n0_y), (n6_x, n6_y))
    n1_n6_dist = math.dist((n1_x, n1_y), (n6_x, n6_y))
    angle = math.acos(
        (n0_n1_dist**2 + n0_n6_dist**2 - n1_n6_dist**2) / (2 * n0_n1_dist * n0_n6_dist)
    )

    number_of_radial_nodes = 6
    assert sameish(
        angle, 2 * math.pi / number_of_radial_nodes
    ), "nodes not placed evenly"


def test_2285():
    """
    using the `svg_inline` output should result in SVG you can inline to HTML
    https://gitlab.com/graphviz/graphviz/-/issues/2285
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2285.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to JSON
    output = dot("svg_inline", input)

    assert "<?xml" not in output, "<?xml in output"
    assert "<!DOCTYPE" not in output, "<?xml in output"
    assert "xmlns" not in output, "xmlns in output"
    assert "<svg" in output, "<svg not in output"


@pytest.mark.skipif(which("gxl2gv") is None, reason="gxl2gv not available")
def test_2300_1():
    """
    translating GXL with an attribute `name` should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/2300
    """

    # locate our associated test case containing a node attribute `name`
    input = Path(__file__).parent / "2300.gxl"
    assert input.exists(), "unexpectedly missing test case"

    # ask `gxl2gv` to process this
    gxl2gv = which("gxl2gv")
    run_raw(gxl2gv, input)


def test_2307():
    """
    'id' attribute should be propagated to 'url' links in SVG output
    https://gitlab.com/graphviz/graphviz/-/issues/2307
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2258.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    # load this as XML
    root = ET.fromstring(svg)

    # the output is expected to contain a number of polygons, any of which have
    # `url` fills should include the ID “G2”
    polygons = root.findall(".//{http://www.w3.org/2000/svg}polygon")
    assert len(polygons) > 0, "no polygons in output"

    for polygon in polygons:
        m = re.match(r"url\((?P<url>.*)\)$", polygon.get("fill"))
        if m is None:
            continue
        assert (
            re.search(r"\bG2_", m.group("url")) is not None
        ), "ID G2 was not applied to polygon fill url"


def test_2325():
    """
    using more than 63 styles and/or more than 128 style bytes should not trigger
    an out-of-bounds memory read
    https://gitlab.com/graphviz/graphviz/-/issues/2325
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2325.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


@pytest.mark.skipif(shutil.which("groff") is None, reason="groff not available")
def test_2341():
    """
    PIC backend should generate correct comments
    https://gitlab.com/graphviz/graphviz/-/issues/2341
    """

    # a simple graph
    source = "digraph { a -> b; }"

    # generate PIC from this
    pic = dot("pic", source=source)

    # run this through groff
    groffed = run("groff", "-Tascii", "-p", input=pic)

    # it should not contain any comments
    assert (
        re.search(r"^\s*#", groffed) is None
    ), "Graphviz comment remains in groff output"


def test_2352():
    """
    referencing an all-one-line external SVG file should work
    https://gitlab.com/graphviz/graphviz/-/issues/2352
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2352.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate it to SVG
    svg = run("dot", "-Tsvg", input, cwd=Path(__file__).parent)

    assert '<image xlink:href="EDA.svg" ' in svg, "external file reference missing"


def test_2352_1():
    """
    variant of 2352 with a leading space in front of `<svg`
    https://gitlab.com/graphviz/graphviz/-/issues/2352
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2352_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate it to SVG
    svg = run("dot", "-Tsvg", input, cwd=Path(__file__).parent)

    assert '<image xlink:href="EDA_1.svg" ' in svg, "external file reference missing"


def test_2352_2():
    """
    variant of 2352 that spaces viewBox such that it is on a 200-character line
    boundary
    https://gitlab.com/graphviz/graphviz/-/issues/2352
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2352_2.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate it to SVG
    svg = run("dot", "-Tsvg", input, cwd=Path(__file__).parent)

    assert '<image xlink:href="EDA_2.svg" ' in svg, "external file reference missing"


def test_2355():
    """
    Using >127 layers should not crash Graphviz
    https://gitlab.com/graphviz/graphviz/-/issues/2355
    """

    # construct a graph with 128 layers
    graph = io.StringIO()
    graph.write("digraph {\n")
    layers = ":".join(f"l{i}" for i in range(128))
    graph.write(f'  layers="{layers}";\n')
    for i in range(128):
        graph.write(f'  n{i}[layer="l{i}"];\n')
    graph.write("}\n")

    # process this with dot
    dot("svg", source=graph.getvalue())


@pytest.mark.parametrize("testcase", ("2368.dot", "2368_1.dot"))
@pytest.mark.xfail(strict=True)  # FIXME
def test_2368(testcase: str):
    """
    routesplines should not corrupt its `prev` and `next` indices
    https://gitlab.com/graphviz/graphviz/-/issues/2368
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / testcase
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
def test_2370():
    """
    tcldot should have a version number TCL accepts
    https://gitlab.com/graphviz/graphviz/-/issues/2370
    """

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # ask TCL to import the Graphviz package
    response = run(
        "tclsh",
        stderr=subprocess.STDOUT,
        input="package require Tcldot;",
        env=env,
    )

    assert (
        "error reading package index file" not in response
    ), "tcldot cannot be loaded by TCL"


def test_2371():
    """
    Large graphs should not cause rectangle area calculation overflows
    https://gitlab.com/graphviz/graphviz/-/issues/2371
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2371.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    run_raw("dot", "-Tsvg", "-Knop2", "-o", os.devnull, input)


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="gvplugin_list symbol is not exposed on Windows",
)
def test_2375(tmp_path: Path):
    """
    `gvplugin_list` should return full plugin names
    https://gitlab.com/graphviz/graphviz/-/issues/2375
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2375.c").resolve()
    assert c_src.exists(), "missing test case"

    # run the test
    run_c(c_src, tmp_path, link=["gvc"])


def test_2377():
    """
    3 letter hex color codes should be accepted
    https://gitlab.com/graphviz/graphviz/-/issues/2377
    """

    # run some 6 letter color input through Graphviz
    input = 'digraph { n [color="#cc0000" fillcolor="#ffcc00" style=filled] }'
    svg1 = dot("svg", source=input)

    # try the equivalent with 3 letter colors
    input = 'digraph { n [color="#c00" fillcolor="#fc0" style=filled] }'
    svg2 = dot("svg", source=input)

    assert svg1 == svg2, "3 letter hex colors were not translated correctly"


def test_2390():
    """
    using an out of range `xdotversion` should not crash Graphviz
    https://gitlab.com/graphviz/graphviz/-/issues/2390
    """

    # some input with an invalid large `xdotversion`
    input = 'graph { xdotversion=99; n[label="hello world"]; }'

    # run this through Graphviz
    dot("xdot", source=input)


def test_2391():
    """
    `nslimit1=0` should not cause Graphviz to crash
    https://gitlab.com/graphviz/graphviz/-/issues/2391
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2391.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


def test_2391_1():
    """
    `nslimit1=0` with a label should not cause Graphviz to crash
    https://gitlab.com/graphviz/graphviz/-/issues/2391
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2391_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("svg", input)


def test_2397(tmp_path: Path):
    """
    escapes in strings should be handled correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2397
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2397.c").resolve()
    assert c_src.exists(), "missing test case"

    # run this to generate a graph
    link = ["cgraph", "gvc"]
    if is_static_build():
        # in static builds, we also need transitive dependencies
        link += ["cdt"]
    source, _ = run_c(c_src, tmp_path, link=link)

    # this should have produced a valid graph
    dot("svg", source=source)


def test_2397_1():
    """
    a variant of test_2397 that confirms the same works via the command line
    https://gitlab.com/graphviz/graphviz/-/issues/2397
    """

    source = 'digraph { a[label="foo\\\\\\"bar"]; }'

    # run this through dot
    output = dot("dot", source=source)

    # the output should be valid dot
    dot("svg", source=output)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck unavailable")
def test_2404():
    """
    shell syntax used by gvmap should be correct
    https://gitlab.com/graphviz/graphviz/-/issues/2404
    """
    gvmap_sh = Path(__file__).parent / "../cmd/gvmap/gvmap.sh"
    run_raw("shellcheck", "-S", "error", gvmap_sh)


def test_2406():
    """
    arrow types like `invdot` and `onormalonormal` should be displayed correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2406
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2406.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    output = dot("svg", input)

    # the rounded hollows should be present
    assert re.search(r"\bellipse\b", output), "missing element of invdot arrow"


@pytest.mark.parametrize("source", ("2413_1.dot", "2413_2.dot"))
def test_2413(source: str):
    """
    graphs that induce an edge length > 65535 should be supported
    https://gitlab.com/graphviz/graphviz/-/issues/2413
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / source
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    proc = subprocess.run(
        ["dot", "-Tsvg", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )

    # work around macOS warnings
    stderr = remove_xtype_warnings(proc.stderr).strip()

    # work around ASan informational printing
    stderr = remove_asan_summary(stderr)

    # no warnings should have been generated
    assert stderr == "", "long edges resulted in a warning"


def test_2429():
    """
    the vt target should be usable
    https://gitlab.com/graphviz/graphviz/-/issues/2429
    """

    # a basic graph
    source = "digraph { a -> b; }"

    # run it through Graphviz
    dot("vt", source=source)


@pytest.mark.skipif(which("nop") is None, reason="nop not available")
@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2436"
)
def test_2436():
    """
    nop should preserve empty labels
    https://gitlab.com/graphviz/graphviz/-/issues/2436
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2436.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through nop
    nop = which("nop")
    output = run(nop, input)

    # the empty label should be present
    assert re.search(r'\blabel\s*=\s*""', output), "empty label was not preserved"


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
@pytest.mark.xfail(
    raises=AssertionError,
    strict=True,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2434",
)
def test_2434(tmp_path: Path):
    """
    the order in which `agmemread` and `gvContext` calls are made should have no impact
    https://gitlab.com/graphviz/graphviz/-/issues/2434
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2434.c").resolve()
    assert c_src.exists(), "missing test case"

    # generate an SVG by calling `gvContext` first
    dst = tmp_path / "a.exe"
    compile_c(c_src, link=["cgraph", "gvc"], dst=dst)
    before = run(dst, "before")

    # generate an SVG by calling `gvContext` second
    after = run(dst, "after")

    # resulting images should be identical
    assert before == after, "agmemread/gvContext ordering affected image output"


def test_2437():
    """
    both an arrowhead and an arrowtail shall be created when using dir=both,
    compass ports, an edge default attribute and rank=same
    https://gitlab.com/graphviz/graphviz/-/issues/2437
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2437.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    # load this as XML
    root = ET.fromstring(svg)

    # The output is expected to contain tree polygons. The graph "background"
    # polygon, the arrowhead polygon and the arrowtail polygon.
    polygons = root.findall(".//{http://www.w3.org/2000/svg}polygon")

    assert len(polygons) == 3, "wrong number of polygons in output"


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2416"
)
def test_2416():
    """
    `splines=curved` should not affect arrow directions
    https://gitlab.com/graphviz/graphviz/-/issues/2416
    """

    # an input graph that provokes the problem
    input = "digraph G { splines=curved; b -> a; a -> b; }"

    # run it through Graphviz
    output = dot("json", source=input)
    data = json.loads(output)

    edges = data["edges"]
    assert len(edges) == 2, "unexpected number of output edges"

    # extract the height each edge’s arrow starts at
    y_1 = edges[0]["_hdraw_"][3]["points"][0][1]
    y_2 = edges[1]["_hdraw_"][3]["points"][0][1]

    # assuming the graph is vertical, these should not be too close
    assert abs(y_1 - y_2) > 1, "edge arrows appear to be drawn next to the same node"


def test_curved_concentrated_attributed_edges_do_not_crash():
    """Curved route concentration compares original edges, not virtual pieces."""

    for source in (
        b"""
            digraph {
              graph [concentrate=true splines=curved]
              a -> c [minlen=3 color=red]
              a -> c [minlen=3 color=red]
            }
        """,
        b"""
            digraph {
              graph [concentrate=true splines=curved]
              a -> b -> c -> d
              a -> d [minlen=3 color=red]
              a -> d [minlen=3 color=red]
            }
        """,
    ):
        proc = subprocess.run(
            [which("dot"), "-Tjson"],
            input=source,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_2454():
    """
    gvpr should support sscanf
    https://gitlab.com/graphviz/graphviz/-/issues/2454
    """

    # an input graph that provokes the problem
    input = "graph x{a -- {b c}}"

    # run it through Graphviz
    output = dot("dot", source=input)

    # run it through gvpr
    program = Path(__file__).parent / "2454.gvpr"
    gvprbin = which("gvpr")
    with subprocess.Popen(
        [gvprbin, "-cf", program], stdin=subprocess.PIPE, text=True
    ) as p:
        p.communicate(output)
        assert p.returncode == 0, "gvpr failed"


@pytest.mark.skipif(which("twopi") is None, reason="twopi not available")
@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2457"
)
def test_2457():
    """
    node definition order should not affect twopi’s layout
    https://gitlab.com/graphviz/graphviz/-/issues/2457
    """

    # locate our associated test cases in this directory
    case1 = Path(__file__).parent / "2457_1.dot"
    assert case1.exists(), "unexpectedly missing test case"
    case2 = Path(__file__).parent / "2457_2.dot"
    assert case2.exists(), "unexpectedly missing test case"

    # tweak the environment to force deterministic PDF generation
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = "0"

    # generate PDFs
    twopi = which("twopi")
    pdf1 = run_raw(twopi, "-Tpdf", case1, env=env)
    pdf2 = run_raw(twopi, "-Tpdf", case2, env=env)

    assert pdf1 == pdf2, "node definition order affected PDF generation"


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2458"
)
def test_2458():
    """
    `pack=true` should not result in edge labels disappearing
    https://gitlab.com/graphviz/graphviz/-/issues/2458
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2458.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    output = dot("svg", input)

    # the edge label should be present
    assert re.search(r"\bconnected\b", output), "missing edge label"


def test_2460():
    """
    labels involving back slashes should come out correctly in JSON
    https://gitlab.com/graphviz/graphviz/-/issues/2460
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2460.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    output = dot("json", input)
    data = json.loads(output)

    assert (
        data["objects"][0]["_ldraw_"][2]["text"]
        == r"double back slash in label \\. End should be the last word - End"
    ), "back slashes in labels handled incorrectly"


@pytest.mark.xfail(
    strict=platform.system() != "Windows",
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2470",
)
def test_2470():
    """
    another “trouble in init_rank variant”
    https://gitlab.com/graphviz/graphviz/-/issues/2470
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2470.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("ps", input)


def test_2814():
    """
    routing around edge labels should not emit triangulation errors or lose edges
    https://gitlab.com/graphviz/graphviz/-/issues/2814
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2814.dot"
    assert input.exists(), "unexpectedly missing test case"

    output = run("dot", "-Tdot", input, stderr=subprocess.STDOUT)

    assert "triangulation failed" not in output, "triangulation warnings were produced"
    assert "Pshortestpath failed" not in output, "pathplan error was produced"
    assert "Error: lost" not in output, "edges were lost"
    assert '"n.2" -> "n.19"' in output
    assert '"n.2" -> "n.20"' in output

    layout = json.loads(dot("json", input))
    labels = {}
    for edge in layout["edges"]:
        for stream in ("_hldraw_", "_tldraw_"):
            font_size = 14.0
            for operation in edge.get(stream, []):
                if operation["op"] == "F":
                    font_size = float(operation.get("size", font_size))
                if operation["op"] != "T" or operation["text"] not in {
                    "Edg2",
                    "Edg3",
                    "Edg4",
                    "Edg5",
                }:
                    continue
                x, y = operation["pt"]
                width = float(operation.get("width", 0.0))
                labels[operation["text"]] = (
                    x - width / 2,
                    y - 0.3 * font_size,
                    x + width / 2,
                    y + 0.9 * font_size,
                )
    assert set(labels) == {"Edg2", "Edg3", "Edg4", "Edg5"}
    for first, second in itertools.combinations(labels.values(), 2):
        assert _box_gap(first, second) >= 2.0


@pytest.mark.xfail(
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2471",
    strict=True,
)
def test_2471():
    """
    another “trouble in init_rank variant”
    https://gitlab.com/graphviz/graphviz/-/issues/2471
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2471.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("png", input)


@pytest.mark.xfail(
    is_rocky_8(),
    reason="Cairo is <v1.16 or malfunctions",
    strict=True,
)
def test_2473_1():
    """
    `SOURCE_DATE_EPOCH` should be usable to suppress timestamps
    https://gitlab.com/graphviz/graphviz/-/issues/2473
    """

    # a trivial graph
    graph = "graph { a -- b }".encode("utf-8")

    # set an epoch
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = "60"

    # generate a PDF
    first_run = run_raw("dot", "-Tpdf", input=graph, env=env)

    # wait long enough for the current time to change
    time.sleep(2)

    # generate another PDF
    second_run = run_raw("dot", "-Tpdf", input=graph, env=env)

    assert (
        first_run == second_run
    ), "PDF output is dependent on current time even when $SOURCE_DATE_EPOCH is set"


def test_2473_2():
    """
    When handling `SOURCE_DATE_EPOCH`, from
    https://reproducible-builds.org/specs/source-date-epoch/:

       If the value is malformed, the build process SHOULD exit with a non-zero
       error code.

    https://gitlab.com/graphviz/graphviz/-/issues/2473
    """

    # set up an invalid epoch
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = "foo"

    # confirm Graphviz rejects this
    with pytest.raises(subprocess.CalledProcessError):
        run(
            "dot",
            "-Tpdf",
            "-o",
            os.devnull,
            input="graph { a -- b }",
            env=env,
            encoding="utf-8",
        )


def test_2476():
    """
    tweaking `mclimit` should not lead to a “trouble in init_rank” failure
    https://gitlab.com/graphviz/graphviz/-/issues/2476
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2476.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    run_raw("dot", "-Tsvg", "-Gmclimit=0.5", "-o", os.devnull, input)


def test_2490():
    """
    the `crow` arrow shall be correctly placed and orientated when ports are used
    https://gitlab.com/graphviz/graphviz/-/issues/2490
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2490.dot"
    assert input.exists(), "unexpectedly missing test case"

    # translate this to SVG
    svg = dot("svg", input)

    # load this as XML
    root = ET.fromstring(svg)

    # The output is expected to contain three polygons, of which the two last
    # are the `crow` arrow shapes of the edge head and tail. Except for the
    # crow's "toes", the corners of these are expected to have the same x
    # position as the nodes' centers. The "toes" are expected to have x
    # positions half the width more or less than the nodes' centers.
    ellipses = root.findall(".//{http://www.w3.org/2000/svg}ellipse")
    assert len(ellipses) == 2, "wrong number of ellipses in output"
    cx = float(ellipses[0].get("cx"))
    assert float(ellipses[1].get("cx")) == cx

    polygons = root.findall(".//{http://www.w3.org/2000/svg}polygon")
    assert len(polygons) == 3, "wrong number of polygons in output"
    for polygon_index, polygon in enumerate(polygons):
        points_attr = polygon.get("points")
        point_pair_strs = points_attr.split(" ")
        points = [point_pair_str.split(",") for point_pair_str in point_pair_strs]
        if polygon_index == 0:
            assert len(points) == 5
            # ignore the graph polygon
            continue
        assert len(points) == 9
        for point_index, point in enumerate(points):
            x = float(point[0])
            crow_width = 9
            expected_crow_tip_and_shaft_x = cx
            expected_crow_toe_left_x = cx - crow_width / 2
            expected_crow_toe_right_x = cx + crow_width / 2
            expected_first_crow_toe_x = (
                expected_crow_toe_left_x
                if polygon_index == 1
                else expected_crow_toe_right_x
            )
            expected_second_crow_toe_x = (
                expected_crow_toe_right_x
                if polygon_index == 1
                else expected_crow_toe_left_x
            )
            if point_index in [0, 2, 3, 4, 5, 6, 8]:
                assert x == expected_crow_tip_and_shaft_x
            elif point_index == 1:
                assert x == expected_first_crow_toe_x
            elif point_index == 7:
                assert x == expected_second_crow_toe_x


@pytest.mark.skipif(which("gv2gml") is None, reason="gv2gml not available")
def test_2493():
    """
    `gv2gml` should support the yWorks.com variant of GML
    https://gitlab.com/graphviz/graphviz/-/issues/2493
    """

    # a trivial graph with a colored label
    src = 'graph { a -- b[label="foo", fontcolor="red"]; }'

    # pass this through `gv2gml`
    gv2gml = which("gv2gml")
    gml = run(gv2gml, "-y", input=src)

    assert (
        re.search(r"\bfontcolor\b", gml) is None
    ), "gv2gml emitted 'fontcolor' when in yWorks.com mode"
    assert (
        re.search(r"\bcolor\b", gml) is not None
    ), "gv2gml did not emit LabelGraphics 'color' attribute"


def test_2497():
    """
    graph rendering should be deterministic
    https://gitlab.com/graphviz/graphviz/-/issues/2497
    """

    # get our baseline reference
    input = Path(__file__).parent / "2497.dot"
    assert input.exists(), "unexpectedly missing test case"
    ref = dot("svg", input)

    # now repeat this, expecting it not to change
    for _ in range(20):
        out = dot("svg", input)
        assert ref == out, "repeated rendering changed output"


def test_2502():
    """
    unicode labels should be usable
    https://gitlab.com/graphviz/graphviz/-/issues/2502
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2502.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("dot", input)


@pytest.mark.xfail(
    strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2516"
)
def test_2516():
    """
    errors in HTML labels should result in a message with correct line number
    https://gitlab.com/graphviz/graphviz/-/issues/2516
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2516.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    proc = subprocess.run(
        ["dot", "-Tsvg", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert proc.returncode != 0, "malformed HTML label was accepted"

    assert (
        re.search(r"\bline 1\b", proc.stderr) is None
    ), "incorrect line number in error message"

    assert (
        re.search(r"\bline 2\b", proc.stderr) is not None
    ), "correct line number missing from error message"


@pytest.mark.parametrize(
    "testcase",
    (
        "705.dot",
        pytest.param(
            "2521.dot",
            marks=pytest.mark.xfail(
                strict=False,
                reason="https://gitlab.com/graphviz/graphviz/-/issues/2521",
            ),
        ),
        "2521_1.dot",
    ),
)
def test_2521(testcase: str):
    """
    `newrank=false` should reset to the default behavior
    https://gitlab.com/graphviz/graphviz/-/issues/2521
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / testcase
    assert input.exists(), "unexpectedly missing test case"

    def sh(args: list[Union[Path, str]]) -> bytes:
        """run a command, as if via the shell"""
        # dump the command being run for the user to observe if the test fails
        print(f"+ {shlex.join(str(x) for x in args)}")

        proc = subprocess.run(args, stdout=subprocess.PIPE, check=True)
        return proc.stdout

    # process this with and without `newrank=true`
    off = sh(["dot", "-Tpng", input])
    on = sh(["dot", "-Gnewrank=true", "-Tpng", input])

    assert off != on, "-Gnewrank=true had no effect"

    # we should be able to reset `newrank` with an explicit setting
    force_off = sh(["dot", "-Gnewrank=false", "-Tpng", input])
    assert force_off == off, "-Gnewrank=false did not reset the default"


@pytest.mark.xfail(
    is_macos(), strict=True, reason="https://gitlab.com/graphviz/graphviz/-/issues/2538"
)
def test_2538():
    """
    `chanSearch` assertion on `cp` should not fail
    https://gitlab.com/graphviz/graphviz/-/issues/2538
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2538.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("dot", input)


@pytest.mark.skipif(which("sfdp") is None, reason="sfdp not available")
def test_2556():
    """
    sfdp should not fail a GTS assertion
    https://gitlab.com/graphviz/graphviz/-/issues/2556
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2556.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through sfdp
    sfdp = which("sfdp")
    p = subprocess.run(
        [sfdp, "-Tpng", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    # if sfdp was built without libgts, it will not handle anything non-trivial
    no_gts_error = "remove_overlap: Graphviz not built with triangulation library"
    if no_gts_error in p.stderr:
        assert p.returncode != 0, "sfdp returned success after an error message"
        return

    p.check_returncode()


def test_2559():
    """
    `concentrate=true` should actually concentrate edges
    https://gitlab.com/graphviz/graphviz/-/issues/2559
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2559.dot"
    assert input.exists(), "unexpectedly missing test case"

    # convert this to JSON
    layout = dot("json", input)
    parsed = json.loads(layout)

    # the last edge, d→b, should be drawn as a curve rather than a straight edge
    assert not parsed["edges"][-1]["pos"].startswith(
        "e"
    ), "concentrated edge drawn as a regular straight edge"


def _json_layout(source: str) -> dict:
    """Render DOT source once and parse its JSON xdot payload."""

    return json.loads(dot("json", source=source))


def _drawn_edges_from_layout(layout: dict) -> list[dict]:
    """Return edges with an xdot ``_draw_`` stream and thus visible geometry."""

    return [edge for edge in layout["edges"] if "_draw_" in edge]


def _drawn_edges(source: str) -> list[dict]:
    """Render DOT source and return visible drawn edges."""

    return _drawn_edges_from_layout(_json_layout(source))


def _drawn_edge_piece_end_gaps(edge: dict) -> list[float]:
    """Measure endpoint gaps between consecutive drawn spline pieces."""

    pieces = [
        operation["points"]
        for operation in edge.get("_draw_", [])
        if operation["op"] in {"B", "b", "L"} and len(operation["points"]) >= 2
    ]
    return [math.dist(left[-1], right[0]) for left, right in zip(pieces, pieces[1:])]


def _assert_regular_g1_piece_join(
    left_points: list[list[float]], right_points: list[list[float]]
) -> None:
    """Assert a rendered cubic seam is C0, regular, and forward G1."""

    assert len(left_points) >= 4 and len(left_points) % 3 == 1
    assert len(right_points) >= 4 and len(right_points) % 3 == 1
    assert math.dist(left_points[-1], right_points[0]) <= 0.01
    incoming = (
        left_points[-1][0] - left_points[-2][0],
        left_points[-1][1] - left_points[-2][1],
    )
    outgoing = (
        right_points[1][0] - right_points[0][0],
        right_points[1][1] - right_points[0][1],
    )
    incoming_length = math.hypot(*incoming)
    outgoing_length = math.hypot(*outgoing)
    assert incoming_length > 1e-6
    assert outgoing_length > 1e-6
    dot_product = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    assert dot_product > 0
    residual = abs(incoming[0] * outgoing[1] - incoming[1] * outgoing[0])
    # JSON control points are rounded to 0.01 pt, so allow the corresponding
    # sub-degree cross-product drift while still rejecting a visible kink.
    assert residual <= 2e-3 * incoming_length * outgoing_length


def _named_drawn_cubic_geometry(
    source: str, tails: set[str], head: str
) -> list[tuple[str, str, tuple]]:
    """Canonicalize rendered cubics by endpoint name, independent of edge order."""

    layout = json.loads(dot("json", source=source))
    names = {node["_gvid"]: node["name"] for node in layout["objects"]}
    geometry = []
    for edge in layout["edges"]:
        tail_name = names[edge["tail"]]
        head_name = names[edge["head"]]
        if tail_name not in tails or head_name != head or "_draw_" not in edge:
            continue
        cubics = tuple(
            tuple(tuple(point) for point in operation["points"])
            for operation in edge["_draw_"]
            if operation["op"] == "b"
        )
        geometry.append((tail_name, head_name, cubics))
    return sorted(geometry)


def _concentrated_graph(splines: str, *body: str) -> str:
    """Wrap readable DOT statements in a concentrated directed graph."""

    body_source = "\n".join(body)
    return f"""
        digraph {{
          graph [concentrate=true {splines}]
          {body_source}
        }}
    """


def _graph_with_concentrate(concentrate: bool, *body: str) -> str:
    """Wrap readable DOT statements in a directed graph with concentrate set."""

    body_source = "\n".join(body)
    return f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {body_source}
        }}
    """


def _set_graph_concentrate(source: str, concentrate: bool) -> str:
    """Force the first graph-level concentrate assignment in a source fixture."""

    return re.sub(
        r"\bconcentrate\s*=\s*(?:true|false)\b",
        f"concentrate={str(concentrate).lower()}",
        source,
        count=1,
    )


def _assert_concentrate_route_gain(
    source: str, plain_count: int, concentrate_count: int
) -> None:
    """Assert exact drawn-route counts and that concentration is not a no-op."""

    plain_routes = len(_drawn_edges(_set_graph_concentrate(source, False)))
    concentrate_routes = len(_drawn_edges(_set_graph_concentrate(source, True)))
    assert plain_routes == plain_count
    assert concentrate_routes == concentrate_count
    assert concentrate_routes < plain_routes


@pytest.mark.parametrize(
    ("case", "source", "plain_count", "concentrate_count"),
    (
        pytest.param(
            "plain-duplicates",
            _graph_with_concentrate(True, "a -> b", "a -> b", "a -> b"),
            3,
            1,
            id="plain-duplicates",
        ),
        pytest.param(
            "same-rendered-attrs",
            _graph_with_concentrate(
                True,
                "a -> b [color=blue style=dashed penwidth=2]",
                "a -> b [color=blue style=dashed penwidth=2]",
                "a -> b [color=blue style=dashed penwidth=2]",
            ),
            3,
            1,
            id="same-rendered-attrs",
        ),
        pytest.param(
            "reverse-same-physical-endpoint",
            _graph_with_concentrate(
                True,
                "a -> b [samehead=x]",
                "b -> a [sametail=x]",
            ),
            2,
            1,
            id="reverse-same-physical-endpoint",
        ),
    ),
)
def test_concentrate_positive_route_gain(
    case: str, source: str, plain_count: int, concentrate_count: int
):
    """Equivalent edge groups still reduce visible drawn routes."""

    assert case
    _assert_concentrate_route_gain(source, plain_count, concentrate_count)


def test_concentrate_issue_2764_bogus_record_ports_still_merge():
    """Unresolved record ports fall back to center and remain concentratable."""

    source = Path(__file__).with_name("2764.dot").read_text(encoding="utf-8")
    _assert_concentrate_route_gain(source, 5, 4)


def _drawn_route_topology(source: str) -> tuple[int, tuple[str, ...]]:
    """Return a compact topology signature for concentrate metamorphic checks."""

    colors = tuple(sorted(_drawn_edge_color(edge) for edge in _drawn_edges(source)))
    return len(colors), colors


def test_concentrate_metamorphic_edge_permutation_invariance():
    """Equivalent duplicate placement does not change the chosen topology."""

    bodies = [
        ("a -> b [color=red]", "a -> b [color=blue]", "a -> b [color=red]"),
        ("a -> b [color=red]", "a -> b [color=red]", "a -> b [color=blue]"),
        ("a -> b [color=blue]", "a -> b [color=red]", "a -> b [color=red]"),
    ]
    signatures = {
        _drawn_route_topology(_concentrated_graph("", *body)) for body in bodies
    }
    assert signatures == {(2, ("#0000ff", "#ff0000"))}


def test_concentrate_metamorphic_duplicate_pressure_monotonicity():
    """Adding exact duplicates never adds visible concentrate lanes."""

    route_counts = [
        len(_drawn_edges(_concentrated_graph("", *("a -> b" for _ in range(count)))))
        for count in range(1, 7)
    ]
    assert route_counts == [1, 1, 1, 1, 1, 1]


def test_concentrate_metamorphic_exact_duplicates_add_no_visual_lanes():
    """Exact duplicates render as one visual lane, not N parallel lanes."""

    source = _graph_with_concentrate(
        True,
        "a -> b [color=red penwidth=2]",
        "a -> b [color=red penwidth=2]",
        "a -> b [color=red penwidth=2]",
    )
    assert _drawn_route_topology(source) == (1, ("#ff0000",))


def test_concentrate_metamorphic_rankdir_mirror_symmetry():
    """Mirroring rank direction preserves the concentrate topology."""

    left_to_right = _concentrated_graph(
        "rankdir=LR",
        "a -> b [color=red]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    right_to_left = _concentrated_graph(
        "rankdir=RL",
        "a -> b [color=red]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    assert _drawn_route_topology(left_to_right) == _drawn_route_topology(right_to_left)


def test_concentrate_metamorphic_invisible_degree2_node_insertion_stability():
    """An unrelated invisible degree-2 node does not perturb duplicate routing."""

    base = _concentrated_graph("", "a -> b", "a -> b")
    inserted = _concentrated_graph(
        "",
        "a -> b",
        "a -> x [style=invis]",
        "x -> b [style=invis]",
        "a -> b",
    )
    assert len(_drawn_edges(base)) == 1
    assert len(_drawn_edges(inserted)) == 1


def test_concentrate_metamorphic_no_worse_than_baseline_primary_metric():
    """The primary route-count metric is no worse with concentrate enabled."""

    source = _graph_with_concentrate(
        True,
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
        "b -> c",
        "b -> c",
    )
    plain_routes = len(_drawn_edges(_set_graph_concentrate(source, False)))
    concentrated_routes = len(_drawn_edges(_set_graph_concentrate(source, True)))
    assert concentrated_routes <= plain_routes


def test_concentrate_metamorphic_layout_determinism():
    """The same concentrated input produces stable JSON layout topology."""

    source = _concentrated_graph(
        "",
        "a -> b [color=red]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "b -> c",
    )
    layouts = [json.loads(dot("json", source=source)) for _ in range(3)]
    assert layouts[0]["edges"] == layouts[1]["edges"] == layouts[2]["edges"]


def test_concentrate_oracle_trace_developer_mode(tmp_path: Path):
    """GV_CONCENTRATE_ORACLE emits a fenced plan diff only when requested."""

    source = tmp_path / "oracle.dot"
    source.write_text(
        """
        digraph {
          graph [concentrate=true]
          a -> b [color=red]
          a -> b [color=red]
          a -> b [color=blue]
        }
        """,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["GV_CONCENTRATE_ORACLE"] = "1"
    env["GV_CONCENTRATE_ORACLE_MAX_EDGES"] = "8"
    completed = subprocess.run(
        ["dot", "-Txdot", source],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert "concentrate-oracle: begin" in completed.stderr
    assert "heuristic=" in completed.stderr
    assert "best=" in completed.stderr
    assert "variants=no-merge,join-at-rank,sub-bundles,full-trunk" in completed.stderr


def _drawn_edge_color(edge: dict) -> str:
    """Read the pen color from an edge's xdot ``c`` operation."""

    operation = next(
        operation for operation in edge["_draw_"] if operation["op"] == "c"
    )
    return operation["color"]


def _drawn_edge_colors(source: str) -> list[str]:
    """Read each drawn edge's pen color."""

    return [_drawn_edge_color(edge) for edge in _drawn_edges(source)]


def _drawn_edges_by_color(source: str) -> dict[str, dict]:
    """Group drawn edges by their xdot pen color."""

    return {_drawn_edge_color(edge): edge for edge in _drawn_edges(source)}


def _drawn_edge_styles(source: str) -> list[str]:
    """Read line styles from xdot's ``S`` operations."""

    return [
        operation["style"]
        for edge in _drawn_edges(source)
        for operation in edge["_draw_"]
        if operation["op"] == "S"
    ]


def _drawn_label_texts(edge: dict) -> list[str]:
    """Read text emitted in an edge's xdot label stream."""

    return [
        operation["text"]
        for operation in edge.get("_ldraw_", [])
        if operation["op"] == "T"
    ]


def _edge_label_draw_streams(edge: dict, include_endpoint: bool = False) -> list[list[dict]]:
    """Read main and endpoint edge label xdot streams."""

    streams = ("_ldraw_", "_hldraw_", "_tldraw_") if include_endpoint else ("_ldraw_",)
    return [
        edge.get(stream, [])
        for stream in streams
        if stream in edge
    ]


def _edge_label_texts(source: str) -> list[str]:
    """Read text emitted by all edge label draw streams."""

    return [
        text
        for edge in json.loads(dot("json", source=source))["edges"]
        for text in _drawn_label_texts(edge)
    ]


def _edge_label_boxes_from_layout(
    layout: dict, include_endpoint: bool = False
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Read approximate edge-label boxes from a parsed JSON xdot layout."""

    boxes = []
    for edge in layout["edges"]:
        for stream in _edge_label_draw_streams(edge, include_endpoint):
            font_size = 14.0
            for operation in stream:
                if operation["op"] == "F":
                    font_size = float(operation.get("size", font_size))
                if operation["op"] != "T":
                    continue
                x, y = operation["pt"]
                width = float(operation.get("width", 0.0))
                align = operation.get("align", "c")
                if align == "l":
                    left = x
                elif align == "r":
                    left = x - width
                else:
                    left = x - width / 2
                boxes.append(
                    (
                        operation["text"],
                        (
                            left,
                            y - 0.3 * font_size,
                            left + width,
                            y + 0.9 * font_size,
                        ),
                    )
                )
    return boxes


def _edge_label_boxes(
    source: str, include_endpoint: bool = False
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Render DOT source and read approximate edge-label boxes."""

    return _edge_label_boxes_from_layout(_json_layout(source), include_endpoint)


def _box_gap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Measure the shortest distance between two axis-aligned boxes."""

    dx = max(first[0] - second[2], second[0] - first[2], 0.0)
    dy = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(dx, dy)


def _boxes_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Return whether two axis-aligned boxes overlap or touch."""

    return max(first[0], second[0]) <= min(first[2], second[2]) and max(
        first[1], second[1]
    ) <= min(first[3], second[3])


def _node_boxes(layout: dict) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Read node boxes from dot JSON coordinates."""

    boxes = []
    for node in layout["objects"]:
        x, y = (float(value) for value in node["pos"].split(","))
        width = float(node["width"]) * 72.0
        height = float(node["height"]) * 72.0
        boxes.append(
            (
                node["name"],
                (x - width / 2, y - height / 2, x + width / 2, y + height / 2),
            )
        )
    return boxes


@pytest.mark.parametrize("fixture", ("sb_box_dbl.gv", "sb_circle_dbl.gv"))
def test_concentrated_duplicate_self_edge_labels_do_not_overlap(fixture: str):
    """
    Concentrating identical labeled self-edges should not overprint duplicate
    label instances.
    """

    source = (Path(__file__).parent / "graphs" / fixture).read_text().replace(
        "{", "{\n  graph [concentrate=true];", 1
    )
    boxes = _edge_label_boxes(source)
    assert len(boxes) == 2
    for (_, first), (_, second) in itertools.combinations(boxes, 2):
        assert _box_gap(first, second) >= 4.0


@pytest.mark.parametrize("fixture", ("sb_box_dbl.gv", "sb_circle_dbl.gv"))
def test_concentrated_duplicate_self_edge_labels_sit_clear_of_drawing(fixture: str):
    """
    Deduped self-edge labels should sit beside their loop, not on nodes or
    routed strokes.
    """

    source = (Path(__file__).parent / "graphs" / fixture).read_text().replace(
        "{", "{\n  graph [concentrate=true];", 1
    )
    layout = _json_layout(source)
    boxes = _edge_label_boxes_from_layout(layout)
    assert len(boxes) == 2

    for text, box in boxes:
        for node_name, node_box in _node_boxes(layout):
            assert not _boxes_overlap(box, node_box), (text, node_name)
        for edge in layout["edges"]:
            route = _drawn_edge_polyline(edge)
            for start, end in zip(route, route[1:]):
                assert _point_segment_distance(
                    ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), start, end
                ) > min(box[2] - box[0], box[3] - box[1]) / 2


def test_concentrated_duplicate_self_edge_routes_merge_with_one_label():
    """Exact duplicate labeled self-loops render as one loop and one label."""

    source = _concentrated_graph(
        "",
        'node [shape=circle]',
        'a -> a [label="tailport=n headport=n" tailport=n headport=n]',
        'a -> a [label="tailport=n headport=n" tailport=n headport=n]',
    )
    layout = _json_layout(source)
    drawn_self_edges = [
        edge
        for edge in layout["edges"]
        if edge["tail"] == edge["head"] and "_draw_" in edge
    ]
    labeled_edges = [
        edge
        for edge in drawn_self_edges
        if any(stream.get("op") == "T" for stream in edge.get("_ldraw_", []))
    ]

    assert len(drawn_self_edges) == 1
    assert len(labeled_edges) == 1


def test_concentrated_duplicate_self_edge_label_is_not_replaced_as_xlabel():
    """A deduped duplicate label must not be placed again by addXLabels()."""

    label = "tailport=n headport=n"
    source = _concentrated_graph(
        "",
        "node [shape=circle]",
        f'a -> a [label="{label}" tailport=n headport=n]',
        f'a -> a [label="{label}" tailport=n headport=n]',
    )
    layout = _json_layout(source)
    label_draws = [
        operation["text"]
        for edge in layout["edges"]
        for operation in edge.get("_ldraw_", [])
        if operation["op"] == "T"
    ]

    assert label_draws == [label]


def test_concentrated_left_self_edge_label_releases_duplicate_route_width():
    """Left self-loop dedupe should not reserve the deleted duplicate lane."""

    source = (Path(__file__).parent / "graphs" / "sl_box_dbl.gv").read_text()
    concentrated = source.replace("{", "{\n  graph [concentrate=true];", 1)
    unconcentrated = source.replace("{", "{\n  graph [concentrate=false];", 1)

    layout = _json_layout(concentrated)
    drawn_self_edges = [
        edge
        for edge in layout["edges"]
        if edge["tail"] == edge["head"] and "_draw_" in edge
    ]
    label_draws = [
        operation["text"]
        for edge in layout["edges"]
        for operation in edge.get("_ldraw_", [])
        if operation["op"] == "T"
    ]

    assert len(drawn_self_edges) == 21
    assert len(label_draws) == 21
    assert _graph_width(layout) < _graph_width(_json_layout(unconcentrated)) * 0.7


def test_concentrated_left_self_edge_label_tracks_loop_height():
    """Deduped left self-loop labels should center on the retained loop."""

    source = (Path(__file__).parent / "graphs" / "sl_box_dbl.gv").read_text()
    concentrated = source.replace("{", "{\n  graph [concentrate=true];", 1)

    layout = _json_layout(concentrated)
    objects = layout["objects"]
    labels_by_tail = {}
    for edge in layout["edges"]:
        tail_name = objects[edge["tail"]]["name"]
        head_name = objects[edge["head"]]["name"]
        if tail_name != head_name or "_draw_" not in edge or "lp" not in edge:
            continue
        route = _drawn_edge_polyline(edge)
        route_center_y = (
            min(point[1] for point in route) + max(point[1] for point in route)
        ) / 2
        label_y = float(edge["lp"].split(",")[1])
        labels_by_tail[tail_name] = (label_y, route_center_y)

    for node_name in ("node11", "node12"):
        label_y, route_center_y = labels_by_tail[node_name]
        assert label_y == pytest.approx(route_center_y, abs=1.0)


def test_2814_grouped_endpoint_labels_sit_clear_of_nodes():
    """Grouped flat endpoint labels should not be separated into node boxes."""

    source = (Path(__file__).parent / "2814.dot").read_text()
    layout = _json_layout(source)
    boxes = [
        (text, box)
        for text, box in _edge_label_boxes_from_layout(layout, include_endpoint=True)
        if text in {"Edg2", "Edg3", "Edg4", "Edg5"}
    ]
    assert {text for text, _ in boxes} == {"Edg2", "Edg3", "Edg4", "Edg5"}

    for text, box in boxes:
        for node_name, node_box in _node_boxes(layout):
            assert not _boxes_overlap(box, node_box), (text, node_name)
    for (_, first), (_, second) in itertools.combinations(boxes, 2):
        assert _box_gap(first, second) >= 2.0


def _arrow_polygon_point_count(edge: dict, endpoint: str) -> int:
    """Distinguish arrow shapes by the size of their xdot ``P`` polygon."""

    endpoint_draw_operations = edge[f"_{endpoint}draw_"]
    polygon = next(
        operation for operation in endpoint_draw_operations if operation["op"] == "P"
    )
    return len(polygon["points"])


def _arrow_fill_color(edge: dict, endpoint: str) -> str:
    """Read an arrow's fill color from its xdot ``C`` operation."""

    operation = next(
        operation
        for operation in edge[f"_{endpoint}draw_"]
        if operation["op"] == "C"
    )
    return operation["color"]


def _drawn_edge_spline_point_count(edge: dict) -> int:
    """Count points across every xdot Bezier segment drawn for one edge."""

    return sum(
        len(operation["points"])
        for operation in edge["_draw_"]
        if operation["op"] == "b"
    )


def _edge_position_tokens(
    edge: dict,
) -> tuple[list[tuple[str, tuple[float, float]]], list[tuple[float, float]]]:
    """Split an edge's ``pos`` into endpoint markers and spline points."""

    markers = []
    points = []
    for token in edge["pos"].split():
        marker = ""
        coordinates = token
        if token.startswith(("e,", "s,")):
            marker = token[0]
            coordinates = token[2:]
        x, y = coordinates.split(",", 1)
        point = (float(x), float(y))
        if marker:
            markers.append((marker, point))
        else:
            points.append(point)
    return markers, points


def _edge_physical_endpoint(edge: dict, endpoint: str) -> tuple[float, float]:
    """Return the visible head or tail endpoint from Graphviz JSON output."""

    markers, points = _edge_position_tokens(edge)
    marker = "e" if endpoint == "head" else "s"
    marked_points = [point for kind, point in markers if kind == marker]
    if marked_points:
        assert len(marked_points) == 1
        return marked_points[0]
    assert points
    return points[-1] if endpoint == "head" else points[0]


def _group_anchor_points(
    source: str,
    *,
    node_name: str,
    endpoint: str,
    attribute: str,
    groups: set[str],
) -> set[tuple[float, float]]:
    """Collect rounded visible anchors for selected samehead/sametail groups."""

    layout = json.loads(dot("json", source=source))
    node_ids = {node["name"]: node["_gvid"] for node in layout["objects"]}
    node_id = node_ids[node_name]
    endpoint_id = "head" if endpoint == "head" else "tail"
    return {
        tuple(
            round(coordinate, 3)
            for coordinate in _edge_physical_endpoint(edge, endpoint)
        )
        for edge in layout["edges"]
        if "_draw_" in edge
        and edge[endpoint_id] == node_id
        and edge.get(attribute, "") in groups
    }


def _samehead_mixed_route_fixture(concentrate: bool) -> str:
    """Exercise regular, backward-classified, and flat samehead members."""

    return f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          rankdir=TB

          s0 -> A [samehead=x]
          s1 -> A [samehead=x]
          A -> back_anchor [style=invis, weight=100]
          back_anchor -> A [samehead=x]
          {{ rank=same; flat; A; }}
          flat -> A [samehead=x]

          b0 -> B [samehead=y]
          b1 -> B [samehead=z]
        }}
    """


def _sametail_mixed_route_fixture(concentrate: bool) -> str:
    """Exercise regular, backward-classified, and flat sametail members."""

    return f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          rankdir=TB

          A -> t0 [sametail=x]
          A -> t1 [sametail=x]
          back -> A [style=invis, weight=100]
          A -> back [sametail=x]
          {{ rank=same; A; flat; }}
          A -> flat [sametail=x]

          A -> u0 [sametail=y]
          A -> u1 [sametail=z]
        }}
    """


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_samehead_anchor_spans_regular_backward_and_flat_routes(
    concentrate: bool,
):
    """Every samehead member meets its node at one physical point (GitLab #448)."""

    anchors = _group_anchor_points(
        _samehead_mixed_route_fixture(concentrate),
        node_name="A",
        endpoint="head",
        attribute="samehead",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_sametail_anchor_spans_regular_backward_and_flat_routes(
    concentrate: bool,
):
    """Every sametail member leaves its node at one physical point (GitLab #448)."""

    anchors = _group_anchor_points(
        _sametail_mixed_route_fixture(concentrate),
        node_name="A",
        endpoint="tail",
        attribute="sametail",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_samehead_flat_edges_share_the_resolved_anchor(
    concentrate: bool,
):
    """Flat adjacent samehead edges keep the group port selected by dot."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; a; b; z; }}
          a -> z [samehead=x]
          b -> z [samehead=x]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="head",
        attribute="samehead",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_sametail_flat_edges_share_the_resolved_anchor(
    concentrate: bool,
):
    """Flat adjacent sametail edges keep the group port selected by dot."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; z; a; b; }}
          z -> a [sametail=x]
          z -> b [sametail=x]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="tail",
        attribute="sametail",
        groups={"x"},
    )
    assert len(anchors) == 1, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_distinct_samehead_groups_keep_distinct_anchors(
    concentrate: bool,
):
    """Different samehead IDs do not acquire a shared physical port."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          b0 -> B [samehead=y]
          b1 -> B [samehead=z]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="B",
        endpoint="head",
        attribute="samehead",
        groups={"y", "z"},
    )
    assert len(anchors) == 2, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_distinct_sametail_groups_keep_distinct_anchors(
    concentrate: bool,
):
    """Different sametail IDs do not acquire a shared physical port."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          A -> a0 [sametail=y]
          A -> a1 [sametail=z]
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="A",
        endpoint="tail",
        attribute="sametail",
        groups={"y", "z"},
    )
    assert len(anchors) == 2, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_ungrouped_flat_edges_keep_independent_anchors(
    concentrate: bool,
):
    """The flat-edge fix does not create samehead behavior without samehead."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; a; b; z; }}
          a -> z
          b -> z
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="head",
        attribute="samehead",
        groups={""},
    )
    assert len(anchors) == 2, anchors


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_ungrouped_flat_tails_keep_independent_anchors(
    concentrate: bool,
):
    """The flat-edge fix does not create sametail behavior without sametail."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; z; a; b; }}
          z -> a
          z -> b
        }}
    """
    anchors = _group_anchor_points(
        source,
        node_name="z",
        endpoint="tail",
        attribute="sametail",
        groups={""},
    )
    assert len(anchors) == 2, anchors


def test_concentrate_flat_port_pair_crosses_steeply():
    """Opposite same-rank compass ports cross at a readable angle."""

    source = """
        digraph {
          graph [concentrate=true]
          { rank=same; a; b }
          a:n -> b:s [color=red]
          a:s -> b:n [color=red]
        }
    """
    edges = _drawn_edges(source)
    assert len(edges) == 2

    assert _crossing_angle(edges[0], edges[1]) >= 45


def test_concentrate_p3_crossings_do_not_exceed_base():
    """Concentration keeps p3 at the true-base sampled crossing count."""

    source = (Path(__file__).parent / "graphs" / "p3.gv").read_text().replace(
        "graph G {", "graph G {\n  graph [concentrate=true];", 1
    )
    edges = _drawn_edges(source)

    assert _sampled_edge_crossing_count(edges) <= 1


def _assert_concentrate_keeps_zero_crossings(source: str) -> None:
    plain_edges = _drawn_edges(_set_graph_concentrate(source, False))
    concentrated_edges = _drawn_edges(_set_graph_concentrate(source, True))

    assert _sampled_edge_crossing_count(plain_edges) == 0
    assert _sampled_edge_crossing_count(concentrated_edges) == 0


@pytest.mark.parametrize(
    ("case", "source"),
    (
        pytest.param(
            "corpus-0005-tree",
            _graph_with_concentrate(
                True,
                "root -> {lead_a lead_b}",
                "lead_a -> {team_a team_b team_c}",
                "lead_b -> {team_d team_e}",
                "team_d -> {leaf_a leaf_b leaf_c}",
            ),
            id="corpus-0005-tree",
        ),
        pytest.param(
            "corpus-0007-fanout",
            _graph_with_concentrate(
                True,
                "root -> {finance product engineering operations}",
                "product -> {analyst designer}",
                "engineering -> {frontend backend qa}",
                "operations -> support",
            ),
            id="corpus-0007-fanout",
        ),
        pytest.param(
            "corpus-0017-pipeline",
            _graph_with_concentrate(
                True,
                "rankdir=LR",
                "provider -> raw",
                "raw -> normalize -> normalized",
                "normalized -> recombine -> event -> rollup",
                "normalized -> event",
                "normalized -> episode",
                "feed -> snapshot -> version -> episode",
            ),
            id="corpus-0017-pipeline",
        ),
    ),
)
def test_concentrate_preserves_zero_crossings(case: str, source: str):
    """Before-zero corpus fixtures stay crossing-free after concentration."""

    assert case
    _assert_concentrate_keeps_zero_crossings(source)


def test_concentrate_train11_minimized_route_has_no_line_gap():
    """A reversed concentrated route keeps a continuous shaft to its endpoint."""

    source = r"""
        digraph G {
          graph [concentrate=true, rankdir=LR, size="6,6"]
          node [fontsize=8, shape=circle]
          st0 -> st0 [label="00/0"]
          st1 [fontsize=""]
          st0 -> st1 [label="10/-"]
          st5 [fontsize=""]
          st1 -> st5
          st3 [fontsize=""]
          st1 -> st3
          st5 -> st6 [label="01/1"]
          st4 [fontsize=""]
          st3 -> st4
          st6 -> st0 [label="00/-"]
          st6 -> st6 [label="01/1"]
          st4 -> st0 [label="00/-"]
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "st6", "st0")

    assert "_hdraw_" in edge
    assert all(gap <= 20 for gap in _drawn_edge_piece_end_gaps(edge))


def test_concentrate_drbd_minimized_routes_do_not_touch_tangentially():
    """Distinct concentrated routes should stay visually traceable."""

    source = r"""
        digraph disk_states {
          graph [concentrate=true]
          Inconsistent -> UpToDate [label="resync completed"]
          Inconsistent -> Failed [label="io completion error"]
          UpToDate -> Inconsistent [label=ioctl_replicate]
          UpToDate -> Failed [label="io completion error"]
          Consistent -> Inconsistent [label="start resync"]
          Consistent -> UpToDate [label="receive_param()"]
          Consistent -> Failed [label="io completion error"]
          Outdated -> Inconsistent [label="start resync"]
          Outdated -> Failed [label="io completion error"]
        }
    """
    layout = json.loads(dot("json", source=source))

    assert _tangential_touching_pair_count(layout) == 0


@pytest.mark.skipif(which("neato") is None, reason="neato not available")
def test_concentrate_train11_suppressed_arrows_survive_pos_roundtrip():
    """Suppressed junction arrows do not reappear after a positioned rerender."""

    source = (Path(__file__).parent / "graphs" / "train11.gv").read_text().replace(
        "digraph G {", "digraph G {\n  graph [concentrate=true];", 1
    )
    dot_layout = json.loads(dot("json", source=source))
    positioned = dot("dot", source=source)
    layout = json.loads(run(which("neato"), "-n2", "-Tjson", input=positioned))

    expected_hdraw = {
        tail: "_hdraw_" in _drawn_edge_between(dot_layout, tail, "st0")
        for tail in ("st8", "st6", "st4", "st10")
    }

    assert not expected_hdraw["st8"]
    assert expected_hdraw["st6"]
    assert expected_hdraw["st10"]
    for tail, has_hdraw in expected_hdraw.items():
        edge = _drawn_edge_between(layout, tail, "st0")
        assert ("_hdraw_" in edge) == has_hdraw


def test_concentrate_fanout_keeps_real_terminal_arrowheads():
    """Junction suppression does not hide arrows at real endpoint nodes."""

    source = """
        digraph {
          graph [concentrate=true]
          a -> c [minlen=2]
          a -> d [minlen=2]
        }
    """
    edges = _drawn_edges(source)

    assert len(edges) == 2
    assert all("_hdraw_" in edge for edge in edges)


def test_concentrate_backward_junction_arrows_follow_swapped_beziers():
    """Reversed concentrated splines keep endpoint arrow state with the points."""

    source = """
        digraph {
          graph [concentrate=true]
          a -> c [minlen=2]
          a -> d [minlen=2]
          c -> a [constraint=false minlen=2]
          d -> a [constraint=false minlen=2]
        }
    """
    edges = _drawn_edges(source)

    assert len(edges) == 2
    assert all(edge["pos"].startswith("s,") for edge in edges)
    assert all("_hdraw_" in edge and "_tdraw_" in edge for edge in edges)


def test_concentrate_compound_overlap_ignores_suppressed_junction_arrows():
    """Suppressed compound junction arrows do not collapse the retained route."""

    source = """
        digraph {
          graph [compound=true concentrate=true]
          node [shape=point width=0.05 height=0.05]
          subgraph cluster_tail {
            label=""
            margin=0
            t0
            t1
          }
          subgraph cluster_head {
            label=""
            margin=0
            h0
            h1
          }
          {rank=same; t0; h0}
          t0 -> h0 [ltail=cluster_tail lhead=cluster_head minlen=0 dir=both]
          h0 -> t0 [ltail=cluster_head lhead=cluster_tail minlen=0 dir=both]
        }
    """
    edges = _drawn_edges(source)
    assert len(edges) == 1
    points = _edge_bezier_points(edges[0])

    assert all(
        not (first == second == third)
        for first, second, third in zip(points, points[1:], points[2:])
    )


def _arrowhead_shaft_angle(edge: dict, stream: str = "_hdraw_") -> float:
    """Return the angle between a normal arrow and its shaft tangent."""

    polygon = next(
        operation["points"]
        for operation in edge[stream]
        if operation["op"] == "P"
    )
    assert len(polygon) == 3
    tip = polygon[1]
    bezier, endpoint_index = min(
        (
            (operation["points"], endpoint_index)
            for operation in edge["_draw_"]
            if operation["op"] == "b"
            for endpoint_index in (0, len(operation["points"]) - 1)
        ),
        key=lambda candidate: math.dist(candidate[0][candidate[1]], tip),
    )
    base_midpoint = (
        (polygon[0][0] + polygon[2][0]) / 2,
        (polygon[0][1] + polygon[2][1]) / 2,
    )
    endpoint = bezier[endpoint_index]
    oriented = bezier if endpoint_index == 0 else list(reversed(bezier))
    prior = next(
        point for point in oriented[1:] if math.dist(point, endpoint) > 0.001
    )
    shaft = (endpoint[0] - prior[0], endpoint[1] - prior[1])
    arrow_axis = (
        polygon[1][0] - base_midpoint[0],
        polygon[1][1] - base_midpoint[1],
    )
    cosine = sum(a * b for a, b in zip(shaft, arrow_axis)) / (
        math.hypot(*shaft) * math.hypot(*arrow_axis)
    )
    return math.degrees(math.acos(max(-1, min(1, cosine))))


def _polygon_self_intersections(points: list[list[float]]) -> list[tuple[int, int]]:
    """Return pairs of non-adjacent polygon edges that cross."""

    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (
            c[0] - a[0]
        )

    intersections = []
    for first in range(len(points)):
        for second in range(first + 1, len(points)):
            if second == first + 1 or (
                first == 0 and second == len(points) - 1
            ):
                continue
            a = points[first]
            b = points[(first + 1) % len(points)]
            c = points[second]
            d = points[(second + 1) % len(points)]
            if orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(
                c, d, a
            ) * orientation(c, d, b) < 0:
                intersections.append((first, second))
    return intersections


def _point_box(points: list[list[float]]) -> tuple[float, float, float, float]:
    """Return a point-list bounding box as left, bottom, right, top."""

    return tuple(map(min, zip(*points))) + tuple(map(max, zip(*points)))


def _boxes_are_disjoint(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Return whether two axis-aligned boxes have no area overlap."""

    return (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def _edge_arrow_polygon(edge: dict, stream: str) -> list[list[float]]:
    return next(
        operation["points"] for operation in edge[stream] if operation["op"] == "P"
    )


def _drawn_edges_between(source: str, tails: set[str], head: str) -> list[dict]:
    """Return visible edges from named tails into one named head node."""

    layout = json.loads(dot("json", source=source))
    node_ids = {node["name"]: node["_gvid"] for node in layout["objects"]}
    tail_ids = {node_ids[tail] for tail in tails}
    head_id = node_ids[head]
    return [
        edge
        for edge in layout["edges"]
        if edge["tail"] in tail_ids and edge["head"] == head_id and "_draw_" in edge
    ]


def _drawn_edge_between(layout: dict, tail: str, head: str) -> dict:
    """Return one visible edge between two named nodes."""

    node_ids = {node["name"]: node["_gvid"] for node in layout["objects"]}
    edges = [
        edge
        for edge in layout["edges"]
        if edge["tail"] == node_ids[tail]
        and edge["head"] == node_ids[head]
        and "_draw_" in edge
    ]
    assert len(edges) == 1
    return edges[0]


def _route_x_coordinates(edge: dict) -> tuple[float, ...]:
    """Return x coordinates from xdot's ``b`` Bezier operation."""

    bezier = next(operation for operation in edge["_draw_"] if operation["op"] == "b")
    return tuple(point[0] for point in bezier["points"])


def _sample_bezier_points(
    points: list[list[float]],
) -> Iterator[tuple[float, float]]:
    """Sample every cubic segment in one xdot Bezier operation."""

    for start in range(0, len(points) - 1, 3):
        control = points[start : start + 4]
        assert len(control) == 4
        for step in range(1001):
            t = step / 1000
            u = 1 - t
            yield (
                u**3 * control[0][0]
                + 3 * u**2 * t * control[1][0]
                + 3 * u * t**2 * control[2][0]
                + t**3 * control[3][0],
                u**3 * control[0][1]
                + 3 * u**2 * t * control[1][1]
                + 3 * u * t**2 * control[2][1]
                + t**3 * control[3][1],
            )


def _sample_bezier_points_with_tangents(
    points: list[list[float]],
) -> Iterator[tuple[tuple[float, float], tuple[float, float]]]:
    """Sample every cubic segment with its tangent vector."""

    for start in range(0, len(points) - 1, 3):
        control = points[start : start + 4]
        assert len(control) == 4
        for step in range(1001):
            t = step / 1000
            u = 1 - t
            point = (
                u**3 * control[0][0]
                + 3 * u**2 * t * control[1][0]
                + 3 * u * t**2 * control[2][0]
                + t**3 * control[3][0],
                u**3 * control[0][1]
                + 3 * u**2 * t * control[1][1]
                + 3 * u * t**2 * control[2][1]
                + t**3 * control[3][1],
            )
            tangent = (
                3 * u**2 * (control[1][0] - control[0][0])
                + 6 * u * t * (control[2][0] - control[1][0])
                + 3 * t**2 * (control[3][0] - control[2][0]),
                3 * u**2 * (control[1][1] - control[0][1])
                + 6 * u * t * (control[2][1] - control[1][1])
                + 3 * t**2 * (control[3][1] - control[2][1]),
            )
            yield point, tangent


def _angle_between_vectors(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Return the acute angle between two vectors in degrees."""

    denominator = math.hypot(*first) * math.hypot(*second)
    assert denominator > 0
    cosine = sum(a * b for a, b in zip(first, second)) / denominator
    angle = math.degrees(math.acos(max(-1, min(1, cosine))))
    return min(angle, 180 - angle)


def _crossing_angle(first_edge: dict, second_edge: dict) -> float:
    first_samples = tuple(
        _sample_bezier_points_with_tangents(_edge_bezier_points(first_edge))
    )
    second_samples = tuple(
        _sample_bezier_points_with_tangents(_edge_bezier_points(second_edge))
    )
    _, first, second = min(
        (math.dist(first[0], second[0]), first, second)
        for first in first_samples
        for second in second_samples
    )
    return _angle_between_vectors(first[1], second[1])


def _drawn_edge_arc_length(edge: dict) -> float:
    samples = _sample_drawn_edge(edge)
    return sum(math.dist(first, second) for first, second in zip(samples, samples[1:]))


def _orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False

    first = _orientation(a, b, c)
    second = _orientation(a, b, d)
    third = _orientation(c, d, a)
    fourth = _orientation(c, d, b)
    return first * second <= 0 and third * fourth <= 0


def _sampled_edge_crossing_count(edges: list[dict]) -> int:
    segments = []
    for edge in edges:
        samples = []
        for operation in edge["_draw_"]:
            if operation["op"] != "b":
                continue
            points = operation["points"]
            for segment_start in range(0, len(points) - 1, 3):
                control = points[segment_start : segment_start + 4]
                for step in range(9):
                    t = step / 8
                    u = 1 - t
                    samples.append(
                        (
                            u**3 * control[0][0]
                            + 3 * u**2 * t * control[1][0]
                            + 3 * u * t**2 * control[2][0]
                            + t**3 * control[3][0],
                            u**3 * control[0][1]
                            + 3 * u**2 * t * control[1][1]
                            + 3 * u * t**2 * control[2][1]
                            + t**3 * control[3][1],
                        )
                    )
        for start, end in zip(samples, samples[1:]):
            if math.dist(start, end) > 0.01:
                segments.append((edge.get("_gvid"), start, end))

    crossing_pairs = set()
    for index, (first_edge, a, b) in enumerate(segments):
        for second_edge, c, d in segments[index + 1 :]:
            if first_edge != second_edge and _segments_cross(a, b, c, d):
                crossing_pairs.add(tuple(sorted((first_edge, second_edge))))
    return len(crossing_pairs)


def _graph_width(layout: dict) -> float:
    left, _, right, _ = (float(value) for value in layout["bb"].split(","))
    return max(1.0, right - left)


def _pos_points(pos: str) -> list[tuple[float, float]]:
    values = [
        float(value)
        for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", pos)
    ]
    return list(zip(values[0::2], values[1::2]))


def _point_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.dist(point, start)
    t = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2,
        ),
    )
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _drawn_edge_polyline(edge: dict) -> list[tuple[float, float]]:
    """Flatten an edge's drawn route enough to catch tangent near-touches."""

    route = []
    for operation in edge.get("_draw_", []):
        if operation["op"] == "L":
            samples = [tuple(point) for point in operation["points"]]
        elif operation["op"] in {"B", "b"}:
            samples = []
            points = operation["points"]
            for start in range(0, len(points) - 1, 3):
                control = points[start : start + 4]
                assert len(control) == 4
                for step in range(13):
                    t = step / 12
                    u = 1 - t
                    samples.append(
                        (
                            u**3 * control[0][0]
                            + 3 * u**2 * t * control[1][0]
                            + 3 * u * t**2 * control[2][0]
                            + t**3 * control[3][0],
                            u**3 * control[0][1]
                            + 3 * u**2 * t * control[1][1]
                            + 3 * u * t**2 * control[2][1]
                            + t**3 * control[3][1],
                        )
                    )
        else:
            continue
        if route and samples and math.dist(route[-1], samples[0]) < 0.01:
            route.extend(samples[1:])
        else:
            route.extend(samples)
    return route


def _segment_distance(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> tuple[float, float, float]:
    """Return segment distance and normalized positions along both segments."""

    candidates = []
    for point, start, end, first_segment in (
        (a, c, d, True),
        (b, c, d, True),
        (c, a, b, False),
        (d, a, b, False),
    ):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length2 = dx * dx + dy * dy
        t = 0.0
        if length2 != 0:
            t = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                    / length2,
                ),
            )
        foot = (start[0] + t * dx, start[1] + t * dy)
        if first_segment:
            candidates.append((math.dist(point, foot), 0.0 if point == a else 1.0, t))
        else:
            candidates.append((math.dist(point, foot), t, 0.0 if point == c else 1.0))
    return min(candidates, key=lambda candidate: candidate[0])


def _route_lengths(points: list[tuple[float, float]]) -> list[float]:
    lengths = [0.0]
    for first, second in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(first, second))
    return lengths


def _tangential_touching_pair_count(layout: dict) -> int:
    """Count distinct drawn route pairs that almost touch while tangent."""

    routes = [
        (edge, _drawn_edge_polyline(edge))
        for edge in layout["edges"]
        if "_draw_" in edge
    ]
    routes = [(edge, route) for edge, route in routes if len(route) >= 2]
    count = 0
    for index, (first_edge, first_route) in enumerate(routes):
        first_lengths = _route_lengths(first_route)
        for second_edge, second_route in routes[index + 1 :]:
            second_lengths = _route_lengths(second_route)
            found = False
            for first_index, (a, b) in enumerate(zip(first_route, first_route[1:])):
                first_len = max(0.001, math.dist(a, b))
                for second_index, (c, d) in enumerate(
                    zip(second_route, second_route[1:])
                ):
                    if _segments_cross(a, b, c, d):
                        continue
                    distance, ta, tb = _segment_distance(a, b, c, d)
                    if distance >= 5:
                        continue
                    first_pos = first_lengths[first_index] + ta * first_len
                    second_len = max(0.001, math.dist(c, d))
                    second_pos = second_lengths[second_index] + tb * second_len
                    if (
                        min(
                            first_pos,
                            first_lengths[-1] - first_pos,
                            second_pos,
                            second_lengths[-1] - second_pos,
                        )
                        < 20
                    ):
                        continue
                    if _angle_between_vectors(
                        (b[0] - a[0], b[1] - a[1]),
                        (d[0] - c[0], d[1] - c[1]),
                    ) < 15:
                        found = True
                        break
                if found:
                    break
            if found:
                count += 1
    return count


def _endpoint_label_text_ops(edge: dict, stream: str) -> list[dict]:
    font_size = 8.0
    labels = []
    for operation in edge.get(stream, []):
        if operation["op"] == "F":
            font_size = operation["size"]
        elif operation["op"] == "T":
            labels.append(
                {
                    "center": tuple(operation["pt"]),
                    "width": operation["width"],
                    "height": font_size,
                    "font_size": font_size,
                }
            )
    return labels


def _endpoint_label_overlap_area(first: dict, second: dict) -> float:
    ax, ay = first["center"]
    bx, by = second["center"]
    x_overlap = max(
        0.0,
        min(ax + first["width"] / 2, bx + second["width"] / 2)
        - max(ax - first["width"] / 2, bx - second["width"] / 2),
    )
    y_overlap = max(
        0.0,
        min(ay + first["height"] / 2, by + second["height"] / 2)
        - max(ay - first["height"] / 2, by - second["height"] / 2),
    )
    return x_overlap * y_overlap


def _endpoint_label_detach_honda_score(layout: dict) -> tuple[int, int]:
    labels = []
    detached = 0
    for edge in layout["edges"]:
        points = _pos_points(edge.get("pos", ""))
        for stream in ("_hldraw_", "_tldraw_"):
            for label in _endpoint_label_text_ops(edge, stream):
                distance = min(
                    (
                        _point_segment_distance(label["center"], start, end)
                        for start, end in zip(points, points[1:])
                    ),
                    default=math.inf,
                )
                if distance / label["font_size"] > 0.45:
                    detached += 1
                labels.append(label)

    overlaps = 0
    for first, second in itertools.combinations(labels, 2):
        area = _endpoint_label_overlap_area(first, second)
        smaller = min(
            first["width"] * first["height"], second["width"] * second["height"]
        )
        if smaller > 0 and area / smaller > 0.50:
            overlaps += 1
    return detached, overlaps


def _longest_horizontal_chain(points: list[tuple[float, float]]) -> float:
    best = 0.0
    current = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        angle = abs(math.degrees(math.atan2(dy, dx)))
        angle = min(angle, 180 - angle)
        if angle <= 10:
            current += length
            best = max(best, current)
        else:
            current = 0.0
    return best


def _long_horizontal_edge_count(layout: dict) -> int:
    min_chain = _graph_width(layout) * 0.4
    return sum(
        _longest_horizontal_chain(_pos_points(edge["pos"])) > min_chain
        for edge in layout["edges"]
        if "pos" in edge
    )


def _longest_layout_path(nodes: set[int], edges: list[tuple[int, int]]) -> list[int]:
    outgoing = {node: [] for node in nodes}
    for tail, head in edges:
        if tail in nodes and head in nodes:
            outgoing.setdefault(tail, []).append(head)

    def walk(node: int, active: frozenset[int] = frozenset()) -> tuple[int, ...]:
        if node in active:
            return ()
        best = (node,)
        next_active = active | {node}
        for head in outgoing.get(node, []):
            suffix = walk(head, next_active)
            if suffix and len((node,) + suffix) > len(best):
                best = (node,) + suffix
        return best

    return list(max((walk(node) for node in nodes), key=len, default=()))


def _normalized_longest_path_drift(layout: dict) -> float:
    centers = {
        node["_gvid"]: tuple(float(value) for value in node["pos"].split(",", 1))
        for node in layout["objects"]
        if "pos" in node
    }
    path = _longest_layout_path(
        set(centers),
        [(edge["tail"], edge["head"]) for edge in layout["edges"]],
    )
    xs = [centers[node][0] for node in path]
    assert xs
    return (max(xs) - min(xs)) / _graph_width(layout)


_CONCENTRATE_ABSTRACT_HORIZONTAL_BUS_MINIMIZED = r"""
    digraph abstract {
      graph [concentrate=true, size="6,6"]
      10 -> T1
      10 -> 11
      10 -> 14
      10 -> 13
      10 -> 12
      11 -> 4
      14 -> 15
      13 -> 19
      4 -> 5
      15 -> T1
      3 -> 4
      S35 -> 36
      S35 -> 43
      36 -> 19
      43 -> 38
      43 -> 40
      38 -> 4
      40 -> 19
      S30 -> 31
      S30 -> 33
      31 -> T1
      31 -> 32
      9 -> T1
      9 -> 42
      42 -> 4
      37 -> 38
      37 -> 40
      37 -> 39
      37 -> 41
      39 -> 15
    }
"""


_CONCENTRATE_ROWE_SPINE_BEND_MINIMIZED = r"""
    digraph rowe {
      graph [concentrate=true, size="6,6"]
      node [shape=box]
      4 -> 5
      5 -> 23
      5 -> 35
      23 -> 24
      35 -> 36
      24 -> 25
      24 -> 27
      36 -> 19
      25 -> 26
      19 -> 28
      19 -> 21
      26 -> 4
      28 -> 29
      21 -> 22
      29 -> 30
      22 -> 23
      30 -> 31
      30 -> 33
      31 -> 32
      33 -> 34
      32 -> 23
      40 -> 19
      38 -> 4
      37 -> 40
      37 -> 38
    }
"""


def test_concentrate_abstract_minimized_has_no_horizontal_bus():
    """Concentration should not create long flat bus segments."""

    layout = json.loads(dot("json", source=_CONCENTRATE_ABSTRACT_HORIZONTAL_BUS_MINIMIZED))
    assert _long_horizontal_edge_count(layout) < 3


def test_concentrate_rowe_minimized_spine_does_not_bend():
    """Concentration should not collapse a long spine into an S-bend."""

    layout = json.loads(dot("json", source=_CONCENTRATE_ROWE_SPINE_BEND_MINIMIZED))
    assert _normalized_longest_path_drift(layout) <= 0.733


def _point_distance_to_line(
    point: tuple[float, float] | list[float],
    start: tuple[float, float] | list[float],
    end: tuple[float, float] | list[float],
) -> float:
    """Return a point's perpendicular distance from an infinite line."""

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    assert length > 0
    return abs(dx * (start[1] - point[1]) - dy * (start[0] - point[0])) / length


def _max_bezier_deviation_from_chord(points: list[list[float]]) -> float:
    """Sample a Bezier and measure how far it bows from its endpoint chord."""

    return max(
        _point_distance_to_line(point, points[0], points[-1])
        for point in _sample_bezier_points(points)
    )


def _edge_bezier_points(edge: dict) -> list[list[float]]:
    """Return the control points of an edge's visible route."""

    return next(operation["points"] for operation in edge["_draw_"] if operation["op"] == "b")


def _sample_drawn_edge(edge: dict) -> tuple[tuple[float, float], ...]:
    """Sample all visible Bezier segments for one drawn edge."""

    return tuple(
        point
        for operation in edge["_draw_"]
        if operation["op"] == "b"
        for point in _sample_bezier_points(operation["points"])
    )


def _max_pointwise_route_distance(first_edge: dict, second_edge: dict) -> float:
    """Compare two sampled routes at equal normalized sample indexes."""

    first = _sample_drawn_edge(first_edge)
    second = _sample_drawn_edge(second_edge)
    sample_count = min(len(first), len(second))
    assert sample_count > 1
    return max(math.dist(first[index], second[index]) for index in range(sample_count))


def _ellipse(node: dict) -> tuple[float, float, float, float]:
    """Return an ellipse node's center and radii."""

    return tuple(
        next(operation["rect"] for operation in node["_draw_"] if operation["op"] == "e")
    )


def _assert_endpoint_departure(layout: dict, edge: dict, endpoint: str) -> None:
    """A route leaves an endpoint outward and does not re-enter its stroke."""

    node_id = edge["tail" if endpoint == "tail" else "head"]
    node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
    center_x, center_y, radius_x, radius_y = _ellipse(node)
    points = _edge_bezier_points(edge)
    # Flat auxiliary routing can preserve the Bezier in physical rather than
    # logical edge order. Orient it by the endpoint node under test.
    route = min(
        (points, list(reversed(points))),
        key=lambda candidate: math.dist(candidate[0], (center_x, center_y)),
    )
    anchor = route[0]
    outward_point = next(point for point in route[1:] if math.dist(point, anchor) > 0.001)
    outward_normal = (anchor[0] - center_x, anchor[1] - center_y)
    departure = (outward_point[0] - anchor[0], outward_point[1] - anchor[1])
    assert sum(a * b for a, b in zip(outward_normal, departure)) > 0

    pen_radius = float(edge.get("penwidth", 1)) / 2
    sampled = tuple(_sample_bezier_points(route))
    normalized = tuple(
        ((x - center_x) / (radius_x + pen_radius)) ** 2
        + ((y - center_y) / (radius_y + pen_radius)) ** 2
        for x, y in sampled
    )
    clear_index = next(index for index, distance in enumerate(normalized) if distance >= 1)
    # Xdot rounds every control point to 0.01 pt; allow the corresponding
    # sub-point sampling drift around the half-stroke clearance boundary.
    assert min(normalized[clear_index:]) >= 0.98


def _assert_compass_attachment(layout: dict, edge: dict, endpoint: str) -> None:
    """A named compass port attaches at the requested ellipse extremum."""

    port_name = edge.get(f"{endpoint}port")
    if port_name not in {"n", "s", "e", "w"}:
        return
    node_id = edge["tail" if endpoint == "tail" else "head"]
    node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
    center_x, center_y, radius_x, radius_y = _ellipse(node)
    attachment = _edge_physical_endpoint(edge, endpoint)
    expected = {
        "n": (center_x, center_y + radius_y),
        "s": (center_x, center_y - radius_y),
        "e": (center_x + radius_x, center_y),
        "w": (center_x - radius_x, center_y),
    }[port_name]
    # The route clips against the shape outline, while the `e` marker records
    # the arrow tip one point beyond its shaft and xdot reports the stroked
    # ellipse. Account for both representations without admitting a field- or
    # node-center attachment.
    assert attachment == pytest.approx(expected, abs=1.5)


def _assert_distinct_drawn_edge_routes(source: str, expected_count: int) -> None:
    """Assert the number of distinct visible routes in a graph."""

    routes = {_route_x_coordinates(edge) for edge in _drawn_edges(source)}
    assert len(routes) == expected_count


def _edge_count_case(expected_count: int, *body: str) -> tuple[int, tuple[str, ...]]:
    """Describe one graph and its expected number of visible edges."""

    return expected_count, body


def _named_edge_count_cases(case_id: str, *cases: tuple[int, tuple[str, ...]]):
    """Give a group of count oracles one meaningful pytest ID."""

    return pytest.param(cases, id=case_id)


def _fixed_edge_count_cases(
    case_id: str, splines: str, *cases: tuple[int, tuple[str, ...]]
):
    """Give fixed-spline count oracles one meaningful pytest ID."""

    return pytest.param(splines, cases, id=case_id)


def _assert_concentrated_edge_counts(
    splines: str, cases: tuple[tuple[int, tuple[str, ...]], ...]
) -> None:
    """Check every independent drawn-edge-count oracle in a table row."""

    for expected_count, body in cases:
        source = _concentrated_graph(splines, *body)
        assert len(_drawn_edges(source)) == expected_count


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
@pytest.mark.parametrize(
    "cases",
    (
        _named_edge_count_cases(
            "distinct-endpoint-label-colors",
            _edge_count_case(
                4,
                "a -> b [label=x fontcolor=red]",
                "a -> b [label=x fontcolor=blue]",
                "b -> c [headlabel=x labelfontcolor=red]",
                "b -> c [headlabel=x labelfontcolor=blue]",
            ),
        ),
        _named_edge_count_cases(
            "ignore-unused-label-colors",
            _edge_count_case(
                4,
                """
                      a -> b [fontcolor=red]
                      a -> b [fontcolor=blue]
                      b -> c [labelfontcolor=red]
                      b -> c [labelfontcolor=blue]
                      c -> d [headlabel=x fontcolor=red labelfontcolor=black]
                      c -> d [headlabel=x fontcolor=blue labelfontcolor=black]
                      d -> e [fontsize=20 fontname=Courier labelfontsize=20
                              labelfontname=Courier labeldistance=2 labelangle=30
                              decorate=true]
                      d -> e
                    """,
            ),
        ),
        _named_edge_count_cases(
            "ignore-endpoint-label-decorate",
            _edge_count_case(
                1,
                "a -> b [headlabel=x decorate=false]",
                "a -> b [headlabel=x decorate=true]",
            ),
        ),
        _named_edge_count_cases(
            "labelaligned-plain-primary-label-only",
            _edge_count_case(
                1,
                "a -> b [labelaligned=true]",
                "a -> b",
            ),
        ),
        _named_edge_count_cases(
            "explicit-rendering-defaults",
            _edge_count_case(
                4,
                """
                      a -> b [style=solid penwidth=1 arrowsize=1]
                      a -> b
                      b -> c [labelfloat=false]
                      b -> c
                      c -> d [dir=none arrowsize=2]
                      c -> d [dir=none]
                      d -> e [dir=none fillcolor=red]
                      d -> e [dir=none fillcolor=blue]
                    """,
            ),
        ),
        _named_edge_count_cases(
            "tooltip-aliases-and-substitution",
            _edge_count_case(1, 'a -> b [tooltip="tip"]', 'a -> b [edgetooltip="tip"]'),
            _edge_count_case(
                1,
                'a -> b [tooltip="\\T"]',
                'b -> a [edgetooltip="\\T"]',
            ),
            _edge_count_case(
                1,
                'a -> b [headlabel=x headtooltip="\\T"]',
                'b -> a [taillabel=x tailtooltip="\\T"]',
            ),
        ),
        _named_edge_count_cases(
            "url-href-aliases",
            _edge_count_case(
                4,
                'a -> b [URL="u"]',
                'a -> b [href="u"]',
                'b -> c [edgeURL="u"]',
                'b -> c [edgehref="u"]',
                'c -> d [labelURL="u"]',
                'c -> d [labelhref="u"]',
                'd -> e [URL="u"]',
                'd -> e [edgeURL="u"]',
            ),
            _edge_count_case(1, 'a -> b [headURL="u"]', 'b -> a [tailhref="u"]'),
            _edge_count_case(2, 'a -> b [headURL="\\T"]', 'b -> a [tailhref="\\T"]'),
            _edge_count_case(2, "a -> b [URL=<\\T>]", "b -> a [href=<\\T>]"),
            _edge_count_case(2, 'a -> b [headURL="u"]', 'b -> a [headhref="u"]'),
        ),
        _named_edge_count_cases(
            "endpoint-url-is-not-edge-only-url",
            _edge_count_case(
                2,
                'a -> b [headlabel=x URL="u"]',
                'a -> b [headlabel=x edgeURL="u"]',
            ),
            _edge_count_case(
                2,
                'a -> b [headlabel=x URL="u" headtooltip="tip"]',
                'a -> b [headlabel=x edgeURL="u" headtooltip="tip"]',
            ),
            _edge_count_case(
                1,
                'a -> b [headlabel=x URL="u"]',
                'a -> b [headlabel=x href="u"]',
            ),
        ),
        _named_edge_count_cases(
            "target-fallbacks",
            _edge_count_case(
                1, "a -> b [URL=u target=t]", "a -> b [URL=u edgetarget=t]"
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x headURL=u target=t]",
                "a -> b [headlabel=x headURL=u headtarget=t]",
            ),
            _edge_count_case(
                2,
                "a -> b [headlabel=x URL=u headtarget=one]",
                "a -> b [headlabel=x URL=u headtarget=two]",
            ),
            _edge_count_case(
                2,
                "a -> b [URL=u target=<\\T>]",
                "b -> a [URL=u edgetarget=<\\T>]",
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x headtarget=one]",
                "a -> b [headlabel=x headtarget=two]",
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x headtarget=one]",
                "b -> a [taillabel=x tailtarget=two]",
            ),
        ),
        _named_edge_count_cases(
            "substituted-ids",
            _edge_count_case(1, 'a -> b [id="\\T"]', 'a -> b [id="a"]'),
            _edge_count_case(2, 'a -> b [id="\\T"]', 'b -> a [id="\\T"]'),
            _edge_count_case(2, "a -> b [id=<\\T>]", "b -> a [id=<\\T>]"),
        ),
        _named_edge_count_cases(
            "endpoint-label-font-fallbacks",
            _edge_count_case(
                1,
                "a -> b [headlabel=x fontsize=20]",
                "a -> b [headlabel=x fontsize=20 labelfontsize=20]",
            ),
            _edge_count_case(
                1,
                "a -> b [headlabel=x fontname=Courier]",
                "a -> b [headlabel=x fontname=Courier labelfontname=Courier]",
            ),
        ),
        _named_edge_count_cases(
            "undeclared-endpoint-label-font-fallbacks",
            _edge_count_case(
                4,
                "a -> b [headlabel=x fontname=Courier]",
                "a -> b [headlabel=x fontname=Times]",
                "b -> c [headlabel=x fontsize=10]",
                "b -> c [headlabel=x fontsize=30]",
            ),
        ),
        _named_edge_count_cases(
            "samehead-sametail-physical-endpoint",
            _edge_count_case(1, "a -> b [samehead=x]", "b -> a [sametail=x]"),
            _edge_count_case(1, "a -> b [samehead=x]", "b -> a [samehead=x]"),
        ),
        _named_edge_count_cases(
            "lhead-ltail-physical-endpoint",
            _edge_count_case(
                1,
                "graph [compound=true]",
                "subgraph cluster_a { a }",
                "subgraph cluster_b { b }",
                "a -> b [ltail=cluster_a lhead=cluster_b]",
                "b -> a [ltail=cluster_b lhead=cluster_a]",
            ),
        ),
        _named_edge_count_cases(
            "gate-cluster-endpoints-on-compound",
            _edge_count_case(
                1,
                "a",
                "subgraph cluster_outer { subgraph cluster_inner { b } }",
                "a -> b [lhead=cluster_inner]",
                "b -> a [ltail=cluster_outer]",
            ),
            _edge_count_case(
                2,
                "graph [compound=true]",
                "a",
                "subgraph cluster_outer { subgraph cluster_inner { b } }",
                "a -> b [lhead=cluster_inner]",
                "b -> a [ltail=cluster_outer]",
            ),
        ),
        _named_edge_count_cases(
            # A shared-trunk join can leave the joining lhead edge with no
            # arrow of its own (the trunk accumulates the tip), and
            # makeCompoundEdge() must clip that arrowless compound spline
            # instead of asserting that an end arrow exists.
            "compound-trunk-arrowless-representative",
            _edge_count_case(
                2,
                "graph [compound=true]",
                "Andrus -> cluster_BE [minlen=4 lhead=cluster_BE weight=100]",
                "Alexei -> cluster_BE [minlen=4 lhead=cluster_BE]",
                "subgraph cluster_BE { cluster_BE -> AndrusBE [style=invis] }",
            ),
        ),
        _named_edge_count_cases(
            "endpoint-label-substitution",
            _edge_count_case(
                1,
                'a -> b [headlabel="\\H"]',
                'b -> a [taillabel="\\T"]',
            ),
            _edge_count_case(
                2,
                'a -> b [headlabel="\\T"]',
                'b -> a [taillabel="\\T"]',
            ),
            _edge_count_case(
                2,
                'a -> b [headlabel=<<TABLE HREF="\\T"><TR><TD>x</TD></TR></TABLE>>]',
                'b -> a [taillabel=<<TABLE HREF="\\T"><TR><TD>x</TD></TR></TABLE>>]',
            ),
        ),
        _named_edge_count_cases(
            "html-like-versus-plain-attribute",
            _edge_count_case(
                2,
                "a -> b [headlabel=<<B>x</B>>]",
                'a -> b [headlabel="<B>x</B>"]',
            ),
        ),
        _named_edge_count_cases(
            "dynamic-record-ports",
            _edge_count_case(
                2,
                "node [shape=record]",
                'a [label="<p> p"]',
                "b",
                "a:p -> b",
                "a:p:c -> b",
            ),
        ),
        _named_edge_count_cases(
            "dynamic-compass-ports",
            _edge_count_case(2, "a -> b:_", "a -> b"),
            _edge_count_case(2, "a:_ -> b", "a -> b"),
        ),
        _named_edge_count_cases(
            "ignore-layout-only-attributes",
            _edge_count_case(
                3,
                "a -> b [constraint=false]",
                "a -> b",
                "b -> c [weight=8]",
                "b -> c",
                "c -> d [minlen=2]",
                "c -> d",
            ),
        ),
        _named_edge_count_cases(
            "explicit-default-edge-attributes",
            _edge_count_case(
                3,
                "a -> b [dir=forward]",
                "a -> b",
                "b -> c [headclip=true]",
                "b -> c",
                "c -> d [tailclip=true]",
                "c -> d",
            ),
        ),
        _named_edge_count_cases(
            "invalid-numeric-defaults",
            _edge_count_case(1, "a -> b [penwidth=bogus]", "a -> b"),
            _edge_count_case(
                1,
                "b -> c [headlabel=x labeldistance=bogus]",
                "b -> c [headlabel=x]",
            ),
        ),
    ),
)
def test_concentrate_drawn_edge_counts(
    splines: str, cases: tuple[tuple[int, tuple[str, ...]], ...]
):
    """Structurally identical concentration scenarios keep readable case IDs."""

    _assert_concentrated_edge_counts(splines, cases)


def test_concentrate_main_label_bare_url_anchors_labeltarget():
    """Bare edge URL fallback makes main labeltarget visible to identity."""

    source = _concentrated_graph(
        "",
        "a -> b [label=x URL=u labeltarget=one]",
        "a -> b [label=x URL=u labeltarget=two]",
    )
    assert len(_drawn_edges(source)) == 2


def test_concentrate_html_table_anchor_identity_matches_emit_gate():
    """HTML table TARGET/ID are visible only on URL or tooltip anchors."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [headlabel=<<TABLE TARGET="one"><TR><TD>x</TD></TR></TABLE>>]',
                'b -> a [taillabel=<<TABLE TARGET="two"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                1,
                'c -> d [headlabel=<<TABLE ID="one"><TR><TD>x</TD></TR></TABLE>>]',
                'd -> c [taillabel=<<TABLE ID="two"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'e -> f [headlabel=<<TABLE HREF="u" TARGET="one" ID="one"><TR><TD>x</TD></TR></TABLE>>]',
                'f -> e [taillabel=<<TABLE HREF="u" TARGET="two" ID="two"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'g -> h [headlabel=<<TABLE HREF="u"><TR><TD TARGET="one">x</TD></TR></TABLE>>]',
                'h -> g [taillabel=<<TABLE HREF="u"><TR><TD TARGET="two">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'i -> j [label=<<TABLE TOOLTIP="tip"><TR><TD TARGET="one">x</TD></TR></TABLE>>]',
                'i -> j [label=<<TABLE TOOLTIP="tip"><TR><TD TARGET="two">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'k -> l [headlabel=<<TABLE BORDER="0"><TR><TD HREF="u">x</TD><TD>y</TD></TR></TABLE>>]',
                'k -> l [headlabel=<<TABLE BORDER="0"><TR><TD>x</TD><TD HREF="u">y</TD></TR></TABLE>>]',
            ),
        ),
    )


@pytest.mark.parametrize(
    ("splines", "cases"),
    (
        _fixed_edge_count_cases(
            "colorscheme-resolution",
            "",
            _edge_count_case(
                3,
                "a -> b [colorscheme=X11]",
                "a -> b",
                "b -> c [colorscheme=X11 color=green]",
                "b -> c [colorscheme=svg color=green]",
            ),
        ),
        _fixed_edge_count_cases(
            "colorscheme-color-list-resolution",
            "",
            _edge_count_case(
                3,
                'a -> b [colorscheme=accent3 color="1:2"]',
                'a -> b [colorscheme=accent3 color="1:2"]',
                'b -> c [colorscheme=accent3 color="1:2"]',
                'b -> c [colorscheme=paired3 color="1:2"]',
            ),
        ),
        _fixed_edge_count_cases(
            "main-label-decorate",
            "splines=ortho",
            _edge_count_case(
                1,
                "a -> b [label=x decorate=false]",
                "a -> b [label=x decorate=false]",
            ),
            _edge_count_case(
                2,
                "a -> b [label=x decorate=false]",
                "a -> b [label=x decorate=true]",
            ),
        ),
        _fixed_edge_count_cases(
            "labelaligned-plain-primary-label",
            "splines=ortho",
            _edge_count_case(
                4,
                "a -> b [label=<x> labelaligned=true]",
                "a -> b [label=<x>]",
                "b -> c [label=x labelaligned=true]",
                "b -> c [label=x]",
                "c -> d [xlabel=x labelaligned=true]",
                "c -> d [xlabel=x]",
            ),
        ),
        _fixed_edge_count_cases(
            "labelfloat-primary-label-gate",
            "splines=ortho",
            _edge_count_case(
                1,
                "a -> b [xlabel=x labelfloat=false]",
                "a -> b [xlabel=x labelfloat=true]",
            ),
            _edge_count_case(
                1,
                "a -> b [label=x labelfloat=false]",
                "a -> b [label=x labelfloat=false]",
            ),
            _edge_count_case(
                2,
                "a -> b [label=x labelfloat=false]",
                "a -> b [label=x labelfloat=true]",
            ),
        ),
        _fixed_edge_count_cases(
            "multicolor-arrow-fillcolor",
            "",
            _edge_count_case(
                3,
                'a -> b [color="red:blue" fillcolor=green]',
                'a -> b [color="red:blue" fillcolor=yellow]',
                "b -> c [color=red fillcolor=green]",
                "b -> c [color=red fillcolor=yellow]",
            ),
        ),
        _fixed_edge_count_cases(
            "label-tooltip-render-gate",
            "",
            _edge_count_case(
                3,
                "a -> b [labeltooltip=left]",
                "a -> b [labeltooltip=right]",
                "b -> c [label=x labeltooltip=left]",
                "b -> c [label=x labeltooltip=right]",
            ),
        ),
        _fixed_edge_count_cases(
            "implicit-endpoint-label-tooltip",
            "",
            _edge_count_case(
                3,
                "a -> b [headlabel=x headURL=u]",
                "a -> b [headlabel=x headURL=u headtooltip=x]",
                "b -> c [headlabel=x headURL=u]",
                "b -> c [headlabel=x headURL=u headtooltip=y]",
            ),
        ),
        _fixed_edge_count_cases(
            "preprocess-explicit-tooltip",
            "",
            _edge_count_case(
                3,
                'a -> b [tooltip="A&amp;B"]',
                'a -> b [tooltip="A&B"]',
                'b -> c [tooltip="A&amp;B"]',
                'b -> c [tooltip="A&C"]',
            ),
        ),
        _fixed_edge_count_cases(
            "overridden-endpoint-label-fonts",
            "",
            _edge_count_case(
                6,
                "a -> b [headlabel=x fontname=Courier labelfontname=Helvetica]",
                "a -> b [headlabel=x fontname=Times labelfontname=Helvetica]",
                "b -> c [headlabel=x fontsize=10 labelfontsize=20]",
                "b -> c [headlabel=x fontsize=30 labelfontsize=20]",
                "c -> d [headlabel=x fontname=Courier]",
                "c -> d [headlabel=x fontname=Times]",
                "d -> e [headlabel=x fontsize=10]",
                "d -> e [headlabel=x fontsize=30]",
            ),
        ),
    ),
)
def test_concentrate_fixed_spline_drawn_edge_counts(
    splines: str, cases: tuple[tuple[int, tuple[str, ...]], ...]
):
    """Count-only scenarios that intentionally use one spline mode."""

    _assert_concentrated_edge_counts(splines, cases)


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_numeric_identity_matches_late_double_prefixes(splines: str):
    """emit_begin_edge()/place_portlabel() consume late_double() prefixes."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, 'a -> b [penwidth="2pt"]', "a -> b [penwidth=2]"),
            _edge_count_case(
                1,
                'b -> c [headlabel=x labeldistance="1x"]',
                "b -> c [headlabel=x labeldistance=1]",
            ),
        ),
    )


def test_concentrate_color_list_identity_matches_parse_segs_fractions():
    """multicolor() consumes parseSegs() normalized segment fractions."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [dir=none color="red:blue"]',
                'a -> b [dir=none color="red;0.5:blue;0.5"]',
            ),
            _edge_count_case(
                1,
                'b -> c [dir=none color="red;1:blue"]',
                "b -> c [dir=none color=red]",
            ),
            _edge_count_case(
                2,
                'c -> d [dir=none color="red;0.5:blue;0.5"]',
                'd -> c [dir=none color="red;0.5:blue;0.5"]',
            ),
            _edge_count_case(
                2,
                'g -> h [dir=none color="red:blue"]',
                'h -> g [dir=none color="red:blue"]',
            ),
            _edge_count_case(
                1,
                'e -> f [dir=none color="red;0.5:blue;0.5"]',
                'f -> e [dir=none color="blue;0.5:red;0.5"]',
            ),
        ),
    )


def test_concentrate_color_list_identity_keeps_empty_lanes():
    """Empty color-list lanes change the stroke count, so they stay distinct.

    color="red::blue" renders three parallel-stroke lanes (raw colon
    count) while "red:blue" renders two; the identity keeps them
    separate."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [dir=none color="red::blue"]',
                'a -> b [dir=none color="red:blue"]',
            ),
        ),
    )


def test_concentrate_radius_identity_is_gated_on_ortho_edges():
    """emit_edge_graphics() consumes edge radius only for splines=ortho."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(1, "a -> b [radius=5]", "a -> b"),
            _edge_count_case(1, "b -> c [style=rounded]", "b -> c"),
        ),
    )
    _assert_concentrated_edge_counts(
        "splines=ortho",
        (
            _edge_count_case(2, "a -> b [radius=5]", "a -> b"),
            _edge_count_case(1, "c -> d [radius=bogus]", "c -> d"),
            _edge_count_case(1, "d -> e [radius=-5]", "d -> e"),
            _edge_count_case(1, "e -> f [radius=NaN]", "e -> f"),
            _edge_count_case(2, "b -> c [style=rounded]", "b -> c"),
        ),
    )


def test_concentrate_ortho_rounded_style_only_when_drawn():
    """emit_edge_graphics() ignores rounded on non-simple ortho edge branches."""

    _assert_concentrated_edge_counts(
        "splines=ortho",
        (
            _edge_count_case(
                1,
                'a -> b [color="red:blue" style=rounded]',
                'a -> b [color="red:blue"]',
            ),
            _edge_count_case(
                1,
                'b -> c [style="tapered,rounded"]',
                "b -> c [style=tapered]",
            ),
            _edge_count_case(2, "c -> d [style=rounded]", "c -> d"),
        ),
    )


def test_concentrate_layer_identity_requires_declared_graph_layers():
    """emit_edge() ignores edge layer when no graph layers are declared."""

    _assert_concentrated_edge_counts(
        "",
        (_edge_count_case(1, "a -> b [layer=x]", "a -> b"),),
    )
    visible_layer = """
        digraph {
          graph [concentrate=true layers="x:y" layerselect=y]
          a -> b [layer=x]
          a -> b [layer=y]
        }
    """
    assert len(_drawn_edges(visible_layer)) == 1


def test_compound_close_edge_preserves_ordinary_tail_arrow():
    """Short non-concentrated compound edges keep explicit dir=both arrows."""

    source = """
        digraph {
          graph [compound=true]
          subgraph cluster_a { a }
          subgraph cluster_b { b }
          a -> b [ltail=cluster_a lhead=cluster_b dir=both minlen=0]
        }
    """
    layout = json.loads(dot("json", source=source))
    assert "_tdraw_" in layout["edges"][0]
    assert "_hdraw_" in layout["edges"][0]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_setlinewidth_style_folds_into_penwidth(splines: str):
    """emit_begin_edge() renders style=setlinewidth(N) as penwidth=N."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(
                1,
                'a -> b [style="setlinewidth(2)"]',
                "a -> b [penwidth=2]",
            ),
            _edge_count_case(
                1,
                'b -> c [style="dashed,setlinewidth(2)"]',
                "b -> c [style=dashed penwidth=2]",
            ),
            _edge_count_case(
                2,
                'c -> d [style="setlinewidth(1)"]',
                'c -> d [style="setlinewidth(4)"]',
            ),
            _edge_count_case(
                2,
                'e -> f [style="setlinewidth(-1)"]',
                'e -> f [style="setlinewidth(0)"]',
            ),
            _edge_count_case(
                2,
                'g -> h [style="setlinewidth(1),setlinewidth(4)"]',
                'g -> h [style="setlinewidth(1)"]',
            ),
            _edge_count_case(
                1,
                'i -> j [style="setlinewidth(2)" penwidth=3]',
                "i -> j [penwidth=3]",
            ),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_setlinewidth_checks_this_edge_penwidth(splines: str):
    """style=setlinewidth(N) renders even when another edge declares penwidth."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(
                3,
                'a -> b [style="setlinewidth(4)"]',
                'a -> b [style="setlinewidth(2)"]',
                "c -> d [penwidth=1]",
            ),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_bold_style_folds_into_penwidth(splines: str):
    """gvrender_set_style() renders style=bold as PENWIDTH_BOLD."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, "a -> b [style=bold]", "a -> b [penwidth=2]"),
            _edge_count_case(2, "c -> d [style=bold]", "c -> d [penwidth=3]"),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_edge_style_identity_uses_final_pen_token(splines: str):
    """gvrender_set_style() applies the last pen-pattern style token."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, 'a -> b [style="dashed,solid"]', "a -> b"),
            _edge_count_case(1, 'b -> c [style="solid,dashed"]', 'b -> c [style=dashed]'),
            _edge_count_case(2, 'c -> d [style="dashed,solid"]', 'c -> d [style=dashed]'),
        ),
    )


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_edge_style_identity_keeps_invis_absorbing(splines: str):
    """emit_edge() skips an edge as soon as any style token is invis."""

    source = _concentrated_graph(
        splines,
        'a -> b [style="invis,solid"]',
        "a -> b",
    )
    assert len(_drawn_edges(source)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_edge_style_identity_treats_invisible_as_pen_token(
    splines: str,
):
    """The renderer treats `invis` as absorbing, but not `invisible`."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(
                2, 'a -> b [style="invisible,solid"]', "a -> b [style=solid]"
            ),
        ),
    )


def test_concentrate_plain_label_identity_uses_compiled_text():
    """Escaped and literal newlines render alike, but justification does not."""

    same_rendered_label = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", 'a -> b [headlabel="a\nb"]'
    ).replace("b -> a", 'b -> a [taillabel="a\\nb"]')
    assert len(_drawn_edges(same_rendered_label)) == 1

    different_justification = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", 'a -> b [headlabel="a\\lb"]'
    ).replace("b -> a", 'b -> a [taillabel="a\\nb"]')
    assert len(_drawn_edges(different_justification)) == 2


def test_concentrate_label_dedupe_keeps_distinct_rendered_label_links():
    """Equal label text/position cannot hide different label anchors."""

    source = _concentrated_graph(
        "",
        "a -> b [label=x labelURL=one]",
        "a -> b [label=x labelURL=two]",
    )
    edges = _drawn_edges(source)
    assert len(edges) == 2
    assert len([edge for edge in edges if edge.get("label") == "x"]) == 2


def test_concentrate_html_endpoint_label_identity_uses_substituted_text():
    """HTML endpoint labels emit edge substitutions, so identity must too."""

    same_direction_distinct = _concentrated_graph(
        "",
        'a -> b [headlabel=<<FONT COLOR="red">\\T</FONT>>]',
        'a -> b [headlabel=<<FONT COLOR="red">\\H</FONT>>]',
    )
    assert len(_drawn_edges(same_direction_distinct)) == 2

    opposite_direction_distinct = _concentrated_graph(
        "",
        'a -> b [headlabel=<<FONT COLOR="red">\\T</FONT>>]',
        'b -> a [taillabel=<<FONT COLOR="red">\\T</FONT>>]',
    )
    assert len(_drawn_edges(opposite_direction_distinct)) == 2


def test_concentrate_html_label_identity_uses_parsed_text_once():
    """make_html_label() already applies object substitutions inside HTML text."""

    tail = r"\H"
    double_substituted = _concentrated_graph(
        "",
        f'"{tail}" -> head [headlabel=<<FONT>\\T</FONT>>]',
        f'"{tail}" -> head [headlabel=<<FONT>head</FONT>>]',
    )
    assert len(_drawn_edges(double_substituted)) == 2


def test_concentrate_html_label_identity_uses_colorscheme():
    """Scheme-relative HTML-like label colors resolve at emit time."""

    _assert_concentrated_edge_counts(
        "splines=ortho",
        (
            _edge_count_case(
                2,
                'a -> b [colorscheme=accent3 label=<<FONT COLOR="1">x</FONT>>]',
                'a -> b [colorscheme=paired3 label=<<FONT COLOR="1">x</FONT>>]',
            ),
            _edge_count_case(
                2,
                'c -> d [colorscheme=accent3 label=<<font color="1">x</font>>]',
                'c -> d [colorscheme=paired3 label=<<font color="1">x</font>>]',
            ),
            _edge_count_case(
                2,
                "g -> h [colorscheme=accent3 label=<<FONT COLOR='1'>x</FONT>>]",
                "g -> h [colorscheme=paired3 label=<<FONT COLOR='1'>x</FONT>>]",
            ),
            _edge_count_case(
                1,
                'e -> f [colorscheme=accent3 label=<<FONT COLOR="red">x</FONT>>]',
                'e -> f [colorscheme=paired3 label=<<FONT COLOR="red">x</FONT>>]',
            ),
        ),
    )


def test_concentrate_html_font_color_identity_uses_rendered_color():
    """Parsed HTML font colors compare by rendered color, not spelling."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [headlabel=<<FONT COLOR="red">x</FONT>>]',
                'a -> b [headlabel=<<FONT COLOR="#ff0000">x</FONT>>]',
            ),
        ),
    )


def test_concentrate_html_table_bgcolor_identity_uses_rendered_color():
    """Parsed HTML cell backgrounds compare by rendered color."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'c -> d [headlabel=<<TABLE><TR><TD BGCOLOR="red">x</TD></TR></TABLE>>]',
                'c -> d [headlabel=<<TABLE><TR><TD BGCOLOR="#ff0000">x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_label_bgcolor_identity_uses_colorscheme():
    """Scheme-relative HTML-like label backgrounds resolve at emit time."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [colorscheme=accent3 label=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
                'a -> b [colorscheme=paired3 label=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'c -> d [colorscheme=accent3 headlabel=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
                'd -> c [colorscheme=paired3 taillabel=<<TABLE><TR><TD BGCOLOR="1">x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_table_identity_uses_parsed_tree():
    """make_html_label() replaces table text, so identity uses the parse tree."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                "a -> b [label=<<TABLE><TR><TD>one</TD></TR></TABLE>>]",
                "a -> b [label=<<TABLE><TR><TD>two</TD></TR></TABLE>>]",
            ),
            _edge_count_case(
                2,
                "b -> c [label=<<TABLE><TR><TD>one</TD><TD>two</TD></TR></TABLE>>]",
                "b -> c [label=<<TABLE><TR><TD>one</TD></TR><TR><TD>two</TD></TR></TABLE>>]",
            ),
            _edge_count_case(
                2,
                "c -> d [label=<<TABLE><TR><TD ROWSPAN=\"2\">one</TD><TD>two</TD></TR><TR><TD>three</TD></TR></TABLE>>]",
                "c -> d [label=<<TABLE><TR><TD>one</TD><TD>two</TD></TR><TR><TD>three</TD></TR></TABLE>>]",
            ),
        ),
    )


def test_concentrate_html_table_identity_uses_layout_fields():
    """Table layout attributes change rendered HTML label geometry."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [label=<<TABLE CELLPADDING="2"><TR><TD>x</TD></TR></TABLE>>]',
                'a -> b [label=<<TABLE CELLPADDING="10"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'b -> c [label=<<TABLE CELLSPACING="2"><TR><TD>x</TD></TR></TABLE>>]',
                'b -> c [label=<<TABLE CELLSPACING="10"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'c -> d [label=<<TABLE WIDTH="20"><TR><TD>x</TD></TR></TABLE>>]',
                'c -> d [label=<<TABLE WIDTH="60"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'd -> e [label=<<TABLE FIXEDSIZE="false"><TR><TD>x</TD></TR></TABLE>>]',
                'd -> e [label=<<TABLE FIXEDSIZE="true" WIDTH="60" HEIGHT="40"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'e -> f [label=<<TABLE ALIGN="LEFT"><TR><TD>x</TD></TR></TABLE>>]',
                'e -> f [label=<<TABLE ALIGN="RIGHT"><TR><TD>x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                2,
                'g -> h [headlabel=<<TABLE BGCOLOR="red:blue" GRADIENTANGLE="0"><TR><TD>x</TD></TR></TABLE>>]',
                'h -> g [taillabel=<<TABLE BGCOLOR="red:blue" GRADIENTANGLE="90"><TR><TD>x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_br_align_identity_uses_span_justification():
    """BR ALIGN changes multiline text placement."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [label=<left<BR ALIGN="LEFT"/>x>]',
                'a -> b [label=<left<BR ALIGN="RIGHT"/>x>]',
            ),
        ),
    )


def test_concentrate_html_table_border_identity_uses_pencolor():
    """HTML table borders inherit edge pencolor before color."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                "a -> b [pencolor=red label=<<TABLE><TR><TD>x</TD></TR></TABLE>>]",
                "a -> b [pencolor=blue label=<<TABLE><TR><TD>x</TD></TR></TABLE>>]",
            ),
            _edge_count_case(
                2,
                'c -> d [colorscheme=accent3 pencolor=1 headlabel=<<TABLE><TR><TD>x</TD></TR></TABLE>>]',
                'd -> c [colorscheme=paired3 pencolor=1 taillabel=<<TABLE><TR><TD>x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_table_identity_uses_rendered_border_and_gradient():
    """Borderless side flags and equivalent gradient colors do not render."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [headlabel=<<TABLE BORDER="0"><TR><TD SIDES="L">x</TD></TR></TABLE>>]',
                'a -> b [headlabel=<<TABLE BORDER="0"><TR><TD SIDES="R">x</TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                1,
                'b -> c [headlabel=<<TABLE><TR><TD BGCOLOR="red:blue">x</TD></TR></TABLE>>]',
                'b -> c [headlabel=<<TABLE><TR><TD BGCOLOR="#ff0000:#0000ff">x</TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_html_img_identity_uses_effective_imagescale():
    """HTML IMG without SCALE uses the edge imagescale at emit time."""

    image = Path(__file__).parent / "../cmd/gvedit/images/save.png"
    assert image.exists(), "missing test data"
    source = _concentrated_graph(
        "",
        f'a -> b [imagescale=true label=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
        f'a -> b [imagescale=false label=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
    )
    assert len(_drawn_edges(source)) == 2


def test_concentrate_html_img_scale_identity_uses_rendered_mode():
    """HTML IMG scale spellings compare by rendered mode."""

    image = Path(__file__).parent / "../cmd/gvedit/images/save.png"
    assert image.exists(), "missing test data"
    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                f'a -> b [headlabel=<<TABLE><TR><TD><IMG SCALE="TRUE" SRC="{image}"/></TD></TR></TABLE>>]',
                f'a -> b [headlabel=<<TABLE><TR><TD><IMG SCALE="true" SRC="{image}"/></TD></TR></TABLE>>]',
            ),
            _edge_count_case(
                1,
                f'c -> d [imagescale=TRUE headlabel=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
                f'c -> d [imagescale=true headlabel=<<TABLE><TR><TD><IMG SRC="{image}"/></TD></TR></TABLE>>]',
            ),
        ),
    )


def test_concentrate_self_contained_html_label_ignores_outer_font_slots():
    """Fully specified HTML text does not emit label-level font attributes."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                1,
                'a -> b [fontname=Courier fontsize=10 fontcolor=blue headlabel=<<FONT FACE="Helvetica" POINT-SIZE="12" COLOR="red">x</FONT>>]',
                'a -> b [fontname=Times fontsize=20 fontcolor=green headlabel=<<FONT FACE="Helvetica" POINT-SIZE="12" COLOR="red">x</FONT>>]',
            ),
        ),
    )


def test_concentrate_html_label_numeric_color_without_colorscheme_does_not_crash():
    """Missing colorscheme defaults must not dereference NULL in identity slots."""

    source = """
        digraph {
          graph [concentrate=true splines=ortho]
          a -> b [label=<<FONT COLOR="1">x</FONT>>]
          a -> b [label=<<FONT COLOR="1">x</FONT>>]
        }
    """
    assert len(_drawn_edges(source)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_singleton_sameport_groups_do_not_affect_identity(splines: str):
    """dot_sameports() moves samehead/sametail ports only with >1 members."""

    _assert_concentrated_edge_counts(
        splines,
        (
            _edge_count_case(1, "a -> b [samehead=x]", "a -> b"),
            _edge_count_case(1, "b -> c [sametail=x]", "b -> c"),
            _edge_count_case(1, "c -> d [samehead=x]", "d -> c [sametail=x]"),
        ),
    )


def test_tapered_multicolor_arrow_fillcolor_is_renderable():
    """emit_edge_graphics() can render a tapered color-list arrow fill."""

    tapered_multicolor = """
        digraph {
          a -> b [style=tapered color="red:blue" fillcolor=green]
        }
    """
    drawn_edges = _drawn_edges(tapered_multicolor)
    assert len(drawn_edges) == 1
    assert "_hdraw_" in drawn_edges[0]


def test_concentrate_arrow_fillcolor_identity_resolves_color_list_first_color():
    """Explicit arrow fillcolor color-lists render through the active colorscheme."""

    _assert_concentrated_edge_counts(
        "",
        (
            _edge_count_case(
                2,
                'a -> b [colorscheme=accent3 fillcolor="1:2"]',
                'a -> b [colorscheme=paired3 fillcolor="1:2"]',
            ),
        ),
    )


def test_concentrate_retained_arrow_fillcolor_is_resolved_before_emit():
    """A retained arrow record must not emit a raw color-list fillcolor."""

    source = _concentrated_graph(
        "",
        'a -> b [fillcolor="red:blue"]',
        'a -> b [fillcolor="red:blue"]',
    )
    drawn_edges = _drawn_edges(source)
    assert len(drawn_edges) == 1
    assert _arrow_fill_color(drawn_edges[0], "h") == "#ff0000"


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_colors(splines: str):
    """Concentration preserves the colors and routes of visibly distinct edges."""

    distinct_parallel_colors = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    assert set(_drawn_edge_colors(distinct_parallel_colors)) == {
        "#ff0000",
        "#0000ff",
    }
    _assert_distinct_drawn_edge_routes(distinct_parallel_colors, 2)

    distinct_opposite_colors = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "b -> a [color=blue]",
    )
    assert set(_drawn_edge_colors(distinct_opposite_colors)) == {
        "#ff0000",
        "#0000ff",
    }
    _assert_distinct_drawn_edge_routes(distinct_opposite_colors, 2)


_SHARED_TRUNK_FIXTURE = """
    digraph {
      graph [concentrate=true]
      { rank=min; a; b }
      a -> c
      c -> e
      e -> d
      %s
    }
"""


_SHARED_TRUNK_DISTINCT_ORDERS = (
    pytest.param(
        (
            "a -> d [color=blue]",
            "b -> d [color=red]",
        ),
        id="blue-a-red-b",
    ),
    pytest.param(
        (
            "b -> d [color=red]",
            "a -> d [color=blue]",
        ),
        id="red-b-blue-a",
    ),
)


def _shared_trunk_source(*edges: str) -> str:
    return _SHARED_TRUNK_FIXTURE % "\n".join(f"        {edge}" for edge in edges)


_BACKWARD_PARALLEL_COLORS_FIXTURE = """
    digraph {
      graph [concentrate=true, ranksep=1.2]
      node [shape=circle, width=0.45, fixedsize=true]
      edge [arrowsize=0.8, penwidth=3]
      b -> c [style=invis]
      c -> a [style=invis]
      a -> b [constraint=false, color=red]
      a -> b [constraint=false, color=blue]
      a -> b [constraint=false, color=red]
    }
"""


@pytest.mark.parametrize("edge_order", _SHARED_TRUNK_DISTINCT_ORDERS)
def test_concentrate_shared_trunk_keeps_distinct_colored_routes(
    edge_order: tuple[str, ...],
):
    """dot_concentrate() keeps each distinct tail-to-head route complete."""

    source = _shared_trunk_source(*edge_order)
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    assert len(edges) == 2
    assert {_drawn_edge_color(edge) for edge in edges} == {"#0000ff", "#ff0000"}
    assert all("_hdraw_" in edge for edge in edges)


def test_concentrate_shared_trunk_keeps_visible_lanes_for_distinct_edges():
    """Rendered-distinct members on a shared corridor separate visibly."""

    source = _shared_trunk_source(
        "a -> d [color=blue]",
        "b -> d [color=red]",
        "a -> d",
        "b -> d",
    )
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    colored = [
        edge
        for edge in edges
        if _drawn_edge_color(edge) in {"#0000ff", "#ff0000"}
    ]
    assert {_drawn_edge_color(edge) for edge in colored} == {
        "#0000ff",
        "#ff0000",
    }
    stroke_width = max(float(edge.get("penwidth", 1)) for edge in colored)
    assert _max_pointwise_route_distance(*colored) > stroke_width


def test_concentrate_shared_trunk_routes_meet_at_junction():
    """Concentrated route pieces meet at their shared virtual node."""

    source = _shared_trunk_source("a -> d", "b -> d")
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    short_edge = min(edges, key=_drawn_edge_spline_point_count)
    long_edge = max(edges, key=_drawn_edge_spline_point_count)
    short_bezier = next(
        operation for operation in short_edge["_draw_"] if operation["op"] == "b"
    )
    long_beziers = [
        operation for operation in long_edge["_draw_"] if operation["op"] == "b"
    ]

    assert len(long_beziers) == 2
    _assert_regular_g1_piece_join(long_beziers[0]["points"], long_beziers[1]["points"])
    assert math.dist(short_bezier["points"][-1], long_beziers[1]["points"][0]) <= 0.01


def test_concentrate_shared_trunk_repair_is_edge_order_deterministic():
    """Tagged-junction controls are independent of input edge iteration order."""

    first = _shared_trunk_source("a -> d", "b -> d")
    reversed_order = _shared_trunk_source("b -> d", "a -> d")
    assert _named_drawn_cubic_geometry(first, {"a", "b"}, "d") == (
        _named_drawn_cubic_geometry(reversed_order, {"a", "b"}, "d")
    )


def test_concentrate_trunk_alignment_preserves_better_piece_g1():
    """A topology-neutral trunk adjustment cannot worsen an existing G1 seam."""

    source = _concentrated_graph(
        "",
        'Diskless -> Inconsistent [label="ioctl_set_disk()"]',
        'Diskless -> Consistent [label="ioctl_set_disk()"]',
        'Diskless -> Outdated [label="ioctl_set_disk()"]',
        'Consistent -> Outdated [label="receive_param()"]',
        'Consistent -> UpToDate [label="receive_param()"]',
        'Consistent -> Inconsistent [label="start resync"]',
        'Outdated -> Inconsistent [label="start resync"]',
        'UpToDate -> Inconsistent [label="ioctl_replicate"]',
        'Inconsistent -> UpToDate [label="resync completed"]',
        'Consistent -> Failed [label="io completion error"]',
        'Outdated -> Failed [label="io completion error"]',
        'UpToDate -> Failed [label="io completion error"]',
        'Inconsistent -> Failed [label="io completion error"]',
        'Failed -> Diskless [label="sending notify to peer"]',
    )
    edge = _drawn_edges_between(source, {"Outdated"}, "Failed")[0]
    pieces = [
        operation["points"] for operation in edge["_draw_"] if operation["op"] == "b"
    ]
    assert len(pieces) == 2
    _assert_regular_g1_piece_join(pieces[0], pieces[1])


def test_concentrate_shared_trunk_still_merges_without_colored_siblings():
    """dot_concentrate() retains the ordinary asymmetric shared-trunk route."""

    source = _shared_trunk_source("a -> d", "b -> d")
    edges = _drawn_edges_between(source, {"a", "b"}, "d")
    counts = sorted(_drawn_edge_spline_point_count(edge) for edge in edges)
    assert counts[0] == 4
    assert counts[1] >= 8
    assert sum("_hdraw_" in edge for edge in edges) == 1


def test_concentrate_same_tail_fanout_routes_share_initial_trunk():
    """Same-endpoint fan-out gathers before splitting to distinct heads."""

    source = _graph_with_concentrate(
        True,
        "a -> b [minlen=2]",
        "a -> c [minlen=2]",
    )
    edges = _drawn_edges(source)
    assert len(edges) == 2
    first_segments = [
        operation["points"]
        for operation in edges[0]["_draw_"]
        if operation["op"] == "b"
    ]
    second_segments = [
        operation["points"]
        for operation in edges[1]["_draw_"]
        if operation["op"] == "b"
    ]
    assert len(first_segments) == 2
    assert len(second_segments) == 1
    _assert_regular_g1_piece_join(first_segments[0], first_segments[1])
    assert math.dist(first_segments[0][-1], second_segments[0][0]) <= 1.01


def test_concentrate_short_bidirectional_flat_edge_bows_to_three_arrows():
    """A short folded same-rank reverse edge has a visible middle shaft."""

    source = """
        strict digraph {
          graph [concentrate=true]
          { rank=same; a; b }
          a -> b
          b -> a
        }
    """
    drawn_edges = _drawn_edges(source)
    assert len(drawn_edges) == 1
    edge = drawn_edges[0]
    assert "_hdraw_" in edge
    assert "_tdraw_" in edge

    assert _drawn_edge_arc_length(edge) >= 30


def test_concentrate_multiedge_arrowheads_follow_shaft_tangents():
    """Arrow axes follow the terminal control arms of concentrated multiedges."""

    drawn_edges = _drawn_edges(_BACKWARD_PARALLEL_COLORS_FIXTURE)
    assert len(drawn_edges) == 2
    assert max(_arrowhead_shaft_angle(edge) for edge in drawn_edges) <= 0.1


def test_concentrate_single_route_arrowhead_follows_smoothed_tangent():
    """Post-route smoothing cannot leave a single route's arrow axis stale."""

    source = """
        digraph {
          graph [concentrate=true]
          subgraph cluster_a {
            n49 -> n53 [label=int]
            n55
          }
          subgraph cluster_b {
            n50 -> n61
            n49 -> n61
          }
          n55 -> n57 [label=exe, dir=back]
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "n55", "n57")

    assert _arrowhead_shaft_angle(edge, "_tdraw_") <= 2


def test_concentrate_smoothing_preserves_terminal_departure():
    """Interior smoothing does not turn a clipped terminal arm inward."""

    source = """
        digraph {
          graph [concentrate=true]
          node [shape=doublecircle]
          running
          lost
          node [shape=circle]
          { rank=min; running_rta [label="running;\\nreconnect\\ntimer\\nactive"] }
          running [label="running;\\nreconnect\\ntimer\\nstopped"]
          blocked
          failfast [label="fail I/O\\nfast"]
          running -> running_rta [label="fast_io_fail_tmo = off and\\ndev_loss_tmo = off;\\nsrp_start_tl_fail_timers()"]
          running_rta -> running [label="fast_io_fail_tmo = off and\\ndev_loss_tmo = off;\\nreconnecting succeeded"]
          running -> blocked [label="fast_io_fail_tmo >= 0 or\\ndev_loss_tmo >= 0;\\nsrp_start_tl_fail_timers()"]
          blocked -> failfast [label="fast_io_fail_tmo\\nexpired or\\nreconnecting\\nfailed"]
          blocked -> lost [label="dev_loss_tmo\\nexpired or\\nsrp_stop_rport_timers()"]
          failfast -> lost [label="dev_loss_tmo\\nexpired or\\nsrp_stop_rport_timers()"]
          failfast -> failfast [label="reconnecting\\nfailed"]
          running -> lost [label="srp_stop_rport_timers()"]
          running_rta -> lost [label="srp_stop_rport_timers()"]
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "running_rta", "lost")

    _assert_endpoint_departure(layout, edge, "tail")


def test_concentrate_multiedge_routes_clear_non_endpoint_nodes():
    """Separated route strokes remain outside every non-endpoint node."""

    layout = json.loads(dot("json", source=_BACKWARD_PARALLEL_COLORS_FIXTURE))
    nodes = {node["_gvid"]: node for node in layout["objects"]}
    for edge in (edge for edge in layout["edges"] if "_draw_" in edge):
        endpoints = {edge["tail"], edge["head"]}
        penwidth = float(edge.get("penwidth", 1))
        sampled_points = (
            point
            for operation in edge["_draw_"]
            if operation["op"] == "b"
            for point in _sample_bezier_points(operation["points"])
        )
        points = tuple(sampled_points)
        for node_id, node in nodes.items():
            if node_id in endpoints:
                continue
            ellipse = next(
                operation["rect"]
                for operation in node["_draw_"]
                if operation["op"] == "e"
            )
            center_x, center_y, radius_x, radius_y = ellipse
            assert radius_x == pytest.approx(radius_y)
            clearance = min(
                math.hypot(x - center_x, y - center_y) - radius_x - penwidth / 2
                for x, y in points
            )
            assert clearance >= 0.5, (edge["color"], node["name"], clearance)


def test_concentrate_same_rank_opposite_vertical_ports_use_clear_ortho_route():
    """The same-rank vertical-port detour must not cut through neighbor nodes."""

    source = """
        strict digraph {
          graph [concentrate=true, splines=ortho, nodesep=0.05]
          node [shape=circle, width=0.45, fixedsize=true]
          { rank=same; x; a; b }
          a:s -> b:n [penwidth=3]
          b:n -> a:s [penwidth=3]
        }
    """
    layout = json.loads(dot("json", source=source))
    neighbor = next(node for node in layout["objects"] if node["name"] == "x")
    ellipse = next(
        operation["rect"] for operation in neighbor["_draw_"] if operation["op"] == "e"
    )
    center_x, center_y, radius_x, radius_y = ellipse
    assert radius_x == pytest.approx(radius_y)
    edge = next(edge for edge in layout["edges"] if "_draw_" in edge)
    penwidth = float(edge.get("penwidth", 1))
    points = tuple(
        point
        for operation in edge["_draw_"]
        if operation["op"] == "b"
        for point in _sample_bezier_points(operation["points"])
    )
    clearance = min(
        math.hypot(x - center_x, y - center_y) - radius_x - penwidth / 2
        for x, y in points
    )
    assert clearance >= 0.5


def test_unconcentrated_single_edge_arrowhead_angle_is_unchanged():
    """Single-edge routing retains its straight arrowhead alignment."""

    edge = _drawn_edges("digraph { a -> b }")[0]
    assert _arrowhead_shaft_angle(edge) == pytest.approx(0.0, abs=0.01)


def test_vee_arrowhead_polygon_does_not_cross_itself_at_wide_penwidth():
    """Penwidth compensation must keep the two vee prongs disjoint."""

    edge = _drawn_edges(
        "digraph { edge [penwidth=3, arrowhead=vee]; a -> b }"
    )[0]
    polygon = next(
        operation["points"]
        for operation in edge["_hdraw_"]
        if operation["op"] == "P"
    )
    assert len(polygon) == 8
    assert _polygon_self_intersections(polygon) == []


def test_concentrate_edge_helpers_are_exported_to_windows_plugins():
    """edge_chains.c, conc.c, and ortho.c can import edge identity helpers."""

    header = (Path(__file__).parent.parent / "lib/common/edgeattr.h").read_text(
        encoding="utf-8"
    )
    assert "#define EDGEATTR_API __declspec(dllexport)" in header
    assert "#define EDGEATTR_API __declspec(dllimport)" in header
    for helper in (
        "gv_edge_attributes_are_equal",
        "gv_opposite_edge_attributes_are_equal",
        "gv_edge_ports_are_equal",
        "gv_opposite_edge_ports_are_equal",
    ):
        assert re.search(rf"EDGEATTR_API bool {helper}\s*\(", header)


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_styles(splines: str):
    """Concentration preserves visibly distinct line styles."""

    distinct_styles = _concentrated_graph(
        splines,
        "a -> b [style=dashed]",
        "a -> b [style=dotted]",
    )
    assert set(_drawn_edge_styles(distinct_styles)) == {"dashed", "dotted"}


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_labels(splines: str):
    """Concentration preserves visibly distinct edge labels."""

    distinct_labels = _concentrated_graph(
        splines,
        "a -> b [label=first]",
        "a -> b [label=second]",
    )
    label_edges = json.loads(dot("json", source=distinct_labels))["edges"]
    assert {edge["label"] for edge in label_edges} == {"first", "second"}


def test_concentrate_curved_route_reuse_merges_identical_xlabels_once():
    """EDGETYPE_CURVED route reuse keeps one visible label for duplicates."""

    identical_xlabels = _concentrated_graph(
        "splines=curved",
        "a -> b [minlen=3 xlabel=same]",
        "a -> b [minlen=3 xlabel=same]",
    )
    assert len(_drawn_edges(identical_xlabels)) == 1
    assert _edge_label_texts(identical_xlabels) == ["same"]


def test_concentrate_curved_route_reuse_preserves_distinct_xlabels():
    """EDGETYPE_CURVED route reuse splits visibly distinct labels."""

    distinct_xlabels = _concentrated_graph(
        "splines=curved",
        "a -> b [minlen=3 xlabel=first]",
        "a -> b [minlen=3 xlabel=second]",
    )
    assert len(_drawn_edges(distinct_xlabels)) == 2
    assert sorted(_edge_label_texts(distinct_xlabels)) == ["first", "second"]


def test_concentrate_ortho_suppression_merges_identical_labels_once():
    """Ortho suppression keeps one route while preserving duplicate labels."""

    identical_labels = _concentrated_graph(
        "splines=ortho",
        "a -> b [label=same]",
        "a -> b [label=same]",
    )
    assert len(_drawn_edges(identical_labels)) == 1
    assert _edge_label_texts(identical_labels) == ["same", "same"]


def test_concentrate_ortho_suppression_preserves_distinct_labels():
    """Ortho suppression splits and draws visibly distinct labels."""

    distinct_labels = _concentrated_graph(
        "splines=ortho",
        "a -> b [label=first]",
        "a -> b [label=second]",
    )
    assert len(_drawn_edges(distinct_labels)) == 2
    assert sorted(_edge_label_texts(distinct_labels)) == ["first", "second"]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_edge_directions(splines: str):
    """Concentration preserves visibly distinct arrow directions."""

    distinct_directions = _concentrated_graph(
        splines,
        "a -> b [dir=forward]",
        "a -> b [dir=both]",
    )
    direction_edges = json.loads(dot("json", source=distinct_directions))["edges"]
    assert {
        (bool(edge.get("_tdraw_")), bool(edge.get("_hdraw_")))
        for edge in direction_edges
    } == {(False, True), (True, True)}


def test_concentrate_svg_preserves_distinct_edge_class_hooks():
    """svg_print_id_class() keeps each rendered edge CSS/DOM class hook."""

    def edge_classes(*attributes: str) -> list[str]:
        source = _concentrated_graph(
            "",
            *(
                f"a -> b [{attribute}]" if attribute else "a -> b"
                for attribute in attributes
            ),
        )
        root = ET.fromstring(dot("svg", source=source))
        return [
            element.attrib["class"]
            for element in root.iter()
            if element.tag.endswith("g")
            and "edge" in element.attrib.get("class", "").split()
        ]

    assert set(edge_classes("class=first", "class=second")) == {
        "edge first",
        "edge second",
    }
    assert edge_classes("class=shared", "class=shared") == ["edge shared"]
    assert edge_classes("", "") == ["edge"]


def test_concentrate_preserves_explicit_endpoint_tooltips_without_labels():
    """nodeIntersect() maps explicit endpoint tooltips without label geometry."""

    distinct_head_tooltips = _concentrated_graph(
        "",
        "a -> b [headtooltip=one]",
        "a -> b [headtooltip=two]",
    )
    assert len(_drawn_edges(distinct_head_tooltips)) == 2

    equal_head_tooltips = _concentrated_graph(
        "",
        "a -> b [headtooltip=same]",
        "a -> b [headtooltip=same]",
    )
    assert len(_drawn_edges(equal_head_tooltips)) == 1

    # emit_edge_label() still needs a main label before labeltooltip renders.
    unrendered_label_tooltips = _concentrated_graph(
        "",
        "a -> b [labeltooltip=one]",
        "a -> b [labeltooltip=two]",
    )
    assert len(_drawn_edges(unrendered_label_tooltips)) == 1

    rendered_label_tooltips = _concentrated_graph(
        "",
        "a -> b [label=x labeltooltip=one]",
        "a -> b [label=x labeltooltip=two]",
    )
    assert len(_drawn_edges(rendered_label_tooltips)) == 2


def test_concentrate_preserves_taper_direction_without_arrowheads():
    """taperfun() uses dir even when both arrow decorations are absent."""

    distinct_tapers = _concentrated_graph(
        "",
        "a -> b [style=tapered dir=none]",
        "a -> b [style=tapered dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(distinct_tapers)) == 2

    equal_tapers = _concentrated_graph(
        "",
        "a -> b [style=tapered dir=forward arrowhead=none]",
        "a -> b [style=tapered dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(equal_tapers)) == 1

    default_forward_tapers = _concentrated_graph(
        "",
        "a -> b [style=tapered arrowhead=none]",
        "a -> b [style=tapered dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(default_forward_tapers)) == 1

    arrowless_lines = _concentrated_graph(
        "",
        "a -> b [dir=none]",
        "a -> b [dir=forward arrowhead=none]",
    )
    assert len(_drawn_edges(arrowless_lines)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_merges_equivalent_parallel_edges(splines: str):
    """Equivalent edges share one route even when input order separates them."""

    adjacent_equivalent_edges = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=red]",
    )
    assert _drawn_edge_colors(adjacent_equivalent_edges) == ["#ff0000"]

    interleaved_equivalent_edges = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
    )
    assert sorted(_drawn_edge_colors(interleaved_equivalent_edges)) == [
        "#0000ff",
        "#ff0000",
    ]

    # The invisible path forces a->b to run against rank order. This exercises
    # the backward-edge classifier, which must apply the same equivalence rule.
    backward_interleaved_equivalent_edges = _concentrated_graph(
        splines,
        "b -> c [style=invis]",
        "c -> a [style=invis]",
        "a -> b [constraint=false color=red]",
        "a -> b [constraint=false color=blue]",
        "a -> b [constraint=false color=red]",
    )
    assert sorted(_drawn_edge_colors(backward_interleaved_equivalent_edges)) == [
        "#0000ff",
        "#ff0000",
    ]

    explicit_default_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=normal]",
        "a -> b",
        "a -> b [arrowtail=vee]",
    )
    assert len(_drawn_edges(explicit_default_arrows)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_equivalent_color_spellings(splines: str):
    """Color values compare by rendered color when both parse cleanly."""

    equivalent_color_spellings = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        'a -> b [color="#ff0000"]',
    )
    assert _drawn_edge_colors(equivalent_color_spellings) == ["#ff0000"]

    fillcolor_defaults_to_color = _concentrated_graph(
        splines,
        "a -> b [color=red]",
        "a -> b [color=red fillcolor=red]",
    )
    assert _drawn_edge_colors(fillcolor_defaults_to_color) == ["#ff0000"]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_non_segmented_color_lists_preserve_empty_lanes(splines: str):
    """Empty color-list lanes shift parallel strokes, so they stay distinct.

    emit_edge_graphics() counts raw colons into numc and offsets the
    parallel strokes, so color="red:" renders shifted relative to
    color=red; the identity must keep them apart.  ":red" and "red:"
    share the same lane count and may merge with each other."""

    empty_lane_spellings = _concentrated_graph(
        splines,
        'a -> b [dir=none color=":red"]',
        'a -> b [dir=none color="red:"]',
        "a -> b [dir=none color=red]",
    )
    assert _drawn_edge_colors(empty_lane_spellings) == ["#ff0000", "#ff0000"]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_arrow_fillcolor_only_gates_filled_shapes(splines: str):
    """arrow_gen() consumes fillcolor only for shapes that render filled marks."""

    unfilled_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=onormal fillcolor=red]",
        "a -> b [arrowhead=onormal fillcolor=blue]",
        "b -> c [arrowhead=curve fillcolor=red]",
        "b -> c [arrowhead=curve fillcolor=blue]",
    )
    assert len(_drawn_edges(unfilled_arrows)) == 2

    filled_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=normal fillcolor=red]",
        "a -> b [arrowhead=normal fillcolor=blue]",
        "b -> c [arrowhead=tee fillcolor=red]",
        "b -> c [arrowhead=tee fillcolor=blue]",
    )
    assert len(_drawn_edges(filled_arrows)) == 4


@pytest.mark.parametrize(
    ("attribute", "first_value", "second_value"),
    (
        ("labelangle", "10", "20"),
        ("labeldistance", "1", "2"),
        ("labelfontcolor", "red", "blue"),
        ("labelfontname", "Helvetica", "Courier"),
        ("labelfontsize", "10", "20"),
    ),
)
def test_concentrate_gates_endpoint_label_attributes(
    attribute: str, first_value: str, second_value: str
):
    """place_portlabel()/initFontLabelEdgeAttr() consume endpoint-label rows."""

    main_label_only = _concentrated_graph(
        "splines=ortho",
        f"a -> b [xlabel=x {attribute}={first_value}]",
        f"a -> b [xlabel=x {attribute}={second_value}]",
    )
    assert len(_drawn_edges(main_label_only)) == 1

    same_endpoint_attribute = _concentrated_graph(
        "splines=ortho",
        f"a -> b [headlabel=x {attribute}={first_value}]",
        f"a -> b [headlabel=x {attribute}={first_value}]",
    )
    assert len(_drawn_edges(same_endpoint_attribute)) == 1

    endpoint_label = _concentrated_graph(
        "splines=ortho",
        f"a -> b [headlabel=x {attribute}={first_value}]",
        f"a -> b [headlabel=x {attribute}={second_value}]",
    )
    assert len(_drawn_edges(endpoint_label)) == 2


@pytest.mark.parametrize(
    ("attribute", "default_value"),
    (("labeldistance", "1"),),
)
def test_concentrate_preserves_endpoint_label_placement_attribute_presence(
    attribute: str, default_value: str
):
    """place_portlabel() uses late_double() defaults for explicit placement."""

    placement_trigger = _concentrated_graph(
        "splines=ortho",
        "a -> b [headlabel=x]",
        f"a -> b [headlabel=x {attribute}={default_value}]",
    )
    assert len(_drawn_edges(placement_trigger)) == 1

    repeated_explicit_default = _concentrated_graph(
        "splines=ortho",
        f"a -> b [headlabel=x {attribute}={default_value}]",
        f"a -> b [headlabel=x {attribute}={default_value}]",
    )
    assert len(_drawn_edges(repeated_explicit_default)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_resolved_port_spellings(splines: str):
    """Raw headport/tailport spelling does not override resolved ports."""

    equivalent_port_spellings = _concentrated_graph(
        splines,
        "node [shape=record]",
        'a [label="<p>p"]',
        "a:p:c -> b [color=red]",
        'a -> b [tailport="p:c" color=red]',
    )
    assert len(_drawn_edges(equivalent_port_spellings)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_rank_parallel_edges_find_prior_equivalent(splines: str):
    """Same-rank duplicates still concentrate when separated by distinct edges."""

    separated_flat_duplicates = _concentrated_graph(
        splines,
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
    )
    assert sorted(_drawn_edge_colors(separated_flat_duplicates)) == [
        "#0000ff",
        "#ff0000",
    ]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_backward_edges_find_prior_equivalent(splines: str):
    """Backward edges still suppress earlier same-direction duplicates."""

    separated_backward_duplicates = _concentrated_graph(
        splines,
        "b",
        "a -> b [color=blue]",
        "b -> a [constraint=false color=red]",
        "b -> a [constraint=false color=red]",
    )
    assert sorted(_drawn_edge_colors(separated_backward_duplicates)) == [
        "#0000ff",
        "#ff0000",
    ]


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_edge_arrow_shapes(splines: str):
    """Each retained edge borrows arrows from its exact reverse mate."""

    opposite_equivalent_pairs = _concentrated_graph(
        splines,
        "a -> b [color=red arrowhead=normal]",
        "a -> b [color=blue arrowhead=vee]",
        "b -> a [color=red arrowhead=normal]",
        "b -> a [color=blue arrowhead=vee]",
    )
    drawn_edges_by_color = _drawn_edges_by_color(opposite_equivalent_pairs)
    # JSON names the head and tail arrow streams `_hdraw_` and `_tdraw_`.
    head_endpoint = "h"
    tail_endpoint = "t"
    actual_arrow_polygon_points = {
        color: (
            _arrow_polygon_point_count(edge, head_endpoint),
            _arrow_polygon_point_count(edge, tail_endpoint),
        )
        for color, edge in drawn_edges_by_color.items()
    }
    expected_arrow_polygon_points = {"#ff0000": (3, 3), "#0000ff": (8, 8)}
    assert actual_arrow_polygon_points == expected_arrow_polygon_points


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_reverse_arrow_after_no_arrow_duplicate(splines: str):
    """A later reverse duplicate without arrows does not erase saved arrows."""

    reverse_arrow_then_no_arrow = _concentrated_graph(
        splines,
        "a -> b [dir=none]",
        "b -> a [arrowhead=normal]",
        "b -> a [dir=none]",
    )
    drawn_edges = _drawn_edges(reverse_arrow_then_no_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert "_hdraw_" not in drawn_edges[0]
    assert _arrow_polygon_point_count(drawn_edges[0], "t") == 3


def test_neato_concentrate_straight_opposite_edge_keeps_borrowed_arrow():
    """Straight neato folds compare opposite physical endpoints."""

    source = """
        digraph {
          graph [concentrate=true splines=false]
          node [shape=circle]
          a [pos="0,0!"]
          b [pos="1,0!"]
          a -> b [dir=none color=red]
          b -> a [arrowhead=normal color=red]
        }
    """
    layout = json.loads(run("dot", "-Kneato", "-Tjson", input=source))
    positioned_edges = [edge for edge in layout["edges"] if edge.get("pos")]
    assert len(positioned_edges) == 1
    assert "_tdraw_" in positioned_edges[0]
    assert "_hdraw_" not in positioned_edges[0]


@pytest.mark.parametrize(
    ("retained_attributes", "candidate_attributes"),
    (
        ("", "fillcolor=1"),
        ('color="#a6cee3"', "color=1"),
    ),
)
def test_concentrate_borrowed_arrow_uses_candidate_colorscheme(
    retained_attributes: str, candidate_attributes: str
):
    """emit_edge_graphics() receives the candidate-resolved borrowed fill."""

    borrowed_scheme_relative_arrow = _concentrated_graph(
        "",
        f"a -> b [dir=none colorscheme=accent3 {retained_attributes}]",
        f"b -> a [arrowhead=normal colorscheme=paired3 {candidate_attributes}]",
    )
    drawn_edges = _drawn_edges(borrowed_scheme_relative_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "t") == "#a6cee3"


def test_concentrate_borrowed_reverse_arrow_uses_endpoint_segment_color():
    """A borrowed color-list arrow keeps the candidate endpoint's segment color."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        'a -> b [dir=none color="red:blue"]',
        'b -> a [dir=forward color="blue:red"]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "t") == "#0000ff"


def test_concentrate_color_list_arrow_endpoint_overrides_explicit_fillcolor():
    """Non-tapered color-list arrows use their endpoint segment color."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        'a -> b [dir=none color="red:blue" fillcolor=green]',
        'b -> a [dir=forward color="blue:red" fillcolor=green]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_tdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "t") == "#0000ff"


def test_concentrate_color_list_arrow_endpoint_strips_segment_fractions():
    """Borrowed endpoint arrow colors resolve normalized color-list segments."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        'a -> b [dir=none color="red;0.5:blue;0.5"]',
        'b -> a [dir=back color="blue;0.5:red;0.5"]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_hdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "h") == "#ff0000"


def test_concentrate_color_list_arrow_endpoint_uses_normalized_segments():
    """Endpoint arrow colors follow the positive segments multicolor() draws."""

    borrowed_segment_arrow = _concentrated_graph(
        "",
        "a -> b [dir=none color=red]",
        'b -> a [dir=back color="red;1:blue"]',
    )
    drawn_edges = _drawn_edges(borrowed_segment_arrow)
    assert len(drawn_edges) == 1
    assert "_hdraw_" in drawn_edges[0]
    assert _arrow_fill_color(drawn_edges[0], "h") == "#ff0000"


def test_endpoint_label_default_position_uses_clearance_anchor():
    """Endpoint labels default from the endpoint anchor, not their lower-left box."""

    layout = json.loads(
        dot(
            "json",
            source='digraph { a -> b [headlabel="x"] }',
        )
    )
    edge = layout["edges"][0]
    node_by_id = {node["_gvid"]: node for node in layout["objects"]}
    node_center = tuple(
        float(coordinate) for coordinate in node_by_id[edge["head"]]["pos"].split(",")
    )
    endpoint = _edge_physical_endpoint(edge, "head")
    label_point = next(
        tuple(operation["pt"])
        for operation in edge["_hldraw_"]
        if operation["op"] == "T"
    )

    assert math.dist(label_point, endpoint) <= math.dist(node_center, endpoint)


def test_arrowless_endpoint_labels_follow_spline_direction():
    """Labels without arrow clip points still seed from nearby spline geometry."""

    for label_attribute, draw_stream, endpoint_name, comparison in (
        ("taillabel", "_tldraw_", "tail", operator.lt),
        ("headlabel", "_hldraw_", "head", operator.gt),
    ):
        layout = json.loads(
            dot(
                "json",
                source=f'digraph {{ a -> b [dir=none {label_attribute}="x"] }}',
            )
        )
        edge = layout["edges"][0]
        endpoint = _edge_physical_endpoint(edge, endpoint_name)
        label_point = next(
            tuple(operation["pt"])
            for operation in edge[draw_stream]
            if operation["op"] == "T"
        )
        assert comparison(label_point[1], endpoint[1])


def test_concentrate_endpoint_labels_keep_distinct_flat_routes():
    """Endpoint labels on distinct flat concentrated lanes stay attached."""

    source = """
        digraph {
          graph [concentrate=true nodesep=0.2 rankdir=LR ranksep=0.2]
          node [fontsize=10 height=0 width=0]
          edge [arrowsize=0.9 dir=none fontsize=8 labelangle=-30
                labeldistance=0.8 labelfontsize=8]

          n003 -> n002 [arrowhead=dot headlabel=":s:"]
          n003 -> n002 [arrowtail=inv samearrowhead=1 samehead=m000]
          n005 -> n002 [arrowhead=dot arrowtail=inv headlabel=":u:"
                        samearrowhead=1 samehead=m000]
        }
    """
    layout = json.loads(dot("json", source=source))

    assert _endpoint_label_detach_honda_score(layout) == (0, 0)


def _dynamic_graphviz_link() -> tuple[list[Union[str, Path]], tuple[Path, ...]]:
    core = _find_plugin_so("core")
    dot_layout = _find_plugin_so("dot_layout")
    if core is None or dot_layout is None:
        build_root = which("dot").resolve().parents[2]
        core = build_root / "plugin/core/libgvplugin_core.so"
        dot_layout = build_root / "plugin/dot_layout/libgvplugin_dot_layout.so"
        cgraph = build_root / "lib/cgraph/libcgraph.so"
        gvc = build_root / "lib/gvc/libgvc.so"
        for library in (core, dot_layout, cgraph, gvc):
            assert library.exists(), f"missing build library {library}"
        link = [cgraph, gvc, core, dot_layout]
        library_directories = (
            cgraph.parent,
            gvc.parent,
            core.parent,
            dot_layout.parent,
        )
    else:
        link = ["cgraph", "gvc", core, dot_layout]
        library_directories = (core.parent, dot_layout.parent)

    return link, library_directories


def _env_with_library_path(library_directories: tuple[Path, ...]) -> dict:
    env = os.environ.copy()
    library_paths = os.pathsep.join(str(path) for path in library_directories)
    loader_path = "DYLD_LIBRARY_PATH" if is_macos() else "LD_LIBRARY_PATH"
    env[loader_path] = os.pathsep.join(
        part for part in (library_paths, env.get(loader_path, "")) if part
    )
    return env


def _compile_concentrate_c_test(
    tmp_path: Path,
    source_name: str,
    exe_name: str,
    *,
    extra_includes: tuple[Path, ...] = (),
) -> tuple[Path, dict]:
    link, library_directories = _dynamic_graphviz_link()
    source_lib = Path(__file__).parent.parent / "lib"
    exe = tmp_path / exe_name
    compile_c(
        Path(__file__).parent / source_name,
        cflags=[
            *(f"-I{include}" for include in extra_includes),
            f"-I{source_lib}",
            f"-I{source_lib / 'cdt'}",
            f"-I{source_lib / 'cgraph'}",
            f"-I{source_lib / 'common'}",
            f"-I{source_lib / 'gvc'}",
            f"-I{source_lib / 'pathplan'}",
        ],
        link=link,
        dst=exe,
    )
    return exe, _env_with_library_path(library_directories)


def _compile_concentrate_edge_identity_tooltip_test(tmp_path: Path) -> tuple[Path, dict]:
    return _compile_concentrate_c_test(
        tmp_path,
        "concentrate_edge_identity_tooltip.c",
        "concentrate-edge-identity-tooltip",
    )


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_implicit_tooltip_fallback_uses_textlabel_text(tmp_path: Path):
    """emit_begin_edge() falls back from tooltip to parsed obj->label text."""

    exe, env = _compile_concentrate_edge_identity_tooltip_test(tmp_path)
    subprocess.run(
        (exe, "parsed-label-fallback"), capture_output=True, env=env, check=True
    )


def test_route_certificate_unit_fixtures(tmp_path: Path):
    """Post-routing portal and crossing certificates preserve topology."""

    source_lib = Path(__file__).parent.parent / "lib"
    exe = tmp_path / "routecert"
    compile_c(
        Path(__file__).parent / "routecert.c",
        cflags=[
            "-std=c17",
            f"-I{source_lib}",
            f"-I{source_lib / 'cdt'}",
            f"-I{source_lib / 'cgraph'}",
            f"-I{source_lib / 'common'}",
            f"-I{source_lib / 'gvc'}",
            f"-I{source_lib / 'pathplan'}",
            *(["-lm"] if platform.system() != "Windows" else []),
        ],
        dst=exe,
    )
    subprocess.run((exe,), capture_output=True, check=True)


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_xlabel_does_not_supply_edge_tooltip_fallback(tmp_path: Path):
    """emit_begin_edge() never assigns obj->label from ED_xlabel()."""

    exe, env = _compile_concentrate_edge_identity_tooltip_test(tmp_path)
    subprocess.run((exe, "xlabel-no-fallback"), capture_output=True, env=env, check=True)


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_ortho_duplicate_edges_are_ignored(tmp_path: Path):
    """ortho concentration marks suppressed duplicates before repeated layout."""

    exe, env = _compile_concentrate_edge_identity_tooltip_test(tmp_path)
    subprocess.run(
        (exe, "ortho-duplicate-ignored"), capture_output=True, env=env, check=True
    )


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_repeated_layout_discards_accumulated_arrows(tmp_path: Path):
    """``gv_cleanup_edge`` drops the private arrow fold before a second layout."""

    exe, env = _compile_concentrate_c_test(
        tmp_path, "concentrate_repeat_layout.c", "concentrate-repeat-layout"
    )
    result = subprocess.run(exe, capture_output=True, env=env, check=True, text=True)
    rendered = json.loads(result.stdout)
    assert len([edge for edge in rendered["edges"] if "_draw_" in edge]) == 1


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_concentrate_sums_retained_virtual_segment_weight(tmp_path: Path):
    """mergevirtual_pair() adds deleted segment weight to an existing carrier."""

    build_root = which("dot").resolve().parents[2]
    if not (build_root / "config.h").exists():
        pytest.skip("private dotgen headers require a configured build tree")

    exe, env = _compile_concentrate_c_test(
        tmp_path,
        "concentrate_segment_weight.c",
        "concentrate-segment-weight",
        extra_includes=(build_root, Path(__file__).parent.parent / "lib" / "dotgen"),
    )
    subprocess.run((exe,), capture_output=True, env=env, check=True)


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_direction_arrows_merge_rendered_identical_duplicates(
    splines: str,
):
    """Same-direction candidates compare against accumulated rendered arrows."""

    rendered_identical_duplicate = _concentrated_graph(
        splines,
        "a -> b [color=red arrowhead=normal]",
        "b -> a [color=red arrowhead=vee]",
        "a -> b [color=red dir=both arrowhead=normal arrowtail=vee]",
    )
    assert len(_drawn_edges(rendered_identical_duplicate)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_direction_arrows_keep_arrowless_candidate_distinct(
    splines: str,
):
    """Borrowed reverse arrows still protect an arrow-less later candidate."""

    arrowless_candidate = _concentrated_graph(
        splines,
        "a -> b [color=red arrowhead=normal]",
        "b -> a [color=red arrowhead=vee]",
        "a -> b [color=red dir=none]",
    )
    assert len(_drawn_edges(arrowless_candidate)) == 2


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_unconcentrated_parallel_edges_keep_separate_routes(splines: str):
    """The attribute guard does not alter ordinary multi-edge routing."""

    parallel_edges_without_concentration = f"""
        digraph {{
          graph [concentrate=false {splines}]
          a -> b [color=red]
          a -> b [color=blue]
        }}
    """
    parallel_edges = _drawn_edges(parallel_edges_without_concentration)
    assert len({edge["pos"] for edge in parallel_edges}) == 2


def test_unconcentrated_opposite_edges_share_multi_edge_routing():
    """Opposite edges remain distinct, non-overlapping members of one group."""

    opposite_edges_without_concentration = """
        digraph {
          graph [concentrate=false]
          a -> b [color=red]
          b -> a [color=blue]
        }
    """
    # A missed merge_chain() call routes both edges down the same centerline.
    # Comparing their Bezier x coordinates catches that overlap without tying
    # the test to exact node positions.
    _assert_distinct_drawn_edge_routes(opposite_edges_without_concentration, 2)


def test_unconcentrated_opposite_port_edges_share_multi_edge_routing():
    """Reverse edges compare ports at physical endpoints (GitLab #1039)."""

    opposite_edges_with_a_shared_port = """
        digraph {
          node [shape=box]
          bonn:s -> berlin
          berlin -> bonn:s
        }
    """
    # Sharing one virtual chain lets the regular multi-edge router separate the
    # two visible splines. Two independent chains collapse onto the centerline.
    _assert_distinct_drawn_edge_routes(opposite_edges_with_a_shared_port, 2)

    nonmatching_physical_ports = _concentrated_graph(
        "",
        "node [shape=box]",
        "bonn:n -> berlin",
        "berlin -> bonn:s",
    )
    assert len(_drawn_edges(nonmatching_physical_ports)) == 2


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_ports_by_physical_endpoint(splines: str):
    """Reverse ports exchange endpoint roles before matching (GitLab #1039)."""

    equivalent_reverse_ports = _concentrated_graph(
        splines,
        "node [shape=box]",
        "bonn:s -> berlin",
        "berlin -> bonn:s",
    )
    assert len(_drawn_edges(equivalent_reverse_ports)) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_clipping_by_physical_endpoint(splines: str):
    """Reverse clipping exchanges head/tail roles at endpoints (GitLab #448)."""

    same_grammar_role = _concentrated_graph(
        splines,
        "a -> b [tailclip=true, headclip=false]",
        "b -> a [tailclip=true, headclip=false]",
    )
    assert len(_drawn_edges(same_grammar_role)) == 2

    same_physical_endpoint = _concentrated_graph(
        splines,
        "a -> b [tailclip=true, headclip=false]",
        "b -> a [tailclip=false, headclip=true]",
    )
    assert len(_drawn_edges(same_physical_endpoint)) == 1


def test_concentrate_does_not_materialize_unmatched_opposite_chains():
    """An unmatched backward edge does not pre-classify later parallel edges."""

    separated_equivalent_edges = _concentrated_graph(
        "",
        "// Creating b first makes this backward edge run before a's outgoing",
        "// edges. constraint=false leaves a -> b to determine their ranks.",
        "b -> a [color=green constraint=false]",
        "a -> b [color=red]",
        "a -> b [color=blue]",
        "a -> b [color=red]",
    )
    assert sorted(_drawn_edge_colors(separated_equivalent_edges)) == [
        "#0000ff",
        "#00ff00",
        "#ff0000",
    ]


_SAME_RANK_REVERSE_EDGES = """
    strict digraph {
      concentrate=true
      subgraph same_rank {
        rank=same
        a
        b
      }
      a -> b
      b -> a
    }
"""


def test_concentrate_same_rank_equivalent_reverse_edges():
    """Equivalent same-rank reverse edges concentrate together (GitLab #150)."""

    concentrated_reverse_edges = _drawn_edges(_SAME_RANK_REVERSE_EDGES)
    assert len(concentrated_reverse_edges) == 1
    assert "_hdraw_" in concentrated_reverse_edges[0]
    assert "_tdraw_" in concentrated_reverse_edges[0]

    concentration_disabled = _SAME_RANK_REVERSE_EDGES.replace(
        "concentrate=true", "concentrate=false"
    )
    assert len(_drawn_edges(concentration_disabled)) == 2


def test_concentrate_same_rank_preserves_distinct_edge_colors():
    """Distinct same-rank reverse colors stay separate (GitLab #150)."""

    distinct_reverse_edges = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [color=red]"
    ).replace("b -> a", "b -> a [color=blue]")
    assert set(_drawn_edge_colors(distinct_reverse_edges)) == {
        "#ff0000",
        "#0000ff",
    }


def test_concentrate_same_rank_matches_ports_by_physical_endpoint():
    """Same-rank reverse ports match by physical endpoint (GitLab #150)."""

    equivalent_reverse_ports = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a:s -> b"
    ).replace("b -> a", "b -> a:s")
    assert len(_drawn_edges(equivalent_reverse_ports)) == 1

    nonmatching_reverse_ports = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a:n -> b"
    ).replace("b -> a", "b -> a:s")
    assert len(_drawn_edges(nonmatching_reverse_ports)) == 2


def test_concentrate_same_rank_compares_labels_by_rendering_role():
    """Same-rank labels compare by their rendered endpoint role (GitLab #150)."""

    same_grammar_endpoint_labels = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [headlabel=x]"
    ).replace("b -> a", "b -> a [headlabel=x]")
    assert len(_drawn_edges(same_grammar_endpoint_labels)) == 2

    same_physical_endpoint_labels = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [headlabel=x]"
    ).replace("b -> a", "b -> a [taillabel=x]")
    assert len(_drawn_edges(same_physical_endpoint_labels)) == 1

    same_rank_xlabels = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [xlabel=x]"
    ).replace("b -> a", "b -> a [xlabel=x]")
    assert len(_drawn_edges(same_rank_xlabels)) == 2


def test_concentrate_same_rank_compares_arrows_by_physical_endpoint():
    """Same-rank reverse arrows combine only at matching endpoints (GitLab #150)."""

    compatible_reverse_arrows = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [arrowhead=normal]"
    ).replace("b -> a", "b -> a [arrowhead=vee]")
    drawn_compatible_reverse_arrows = _drawn_edges(compatible_reverse_arrows)
    assert len(drawn_compatible_reverse_arrows) == 1
    assert _arrow_polygon_point_count(drawn_compatible_reverse_arrows[0], "h") == 3
    assert _arrow_polygon_point_count(drawn_compatible_reverse_arrows[0], "t") == 8

    conflicting_physical_endpoint_arrows = _SAME_RANK_REVERSE_EDGES.replace(
        "a -> b", "a -> b [dir=both arrowhead=normal arrowtail=dot]"
    ).replace("b -> a", "b -> a [dir=both arrowhead=vee arrowtail=box]")
    assert len(_drawn_edges(conflicting_physical_endpoint_arrows)) == 2


def test_concentrate_same_rank_reverse_edges_across_rank_spans():
    """Same-rank reverse concentration works across rank spans (GitLab #150)."""

    rank_span_one = """
        strict digraph {
          concentrate=true
          subgraph same_rank {
            rank=same
            a
            b
          }
          c -> a [color=green]
          a -> b [color=red]
          b -> a [color=red]
        }
    """
    assert sorted(_drawn_edge_colors(rank_span_one)) == [
        "#00ff00",
        "#ff0000",
    ]

    rank_span_two = """
        strict digraph {
          concentrate=true
          subgraph same_rank {
            rank=same
            a
            b
          }
          z -> y [color=green]
          y -> a [color=green]
          a -> b [color=red]
          b -> a [color=red]
        }
    """
    assert sorted(_drawn_edge_colors(rank_span_two)) == [
        "#00ff00",
        "#00ff00",
        "#ff0000",
    ]


def test_concentrate_rank_spanning_reverse_edges_keep_xlabels():
    """emit_end_edge() emits xlabels per edge, so neither edge may disappear."""

    labeled_reverse_edges = """
        strict digraph {
          concentrate=true
          subgraph same_rank {
            rank=same
            a
            b
          }
          c -> a
          a -> b [xlabel=x]
          b -> a [xlabel=x]
        }
    """
    assert len(_drawn_edges(labeled_reverse_edges)) == 3


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_same_rank_edges_compare_their_actual_direction(splines: str):
    """Same-rank peers compare endpoint attributes in their actual direction."""

    same_direction_distinct_ports = _concentrated_graph(
        splines,
        "subgraph same_rank { rank=same; a; b }",
        "a:n -> b:s [color=red]",
        "a:s -> b:n [color=red]",
    )
    assert len(_drawn_edges(same_direction_distinct_ports)) == 2

    one_sided_clipping = _concentrated_graph(
        splines,
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [headclip=false]",
        "b -> a",
    )
    assert len(_drawn_edges(one_sided_clipping)) == 2


@pytest.mark.parametrize(
    "source",
    (
        """
        digraph {
          graph [concentrate=true]
          { rank=same; a; b; z; }
          a -> z [samehead=x]
          b -> z [samehead=x]
        }
        """,
        """
        digraph {
          graph [concentrate=false]
          { rank=same; a; b; z; }
          a -> z [samehead=x]
          b -> z [samehead=x]
        }
        """,
        """
        digraph {
          graph [concentrate=true]
          { rank=same; z; a; b; }
          z -> a [sametail=x]
          z -> b [sametail=x]
        }
        """,
        """
        digraph {
          graph [concentrate=false]
          { rank=same; z; a; b; }
          z -> a [sametail=x]
          z -> b [sametail=x]
        }
        """,
    ),
    ids=("samehead-concentrate", "samehead", "sametail-concentrate", "sametail"),
)
def test_flat_grouped_route_arrowheads_follow_terminal_tangents(source: str):
    """Grouped flat-route arrows follow their terminal Bezier control arms."""

    drawn_edges = _drawn_edges(source)
    assert len(drawn_edges) == 2
    assert max(_arrowhead_shaft_angle(edge) for edge in drawn_edges) <= 2


@pytest.mark.parametrize(
    "source",
    (
        _concentrated_graph(
            "",
            "subgraph same_rank { rank=same; a; b }",
            "a:n -> b:s [color=red]",
            "a:s -> b:n [color=red]",
        ),
        _concentrated_graph(
            "",
            "subgraph same_rank { rank=same; a; b }",
            "a:s -> b",
            "b -> a:s",
        ),
        _concentrated_graph(
            "",
            "subgraph same_rank { rank=same; a; b }",
            "a:n -> b",
            "b -> a:s",
        ),
        _concentrated_graph(
            "splines=ortho",
            "subgraph same_rank { rank=same; a; b }",
            "a:n -> b:s [color=red]",
            "a:s -> b:n [color=red]",
        ),
    ),
    ids=("opposite-port-pairs", "merged-south-port", "north-south", "ortho"),
)
def test_concentrate_flat_port_routes_respect_endpoint_geometry(source: str):
    """Flat port routes attach, depart outward, and never re-enter nodes."""

    layout = json.loads(dot("json", source=source))
    drawn_edges = [edge for edge in layout["edges"] if "_draw_" in edge]
    assert drawn_edges
    for edge in drawn_edges:
        for endpoint in ("tail", "head"):
            _assert_compass_attachment(layout, edge, endpoint)
            _assert_endpoint_departure(layout, edge, endpoint)
        assert _arrowhead_shaft_angle(edge) <= 0.1


def test_concentrate_ungrouped_flat_port_route_preserves_tail_direction():
    """Straightening a multi-segment flat route preserves terminal port arms."""

    source = """
        digraph {
          graph [concentrate=true]
          TOP -> {rank=same a f} -> BOTTOM
          a:w -> f:e
        }
    """
    layout = json.loads(dot("json", source=source))
    edge = _drawn_edge_between(layout, "a", "f")
    points = _edge_bezier_points(edge)

    for endpoint in ("tail", "head"):
        node_id = edge["tail" if endpoint == "tail" else "head"]
        node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
        center_x, center_y, _, _ = _ellipse(node)
        route = min(
            (points, list(reversed(points))),
            key=lambda candidate: math.dist(candidate[0], (center_x, center_y)),
        )
        anchor = route[0]
        outward_point = next(
            point for point in route[1:] if math.dist(point, anchor) > 0.001
        )
        outward_normal = (anchor[0] - center_x, anchor[1] - center_y)
        departure = (outward_point[0] - anchor[0], outward_point[1] - anchor[1])
        assert sum(a * b for a, b in zip(outward_normal, departure)) > 0
    assert _arrowhead_shaft_angle(edge) <= 0.1


@pytest.mark.parametrize("concentrate", (False, True))
def test_flat_grouped_routes_depart_outward(concentrate: bool):
    """Restoring a sametail anchor also translates its terminal control arm."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()}]
          {{ rank=same; z; a; b; }}
          z -> a [sametail=x]
          z -> b [sametail=x]
        }}
    """
    layout = json.loads(dot("json", source=source))
    for edge in (edge for edge in layout["edges"] if "_draw_" in edge):
        _assert_endpoint_departure(layout, edge, "tail")


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_flat_mixed_sametail_route_does_not_reenter_tail(
    concentrate: bool,
):
    """A restored flat member of a mixed sametail group clears the tail node."""

    layout = json.loads(dot("json", source=_sametail_mixed_route_fixture(concentrate)))
    edge = _drawn_edge_between(layout, "A", "flat")
    _assert_endpoint_departure(layout, edge, "tail")


@pytest.mark.parametrize("concentrate", (False, True))
def test_concentrate_mixed_samehead_backward_route_approaches_outward(
    concentrate: bool,
):
    """A backward member of a mixed samehead group clears the head node."""

    layout = json.loads(dot("json", source=_samehead_mixed_route_fixture(concentrate)))
    edge = _drawn_edge_between(layout, "back_anchor", "A")
    _assert_endpoint_departure(layout, edge, "head")


@pytest.mark.parametrize("concentrate", (False, True))
def test_flat_grouped_routes_preserve_head_direction_under_rankdir_flip(
    concentrate: bool,
):
    """restore_flat_edge_ports() sees physical tails after rankdir=LR swapping."""

    source = f"""
        digraph {{
          graph [concentrate={str(concentrate).lower()} rankdir=LR]
          {{ rank=same; z; a; b; }}
          z -> a [samehead=x]
          z -> b [samehead=x]
        }}
    """
    layout = json.loads(dot("json", source=source))
    for edge in (edge for edge in layout["edges"] if "_draw_" in edge):
        _assert_endpoint_departure(layout, edge, "head")


def test_concentrate_flat_bidirectional_arrows_use_distinct_clip_ends():
    """A short merged flat route arcs enough for both endpoint arrows."""

    source = _concentrated_graph(
        "",
        "subgraph same_rank { rank=same; a; b }",
        "a -> b [headlabel=x]",
        "b -> a [taillabel=x]",
    )
    layout = json.loads(dot("json", source=source))
    edge = next(edge for edge in layout["edges"] if "_draw_" in edge)
    assert len([edge for edge in layout["edges"] if "_draw_" in edge]) == 1

    head_box = _point_box(_edge_arrow_polygon(edge, "_hdraw_"))
    tail_box = _point_box(_edge_arrow_polygon(edge, "_tdraw_"))
    assert _boxes_are_disjoint(head_box, tail_box)

    for endpoint in ("tail", "head"):
        node_id = edge[endpoint]
        node = next(node for node in layout["objects"] if node["_gvid"] == node_id)
        center_x, center_y, radius_x, radius_y = _ellipse(node)
        x, y = _edge_physical_endpoint(edge, endpoint)
        boundary = ((x - center_x) / radius_x) ** 2 + (
            (y - center_y) / radius_y
        ) ** 2
        assert boundary == pytest.approx(1, abs=0.05)


def test_concentrate_short_compound_arrows_do_not_overlap():
    """Short cluster-clipped reverse edges must not draw overlapping arrows."""

    source = """
        digraph {
          graph [concentrate=true compound=true]
          subgraph cluster_a { a }
          subgraph cluster_b { b }
          a -> b [ltail=cluster_a lhead=cluster_b]
          b -> a [ltail=cluster_b lhead=cluster_a]
        }
    """
    edge = _drawn_edges(source)[0]
    arrows = [
        operation["points"]
        for stream in ("_hdraw_", "_tdraw_")
        for operation in edge.get(stream, [])
        if operation["op"] == "P"
    ]
    boxes = [_point_box(arrow) for arrow in arrows]
    for first, second in itertools.combinations(boxes, 2):
        assert _boxes_are_disjoint(first, second)


def test_concentrate_compound_clipping_keeps_suppressed_junction_arrows_hidden():
    """Cluster clipping must preserve concentrated junction suppression bits."""

    source = """
        digraph {
          graph [concentrate=true compound=true]
          subgraph cluster_a { a }
          subgraph cluster_b { b }
          a -> b [ltail=cluster_a lhead=cluster_b]
          b -> a [ltail=cluster_b lhead=cluster_a]
        }
    """
    edge = _drawn_edges(source)[0]
    arrow_polygons = [
        operation
        for stream in ("_hdraw_", "_tdraw_")
        for operation in edge.get(stream, [])
        if operation["op"] == "P"
    ]
    assert len(arrow_polygons) == 1


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_matches_reverse_arrowheads_by_physical_endpoint(
    splines: str,
):
    """Opposite arrowheads render at opposite physical endpoints."""

    compatible_reverse_arrows = _concentrated_graph(
        splines,
        "a -> b [arrowhead=normal]",
        "b -> a [arrowhead=vee]",
    )
    drawn_edges = _drawn_edges(compatible_reverse_arrows)
    assert len(drawn_edges) == 1
    assert _arrow_polygon_point_count(drawn_edges[0], "h") == 3
    assert _arrow_polygon_point_count(drawn_edges[0], "t") == 8


@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_rejects_conflicting_physical_endpoint_arrows(splines: str):
    """Packed arrow flags cannot hold two different shapes at one endpoint."""

    conflicting_reverse_arrows = _concentrated_graph(
        splines,
        "a -> b [dir=both arrowhead=normal arrowtail=dot]",
        "b -> a [dir=both arrowhead=vee arrowtail=box]",
    )
    assert len(_drawn_edges(conflicting_reverse_arrows)) == 2


def test_concentrate_flat_cycle_keeps_virtual_representatives_private():
    """Flat cycle reversal keeps non-Cgraph representative edges private."""

    flat_cycle = _concentrated_graph(
        "",
        "subgraph same_rank { rank=same; a; b; c }",
        "a -> b",
        "b -> c",
        "c -> a",
        "a -> c",
    )
    assert len(_drawn_edges(flat_cycle)) == 3


def test_concentrate_flat_aux_routes_keep_suppressed_junction_arrows_hidden():
    """Aux-graph flat route copies must preserve concentrated suppression bits."""

    layout = json.loads(
        dot(
            "json",
            source="""
                digraph {
                  graph [concentrate=true]
                  { rank=same; a; b }
                  a:e -> b:w
                  b:w -> a:e
                }
            """,
        )
    )
    arrow_polygons = [
        operation
        for edge in layout["edges"]
        for stream in ("_hdraw_", "_tdraw_")
        for operation in edge.get(stream, [])
        if operation["op"] == "P"
    ]
    assert len(arrow_polygons) == 1


def test_concentrate_ortho_ignores_suppressed_representatives():
    """Suppressed edges never become ortho concentration group heads."""

    ignored_reverse_first = _concentrated_graph(
        "splines=ortho",
        "b",
        "a -> b [color=red]",
        "b -> a [constraint=false color=red]",
        "a -> b [color=blue]",
    )
    assert sorted(_drawn_edge_colors(ignored_reverse_first)) == [
        "#0000ff",
        "#ff0000",
    ]


@pytest.mark.parametrize("direction", ("down", "up"))
@pytest.mark.parametrize("splines", ("", "splines=ortho"))
def test_concentrate_preserves_distinct_record_port_continuations(
    direction: str, splines: str
):
    """Concentration retains distinct record-port continuations (GitLab #449)."""

    if direction == "down":
        rank_constraint = "subgraph { rank=source; source }"
        distinct_edges = """
          some -> problem:p1
          source -> problem:p2
          source -> problem:p3
        """
    else:
        rank_constraint = "subgraph { rank=sink; sink }"
        distinct_edges = """
          problem:p1 -> some
          problem:p2 -> sink
          problem:p3 -> sink
        """

    distinct_ports = _concentrated_graph(
        splines,
        'problem [shape=record, label="<p1>p1|<p2>p2|<p3>p3"]',
        rank_constraint,
        distinct_edges,
    )
    assert len(_drawn_edges(distinct_ports)) == 3

    same_port_duplicates = distinct_ports.replace("problem:p3", "problem:p2")
    assert len(_drawn_edges(same_port_duplicates)) == 2


@pytest.mark.parametrize("direction", ("down", "up"))
def test_concentrate_record_port_routes_clip_at_field_boundaries(direction: str):
    """Distinct record continuations attach at their own field rectangles."""

    if direction == "down":
        rank_constraint = "subgraph { rank=source; source }"
        distinct_edges = """
          some -> problem:p1
          source -> problem:p2
          source -> problem:p3
        """
        endpoint = "head"
    else:
        rank_constraint = "subgraph { rank=sink; sink }"
        distinct_edges = """
          problem:p1 -> some
          problem:p2 -> sink
          problem:p3 -> sink
        """
        endpoint = "tail"

    source = _concentrated_graph(
        "",
        'problem [shape=record, label="<p1>p1|<p2>p2|<p3>p3"]',
        rank_constraint,
        distinct_edges,
    )
    layout = json.loads(dot("json", source=source))
    problem = next(node for node in layout["objects"] if node["name"] == "problem")
    rectangles = tuple(
        tuple(map(float, rectangle.split(","))) for rectangle in problem["rects"].split()
    )
    problem_id = problem["_gvid"]
    edges = [
        edge
        for edge in layout["edges"]
        if "_draw_" in edge and edge[endpoint] == problem_id
    ]
    assert len(edges) == 3
    for edge in edges:
        port = edge[f"{endpoint}port"]
        rectangle = rectangles[int(port[1:]) - 1]
        x, y = _edge_physical_endpoint(edge, endpoint)
        left, bottom, right, top = rectangle
        # `rects` describes the record-field interior; clipping happens at
        # the outside of the half-point outline stroke.
        assert left - 0.6 <= x <= right + 0.6
        assert bottom - 0.6 <= y <= top + 0.6
        assert min(abs(x - left), abs(x - right), abs(y - bottom), abs(y - top)) <= 0.6


def _assert_public_concentrate_crash_repro_renders(issue: int):
    input = Path(__file__).parent / f"{issue}.dot"
    assert input.exists(), "unexpectedly missing test case"

    proc = subprocess.run(
        ["dot", "-Kdot", "-Tdot", input],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    for token in ("AddressSanitizer", "DEADLYSIGNAL", "SEGV"):
        assert token not in proc.stderr
    assert any("->" in line and "pos=" in line for line in proc.stdout.splitlines())


def test_concentrate_issue_2764_public_repro_renders_edge_splines():
    """GitLab #2764: raw public conc_slope crash repro still renders edges."""

    _assert_public_concentrate_crash_repro_renders(2764)


def test_concentrate_issue_2765_public_repro_renders_edge_splines():
    """GitLab #2765: raw public straight_len crash repro still renders edges."""

    _assert_public_concentrate_crash_repro_renders(2765)


def test_150():
    """
    `concentrate=true` should concentrate equivalent same-rank reverse edges
    https://gitlab.com/graphviz/graphviz/-/issues/150
    """

    source = """
        strict digraph {
          concentrate=true
          subgraph foo {
            rank=same
            a
            b
          }
          a -> b
          b -> a
        }
    """
    def drawn_edges(source: str) -> list[dict]:
        layout = json.loads(dot("json", source=source))
        return [edge for edge in layout["edges"] if "_draw_" in edge]

    def drawn_colors(layout: dict) -> list[str]:
        return [
            operation["color"]
            for edge in layout["edges"]
            for operation in edge.get("_draw_", ())
            if operation["op"] == "c"
        ]

    drawn = drawn_edges(source)
    assert len(drawn) == 1, "same-rank reverse edges were not concentrated"

    disabled = source.replace("concentrate=true", "concentrate=false")
    assert len(drawn_edges(disabled)) == 2, "concentrate=false changed edge drawing"

    distinct = source.replace("a -> b", "a -> b [color=red]").replace(
        "b -> a", "b -> a [color=blue]"
    )
    assert set(drawn_colors(json.loads(dot("json", source=distinct)))) == {
        "#ff0000",
        "#0000ff",
    }

    # dot_concentrate() takes its early path for rank spans of zero and one.
    # `c -> a` makes the span one while `a` and `b` remain in the same rank.
    rank_span_one = """
        strict digraph {
          concentrate=true
          subgraph foo {
            rank=same
            a
            b
          }
          c -> a [color="#00aa00"]
          a -> b [color=red]
          b -> a [color=red]
        }
    """
    layout = json.loads(dot("json", source=rank_span_one))
    positions = {
        node["name"]: node["pos"].rsplit(",", 1)[1]
        for node in layout["objects"]
        if node["name"] in {"a", "b", "c"}
    }
    assert positions["a"] == positions["b"]
    assert positions["a"] != positions["c"], "expected exactly two rank layers"
    colors = drawn_colors(layout)
    assert colors.count("#ff0000") == 1, "rank-span-1 reverse edges were not concentrated"
    assert colors.count("#00aa00") == 1


@pytest.mark.skipif(which("fdp") is None, reason="fdp not available")
def test_2563():
    """
    `overlap` parameters should generate different results
    https://gitlab.com/graphviz/graphviz/-/issues/2563
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2563.dot"
    assert input.exists(), "unexpectedly missing test case"

    # try various `overlap=…` values
    results: set[str] = set()
    for overlap in ("scale", "scalexy"):
        # run this through fdp
        fdp = which("fdp")
        p = subprocess.run(
            [fdp, f"-Goverlap={overlap}", input],
            capture_output=True,
            check=False,
            text=True,
        )

        # if fdp was built without libgts, it will not handle anything non-trivial
        no_gts_error = "remove_overlap: Graphviz not built with triangulation library"
        if no_gts_error in p.stderr:
            assert p.returncode != 0, "fdp returned success after an error message"
            return
        p.check_returncode()

        # remove the overlap parameter itself, that would otherwise cause each
        # output to be unique
        output = re.sub(r"\boverlap\s*=\s*scale(xy)?\b", "", p.stdout)

        assert (
            output not in results
        ), "altering `overlap` attribute did not affect output"
        results.add(output)


def test_2564():
    """
    `overlap="scale"` should not result in all nodes overlapping
    https://gitlab.com/graphviz/graphviz/-/issues/2564
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2564.dot"
    assert input.exists(), "unexpectedly missing test case"

    # convert this to JSON
    layout = run("dot", "-Kneato", "-Tjson", input)
    parsed = json.loads(layout)

    # nodes should not be on top of one another
    starts = []
    for node in parsed["objects"]:
        start = re.match(r"(?P<start>\d+(\.\d+)?,\d+(\.\d+)?)\b", node["pos"]).group(
            "start"
        )
        assert start not in starts, "nodes overlap"
        starts += [start]


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="pexpect.spawn is not available on Windows "
    "(https://pexpect.readthedocs.io/en/stable/overview.html#pexpect-on-windows)",
)
def test_2568():
    """
    tags used in TCL output should be usable for later lookup
    https://gitlab.com/graphviz/graphviz/-/issues/2568
    """

    # locate the TCL input for this test
    prelude = Path(__file__).parent / "2568.tcl"
    assert prelude.exists(), "unexpectedly missing test collateral"

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # startup TCL and load our graph setup code
    proc = pexpect_spawn_tclsh(timeout=1, env=env)
    proc.expect("% ")
    proc.sendline(f'source "{shlex.quote(str(prelude))}"')

    # look for tags to query
    while True:
        index = proc.expect(
            [
                "invalid command name",
                re.compile(rb"-tags {\d(?P<tag>(edge|node)0x[\da-fA-F]+)}"),
                pexpect.TIMEOUT,
            ]
        )

        # stdout and stderr are multiplexed onto the same stream by `pexpect`, so if one
        # of the commands we previously entered was not recognized, we will see an error
        # at the end of the output stream
        assert index != 0, "at least one tag was not recognized"

        # if we got no output within 1s, assume we are done
        if index == 2:
            break

        tag = proc.match.group("tag").decode("utf-8")

        # try to look up its corresponding entities
        if tag.startswith("edge"):
            cmd = "listnodes"
        else:
            cmd = "listedges"
        proc.sendline(f"{tag} {cmd}")


@pytest.mark.skipif(which("sfdp") is None, reason="sfdp not available")
def test_2572():
    """
    sfdp should be able to find non-overlapping layouts
    https://gitlab.com/graphviz/graphviz/-/issues/2572
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2572.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through SFDP and convert this to JSON
    sfdp = which("sfdp")
    layout = run(sfdp, "-Kneato", "-Tjson", input)
    parsed = json.loads(layout)

    @dataclasses.dataclass
    class Box:
        """
        a geometric rectangle, defined by two of its corners
        """

        llx: float  # lower left X coordinate
        lly: float  # lower left Y coordinate
        urx: float  # upper right X coordinate
        ury: float  # upper right Y coordinate

        def overlaps(self, other: "Box") -> bool:
            """
            do we intersect the given box?
            """
            if self.llx > other.urx:
                return False
            if self.lly > other.ury:
                return False
            if self.urx < other.llx:
                return False
            if self.ury < other.lly:
                return False
            return True

    nodes: list[Box] = []
    for obj in parsed["objects"]:
        # extract the ellipse drawn for this node
        ellipses = [e for e in obj["_draw_"] if e["op"] == "e"]
        assert len(ellipses) == 1, "could not find ellipse for node"
        center_x, center_y, width, height = ellipses[0]["rect"]

        assert center_x >= width, "ellipse extends into negative X space"
        assert center_y >= height, "ellipse extends into negative Y space"

        node = Box(
            center_x - width, center_y - height, center_x + width, center_y + height
        )

        assert not any(n.overlaps(node) for n in nodes), "nodes overlap"

        nodes.append(node)


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_2577():
    """
    accessing an uninitialized string should not corrupt GVPR’s state
    https://gitlab.com/graphviz/graphviz/-/issues/2577
    """

    # find our collocated test case
    program = Path(__file__).parent / "2577.gvpr"
    assert program.exists(), "unexpectedly missing test case"

    # run it through GVPR
    output = gvpr(program)

    # it should have printed an empty string for the uninitialized attribute
    assert (
        "Before...\n<>\nAfter." in output
    ), "incorrect handling of uninitialized attribute in GVPR"


@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_2577_1():
    """
    a variant of `test_2577` that does not involve attribute access
    https://gitlab.com/graphviz/graphviz/-/issues/2577
    """

    # run GVPR on a simple program
    gvprbin = which("gvpr")
    output = run(
        gvprbin,
        'BEGIN { printf("hello%s world\\n", ""); }',
        stdin=subprocess.DEVNULL,
    )

    # it should have printed the expected text
    assert output == "hello world\n", "gvpr cannot handle empty strings to printf"


@pytest.mark.parametrize(
    "program,a_arg,expected",
    (
        ("BEGIN { print(#ARGV); }", "abc", "1"),
        ("BEGIN { print(0 in ARGV); }", "abc", "1"),
        (
            'BEGIN {string argv[int]; argv[0] = "abc"; print(#argv); print(0 in argv);}',
            None,
            "1\n1",
        ),
    ),
)
@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_2582(program: str, a_arg: Optional[str], expected: str):
    """
    gvpr should treat `ARGV` as an array
    https://gitlab.com/graphviz/graphviz/-/issues/2582

    Args:
        program: Program text to run in gvpr
        a_arg: An optional parameter to pass via `-a …` to gvpr
        expected: Expected output
    """
    gvprbin = which("gvpr")
    args = [gvprbin]
    if a_arg is not None:
        args += ["-a", a_arg]
    args += [program]

    actual = run(*args)

    assert actual.strip() == expected, "unexpected GVPR program output"


@pytest.mark.parametrize(
    "testcase",
    (
        "2585",
        "2585_1",
        "2585_2",
        "2585_3",
        "2585_4",
        "2585_5",
        "2585_6",
        "2585_7",
    ),
)
@pytest.mark.skipif(which("gvpr") is None, reason="GVPR not available")
def test_2585(testcase: str):
    """
    GVPR should reject various invalid uses of `void` types
    https://gitlab.com/graphviz/graphviz/-/issues/2585
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / f"{testcase}.gvpr"
    assert input.exists(), "unexpectedly missing test case"

    # run the program
    gvprbin = which("gvpr")
    ret = subprocess.call(
        [gvprbin, "-o", os.devnull, "-f", input], stdin=subprocess.DEVNULL
    )

    assert ret == 1, "GVPR did not reject invalid use of `void`"


@pytest.mark.skipif(which("gml2gv") is None, reason="gml2gv not available")
def test_2586():
    """
    labels should be preserved in GML→GV translation
    https://gitlab.com/graphviz/graphviz/-/issues/2586
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2586.gml"
    assert input.exists(), "unexpectedly missing test case"

    # translate it
    gml2gv = which("gml2gv")
    gv = run(gml2gv, input)

    assert (
        re.search(r'\blabel\s*=\s*"?0"?\b', gv) is not None
    ), "labels not preserved in GML→GV translation"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_2587():
    """
    gvpr should have a usable `unsigned` type
    https://gitlab.com/graphviz/graphviz/-/issues/2587
    """

    gvpr_bin = which("gvpr")
    output = run(
        gvpr_bin,
        "BEGIN { unsigned x = 281; print(x); }",
        stdin=subprocess.DEVNULL,
    )

    assert output == "281\n", "gvpr did not correctly interpret an `unsigned`"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_2587_1():
    """
    gvpr should have a usable `unsigned` type
    https://gitlab.com/graphviz/graphviz/-/issues/2587
    """

    gvpr_bin = which("gvpr")
    output = run(
        gvpr_bin,
        'BEGIN { unsigned x; sscanf("139", "%u", &x); print(x); }',
        stdin=subprocess.DEVNULL,
    )

    assert output == "139\n", "gvpr did not correctly interpret an `unsigned`"


@pytest.mark.skipif(which("gvgen") is None, reason="gvgen not available")
def test_2588():
    """
    `gvgen` should not crash when producing random graphs
    https://gitlab.com/graphviz/graphviz/-/issues/2588
    """

    gvgen = which("gvgen")

    # this execution depends on random numbers, so we need to run many times to
    # have a chance of provoking the bug
    for _ in range(200):
        run_raw(gvgen, "-R", "20", stdout=subprocess.DEVNULL)


@pytest.mark.skipif(which("edgepaint") is None, reason="edgepaint not available")
@pytest.mark.skipif(which("gvgen") is None, reason="gvgen not available")
def test_2591():
    """
    edgepaint color schemes should do something
    https://gitlab.com/graphviz/graphviz/-/issues/2591
    """

    # make an input graph
    gvgen = which("gvgen")
    graph = run(gvgen, "-k", "5")

    # run it through neato
    laidout = run("dot", "-Kneato", "-Goverlap=false", input=graph)

    # try two different edgepaint invocations
    edgepaint = which("edgepaint")
    gray = run(edgepaint, "--angle=89.999", "--color_scheme=gray", input=laidout)
    rgb = run(
        edgepaint, "--angle=89.999", "--color_scheme=#00ff00,#0000ff", input=laidout
    )

    # process these into an image
    gray_svg = run("dot", "-Kneato", "-n2", "-Tsvg", input=gray)
    rgb_svg = run("dot", "-Kneato", "-n2", "-Tsvg", input=rgb)

    assert gray_svg != rgb_svg, "edgepaint --color_scheme had no effect"


@pytest.mark.skipif(which("ccomps") is None, reason="ccomps not available")
def test_2593():
    """
    ccomps should be able to handle this graph in reasonable time
    https://gitlab.com/graphviz/graphviz/-/issues/2593
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2593.dot"
    assert input.exists(), "unexpectedly missing test case"

    # this typically takes 30-45s to run, so give a wide margin of error and require
    # that ccomps finishes within that
    ccomps = which("ccomps")
    proc = subprocess.run(
        [ccomps, "-o", os.devnull, input], timeout=60 * 10, check=False
    )

    assert proc.returncode == 1, "ccomps did not detect graphs have multiple components"


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="pexpect.spawn is not available on Windows "
    "(https://pexpect.readthedocs.io/en/stable/overview.html#pexpect-on-windows)",
)
@pytest.mark.xfail(
    is_cmake() and is_macos(),
    reason="FIXME: 'vgpane' command is unrecognized for unknown reasons",
    strict=True,
)
@pytest.mark.xfail(
    is_autotools() and is_macos(),
    reason="Autotools on macOS does not detect TCL",
    strict=True,
)
def test_2596():
    """
    running Tclpathplan `triangulate` with a malformed callback script should not read
    out-of-bounds
    https://gitlab.com/graphviz/graphviz/-/issues/2596
    """

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # startup TCL and load the pathplan module
    proc = pexpect_spawn_tclsh(timeout=1, env=env)
    proc.expect("% ")
    proc.sendline("package require Tclpathplan")
    proc.expect("% ")

    # Create a pane. We assume the first created pane will be index 0, though
    # this is not technically required.
    proc.sendline("vgpane")
    proc.expect("vgpane0")
    proc.expect("% ")

    # bind the triangulation callback to something ending in a trailing '%'
    proc.sendline("vgpane0 bind triangle %")
    proc.expect("% ")

    # add a triangular polygon
    proc.sendline("vgpane0 insert 1 1 2 2 1 2")
    proc.expect("1")
    proc.expect("% ")

    # attempt triangulation on this polygon
    proc.sendline("vgpane0 triangulate 1")
    proc.expect("% ")

    # delete the pane to clean up, to exit ASan-clean
    proc.sendline("vgpane0 delete")
    proc.expect("% ")


@pytest.mark.skipif(not is_cmake(), reason="only relevant in CMake builds")
@pytest.mark.skipif(shutil.which("cmake") is None, reason="cmake not available")
@pytest.mark.skipif(
    re.search(r"\bstatic\b", os.environ.get("CI_JOB_NAME", "")) is not None,
    reason="CMake support files are not installed in static builds",
)
def test_2598(tmp_path: Path):
    """
    Graphviz, as installed by the CMake build system, should be usable with standard
    CMake idioms
    https://gitlab.com/graphviz/graphviz/-/issues/2598
    """

    # configure a build directory for our sample applications
    src = Path(__file__).parent / "2598"
    args = ["cmake", "--debug-find", "-B", tmp_path, "-S", src]
    if os.environ.get("CI_JOB_NAME", "").startswith("windows-cmake-Win32"):
        args += ["-A", "Win32"]
    run_raw(*args)

    # run compilation
    run_raw("cmake", "--build", tmp_path)


@pytest.mark.skipif(not is_cmake(), reason="only relevant in CMake builds")
@pytest.mark.skipif(shutil.which("cmake") is None, reason="cmake not available")
@pytest.mark.skipif(
    re.search(r"\bstatic\b", os.environ.get("CI_JOB_NAME", "")) is not None,
    reason="CMake support files are not installed in static builds",
)
def test_2598_1(tmp_path: Path):
    """
    A variant of test_2598, that does not directly use cdt
    https://gitlab.com/graphviz/graphviz/-/issues/2598
    """

    # configure a build directory for our sample applications
    src = Path(__file__).parent / "2598_1"
    args = ["cmake", "--debug-find", "-B", tmp_path, "-S", src]
    if os.environ.get("CI_JOB_NAME", "").startswith("windows-cmake-Win32"):
        args += ["-A", "Win32"]
    run_raw(*args)

    # run compilation
    run_raw("cmake", "--build", tmp_path)


@pytest.mark.skipif(which("gvgen") is None, reason="gvgen not available")
@pytest.mark.skipif(which("mingle") is None, reason="mingle not available")
def test_2599():
    """
    mingle should not segfault when processing simple graphs
    https://gitlab.com/graphviz/graphviz/-/issues/2599
    """

    # generate a graph
    gvgen = which("gvgen")
    graph = run(gvgen, "-d", "-k", "5")

    # process it into canonical form
    processed = run("dot", input=graph)

    # pass it through mingle
    mingle = which("mingle")
    proc = subprocess.run(
        [mingle, "-v", "999"], check=False, text=True, input=processed
    )

    assert proc.returncode in (0, 1), "mingle crashed"


@pytest.mark.skipif(which("acyclic") is None, reason="acyclic not available")
def test_2600():
    """
    acyclic should produce output
    https://gitlab.com/graphviz/graphviz/-/issues/2600
    """

    # run acyclic on a simple cyclic graph
    acyclic = which("acyclic")
    ret = subprocess.run(
        [acyclic],
        input="digraph { A -> B -> C -> D -> E; E -> A }",
        stdout=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert ret.returncode == 1, "acyclic did not detect a cyclic graph"
    assert ret.stdout.strip() != "", "acyclic produced no output"


@pytest.mark.skipif(which("dot_builtins") is None, reason="dot_builtins not available")
def test_2604():
    """
    dot_builtins should not repeat formats in guidance
    https://gitlab.com/graphviz/graphviz/-/issues/2604
    """

    # a simple graph
    input = "digraph { a -> b; }"

    # run dot_builtins with an incorrect format
    dot_builtins = which("dot_builtins")
    proc = subprocess.run(
        [dot_builtins, "-o", os.devnull, "-Tpng:"],
        stderr=subprocess.PIPE,
        input=input,
        text=True,
        check=False,
    )

    assert proc.returncode != 0, "dot_builtins accepted malformed format 'png:'"

    assert (
        len(re.findall(r"\bpng:cairo:cairo\b", proc.stderr)) <= 1
    ), "duplicate formats listed in guidance"


@pytest.mark.skipif(which("neato") is None, reason="neato not available")
def test_2609(tmp_path: Path):
    """
    GIFs should not be blank
    https://gitlab.com/graphviz/graphviz/-/issues/2609
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2609.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Neato and convert to GIF
    neato = which("neato")
    gif = tmp_path / "2609.gif"
    run_raw(neato, "-Tgif", input, "-o", gif)

    # load the image and scan its pixels
    img = Image.open(gif)
    reference = None
    for x in range(img.width):
        for y in range(img.height):
            pixel = img.getpixel((x, y))
            if reference is None:
                reference = pixel
            elif reference != pixel:
                # found a different pixel
                return

    pytest.fail("generated GIF was a solid color")


def test_2613():
    """
    Graphviz should not fail an assertion when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2613
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2613.dot"
    assert input.exists(), "unexpectedly missing test case"

    # generate it as a PDF
    dot("pdf", input)


def test_2614():
    """
    quotes in strings should be correctly escaped
    https://gitlab.com/graphviz/graphviz/-/issues/2614
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2614.dot"
    assert input.exists(), "unexpectedly missing test case"

    # generate the canonical form of this
    canonical = dot("canon", input)

    # it should be re-parseable
    dot("svg", source=canonical)

    # quotes should have been escaped
    assert canonical.count('\\"') == 2, "quotes in string were not properly escaped"


def test_2615():
    """
    cluster→cluster edges should not be duplicated
    https://gitlab.com/graphviz/graphviz/-/issues/2615
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2615.dot"
    assert input.exists(), "unexpectedly missing test case"

    # lay this out
    layout = dot("dot", input)

    # find an inter-cluster edge
    m = re.search(r'\bD\s*->\s*F\s*\[\s*pos\s*=\s*"(?P<position>[^"]*)"', layout)
    assert m is not None, "could not locate D->F edge"

    edge_count = len(re.findall(r"\be\b", m.group("position")))
    assert edge_count == 1, "incorrect number of inter-cluster edges"


@pytest.mark.xfail(
    platform.system() == "Windows" and not is_mingw() and not is_ndebug_defined(),
    strict=True,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2619",
)
def test_2619():
    """
    loading a JPEG with initial EXIF stream should be possible
    https://gitlab.com/graphviz/graphviz/-/issues/2619
    """

    # we need to run in our own directory so relative path references work
    cwd = Path(__file__).parent

    # our test case should be translatable to PDF
    run_raw("dot", "-Tpdf", "-o", os.devnull, "2619.dot", cwd=cwd)


@pytest.mark.xfail(
    platform.system() == "Windows" and not is_mingw() and not is_ndebug_defined(),
    strict=True,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2619",
)
@pytest.mark.parametrize("images", ("2619_1", "2619_2"))
@pytest.mark.parametrize("output", ("pdf", "png"))
@pytest.mark.parametrize("source", ("2619_1.dot", "2619_2.dot"))
def test_2619_1(images: str, output: str, source: str, tmp_path: Path):
    """
    output files for this graph should be non-empty
    https://gitlab.com/graphviz/graphviz/-/issues/2619
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / source
    assert input.exists(), "unexpectedly missing test case"

    # copy it to a temporary path, replacing image references for our variant
    content = input.read_bytes()
    specialized = re.sub(
        rb"\b2619_(\d)1\.jpg\b", images.encode("utf-8") + rb"_\1.jpg", content
    )
    destination = tmp_path / "2619.dot"
    destination.write_bytes(specialized)

    # copy images to the expected directory structure
    media = tmp_path / "data/media/media/schoenfeld-liberman.ged"
    media.mkdir(parents=True)
    for i in (1, 2, 3):
        src = Path(__file__).parent / f"{images}_{i}.jpg"
        shutil.copy(src, media / f"2619_{i}.jpg")

    def sh(args: list[Union[Path, str]], stdin: Optional[bytes] = None) -> bytes:
        """run a command, as if via the shell"""
        nonlocal tmp_path

        # dump the command being run for the user to observe if the test fails
        print(
            f"+ cd {shlex.quote(str(tmp_path))} && {shlex.join(str(x) for x in args)}"
        )

        proc = subprocess.run(
            args, input=stdin, stdout=subprocess.PIPE, cwd=tmp_path, check=True
        )
        return proc.stdout

    # render this
    dot_result = sh(["dot", f"-T{output}", destination])

    assert dot_result.strip() != b"", "an empty file was rendered"

    # render it with exact position information
    positioned = sh(["dot", "-Tdot", destination])

    # use this to render with neato
    neato = which("neato")
    neato_result = sh([neato, "-n2", f"-T{output}"], stdin=positioned)

    assert neato_result.strip() != b"", "an empty file was rendered"


@pytest.mark.xfail(
    platform.system() == "Windows" and not is_mingw() and not is_ndebug_defined(),
    strict=True,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2619",
)
def test_2619_3():
    """
    loading a JPEG image shall not cause a crash in the GD plugin when the output format is PDF
    https://gitlab.com/graphviz/graphviz/-/issues/2619
    """

    # we need to run in our own directory so relative path references work
    cwd = Path(__file__).parent

    src = 'digraph {a [image="2619_1_2.jpg"]}'.encode("utf-8")

    # our test case shall not cause a crash
    run_raw("dot", "-Tpdf", "-o", os.devnull, cwd=cwd, input=src)


def test_2619_4():
    """
    processing a node with the 'image' attribute set to a JPEG file shall not yield warnings
    https://gitlab.com/graphviz/graphviz/-/issues/2619
    """

    # we need to run in our own directory so relative path references work
    cwd = Path(__file__).parent

    src = 'digraph {a [image="2619.jpg"]}'

    output = run(
        "dot",
        "-Tsvg",
        "-o",
        os.devnull,
        cwd=cwd,
        input=src,
        stderr=subprocess.STDOUT,
    )

    assert "Warning:" not in output, f"Warnings issued: {output}"


@pytest.mark.parametrize(
    "image",
    (
        "2619.jpg",
        "2619_1_1.jpg",
        "2619_1_2.jpg",
        "2619_1_3.jpg",
        "2619_2_1.jpg",
        "2619_2_2.jpg",
        "2619_2_3.jpg",
    ),
)
def test_2619_5(image: str):
    """
    a node with the 'image' attribute set to a JPEG file shall render an SVG
    containing an 'image' element with the correct width and height
    https://gitlab.com/graphviz/graphviz/-/issues/2619
    """

    # we need to run in our own directory so relative path references work
    cwd = Path(__file__).parent

    file = cwd / image
    width, height = Image.open(file).size

    src = f'digraph {{a [image="{image}"]}}'

    svg = run("dot", "-Tsvg", cwd=cwd, input=src)

    # load it as XML
    root = ET.fromstring(svg)

    # find the `image` element
    image_element = root.findall(".//{http://www.w3.org/2000/svg}image")

    assert len(image_element) == 1, "could not find an 'image' element in the SVG"

    assert image_element[0].get("width") == f"{width}px"
    assert image_element[0].get("height") == f"{height}px"


def test_2620():
    """
    arrows in this graph should not be truncated
    https://gitlab.com/graphviz/graphviz/-/issues/2620
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2620.dot"
    assert input.exists(), "unexpectedly missing test case"

    # render to SVG
    svg = dot("svg", input)

    # parse the SVG
    root = ET.fromstring(svg)

    # most of the differences between the “good” and “bad” rendering are small
    # (~1pt diff), so discriminate using one that has been observed to be much larger
    edge = root.findall(
        ".//{http://www.w3.org/2000/svg}g[@id='edge101']/{http://www.w3.org/2000/svg}path"
    )
    assert len(edge) == 1, "could not find expected edge"

    # parse the expected drawing instructions out of this
    m = re.match("M(?P<move>.*)C(?P<curve>.*)", edge[0].attrib["d"])
    assert m is not None, "drawing command in unexpected format"

    # the curve is expected to be composed of two Béziers
    points = m.group("curve").split()
    assert len(points) == 6, "unexpected number of Bézier curve components"

    bezier2 = [pt.split(",") for pt in points[3:]]
    assert all(len(pt) == 2 for pt in bezier2), "unexpected Bézier composition"

    # compare the end point of the last command against what we expect with a large
    # margin of error
    expected = -7286.11
    assert abs(float(bezier2[2][1]) - expected) < 1000, "incorrect edge construction"


def test_2621():
    """
    this graph should not trigger an integer overflow in crossing calculation
    https://gitlab.com/graphviz/graphviz/-/issues/2621
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2621.dot"
    assert input.exists(), "unexpectedly missing test case"

    run_raw("dot", "-Gmclimit=.05", "-Gphase=2", "-Tsvg", "-o", os.devnull, input)


def test_2636_1():
    """
    `viewBox` in an SVG image should not override `width` and `height`
    https://gitlab.com/graphviz/graphviz/-/issues/2636
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2636_1.dot"
    assert input.exists(), "unexpectedly missing test case"

    # we need to run in our current directory in order to reference the co-located
    # 2636_1.svg
    cwd = Path(__file__).parent

    svg = run("dot", "-Tsvg", input, cwd=cwd)

    # parse the generated SVG
    root = ET.fromstring(svg)

    # find the included image
    imgs = root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(imgs) == 1, "could not find included SVG"
    img = imgs[0]

    assert img.attrib["height"] == "100px", "image height set incorrectly"
    assert img.attrib["width"] == "100px", "image width set incorrectly"


def test_2636_2():
    """
    `viewBox` parameters in an SVG image should be interpreted correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2636
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2636_2.dot"
    assert input.exists(), "unexpectedly missing test case"

    # we need to run in our current directory in order to reference the co-located
    # 2636_2.svg
    cwd = Path(__file__).parent

    svg = run("dot", "-Tsvg", input, cwd=cwd)

    # parse the generated SVG
    root = ET.fromstring(svg)

    # find the included image
    imgs = root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(imgs) == 1, "could not find included SVG"
    img = imgs[0]

    assert img.attrib["height"] == "10px", "image height set incorrectly"
    assert img.attrib["width"] == "10px", "image width set incorrectly"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_2639():
    """
    in GVPR, setting an attribute on a subgraph should not set it on the root graph
    https://gitlab.com/graphviz/graphviz/-/issues/2639
    """

    # locate our associated supporting files in this directory
    input = Path(__file__).parent / "2639.dot"
    assert input.exists(), "unexpectedly missing test case"
    program = Path(__file__).parent / "2639.gvpr"
    assert program.exists(), "unexpectedly missing test case"
    checker = Path(__file__).parent / "2639_2.gvpr"
    assert checker.exists(), "unexpectedly missing test case"

    # process the graph with GVPR
    gvpr_bin = which("gvpr")
    output = run(
        gvpr_bin,
        "-c",
        program.read_text(encoding="utf-8"),
        input=input.read_text(encoding="utf-8"),
    )

    # run this resulting graph through the checker to retrieve one of its root graph’s
    # defaults
    color = run(
        gvpr_bin,
        "-c",
        checker.read_text(encoding="utf-8"),
        "-o",
        os.devnull,
        input=output,
    )

    assert (
        re.search(r"\bred\b", color) is None
    ), "subgraph default was set on root graph"


@pytest.mark.skipif(which("twopi") is None, reason="twopi not available")
def test_2643():
    """
    twopi should not read/write out of bounds when processing this case’s graph
    https://gitlab.com/graphviz/graphviz/-/issues/2643
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2643.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through twopi
    twopi = which("twopi")
    run_raw(twopi, "-o", os.devnull, input)


@pytest.mark.slow  # ~13min
def test_2646():
    """
    Graphviz should not crash when processing this large graph
    https://gitlab.com/graphviz/graphviz/-/issues/2646
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2646.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    dot("pdf", input)


def test_2646_1():
    """
    It was observed that `test_2646` could crash early on when minor changes subtly
    affected the stack depth of functions in lib/common/ns.c. Because `test_2646` is
    expensive, this tries to validate the lack of such crashes with something quicker.
    https://gitlab.com/graphviz/graphviz/-/issues/2646
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2646.dot"
    assert input.exists(), "unexpectedly missing test case"

    # Run this through Graphviz. We expect this long running process to timeout, not
    # crash.
    try:
        run("dot", "-Tpdf", "-o", os.devnull, input, timeout=10)
    except subprocess.TimeoutExpired:
        pass


def test_2647():
    """
    `-Tsvg_inline` should allow references to external files in its output
    https://gitlab.com/graphviz/graphviz/-/issues/2636#note_2326527219
    https://gitlab.com/graphviz/graphviz/-/merge_requests/4208
    https://gitlab.com/graphviz/graphviz/-/issues/2647
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2636.dot"
    assert input.exists(), "unexpectedly missing test case"

    # we need to run in our current directory in order to reference the co-located
    # 2636.svg
    cwd = Path(__file__).parent

    svg = run("dot", "-Tsvg_inline", input, cwd=cwd)

    assert (
        re.search(r"\b2636\.svg\b", svg) is not None
    ), "`-Tsvg_inline` output did not reference external image"


@pytest.mark.slow  # ~10min
def test_MR_2854():
    """
    this graph should be handled in a reasonable amount of time

    The graph used by this test was accelerated by commit
    4b736d297bb1599451e89c2fde911d966a1db3cf landing in Merge Request !2857. This test
    case checks if performance on this workload has regressed since then.

    https://gitlab.com/graphviz/graphviz/-/merge_requests/2854
    https://gitlab.com/graphviz/graphviz/-/merge_requests/2857
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2854.dot"
    assert input.exists(), "unexpectedly missing test case"

    # this typically takes ~10m to run, so give a wide margin of error and require that
    # Graphviz finishes within that
    run_raw("dot", "-Tsvg", "-o", os.devnull, input, timeout=60 * 20)


@pytest.mark.skipif(which("gvgen") is None, reason="gvgen not available")
@pytest.mark.parametrize("seed", range(1, 2000))
def test_2640(seed: int):
    """
    gvgen should not access values out of bounds
    https://gitlab.com/graphviz/graphviz/-/issues/2640
    """

    # the seed 1967 was observed previously to cause crashes on Windows
    gvgen = which("gvgen")
    run_raw(gvgen, "-R", "20", f"-u{seed}", stdout=subprocess.DEVNULL)


@pytest.mark.parametrize(
    "testcase", ("agattr", "agsafeset", "agset", "agstrbind", "agxset")
)
@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_2641(testcase: str, tmp_path: Path):
    """
    `agattr*` and friends should preserve some measure of backwards compatibility
    https://gitlab.com/graphviz/graphviz/-/issues/2641
    """

    # find co-located test source
    c_src = (Path(__file__).parent / f"2641_{testcase}.c").resolve()
    assert c_src.exists(), "missing test case"

    # run it
    run_c(c_src, tmp_path, link=["cgraph"])


def _find_plugin_so(plugin: str) -> Optional[Path]:
    """
    find the absolute path to the dynamic library for a given Graphviz plugin

    Args:
        plugin: Name of the plugin being sought

    Return:
        An absolute path to the corresponding installed dynamic library or `None` if it
        could not be found.
    """

    # figure out the path to installed root based on binaries
    dot_bin = which("dot")
    root = dot_bin.parents[1]

    # extract plugin version
    current, revision, age = plugin_version()

    for subdir in ("lib", "lib64"):
        if is_macos():
            candidate = root / subdir / f"graphviz/libgvplugin_{plugin}.dylib"
        elif is_mingw():
            candidate = root / f"bin/libgvplugin_{plugin}-{current - age}.dll"
        elif platform.system() == "Windows":
            candidate = root / subdir / f"gvplugin_{plugin}.lib"
        else:
            candidate = root / subdir / f"graphviz/libgvplugin_{plugin}.so"
        print(f"checking {candidate}")  # log some useful information
        if candidate.exists():
            return candidate

        if platform.system() == "Linux":
            # try it with the version info suffix, which is what some RHEL platforms use
            suffix = f".{current - age}.{age}.{revision}"
            candidate = root / subdir / f"graphviz/libgvplugin_{plugin}.so{suffix}"
            print(f"checking {candidate}")  # log some useful information
            if candidate.exists():
                return candidate

    # not found
    return None


@pytest.mark.skipif(
    is_static_build(),
    reason="dynamic libraries are unavailable to link against in static builds",
)
def test_2648(tmp_path: Path):
    """
    rendering multiple times programmatically should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/2648
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2648.c").resolve()
    assert c_src.exists(), "missing test case"

    # From here, we essentially want to `run_c(c_src, …)`. However we cannot easily do
    # this because we want to directly link against plugins (instead of `dlopen` them),
    # libraries that are not in the linker’s search path. So instead we have to take a
    # more manual approach.

    # find the plugins we need to link against
    core = _find_plugin_so("core")
    assert core is not None, "core plugin library not found"
    dot_layout = _find_plugin_so("dot_layout")
    assert dot_layout is not None, "dot layout plugin library not found"

    # compile the test code
    exe = tmp_path / "a.exe"
    compile_c(c_src, link=["cgraph", "gvc", core, dot_layout], dst=exe)

    # teach the runtime linker how to find the plugins
    env = os.environ.copy()
    ld_library_path = f"{core.parent}:{dot_layout.parent}"
    prefix = ""
    if is_macos():
        if "DYLD_LIBRARY_PATH" in env:
            env["DYLD_LIBRARY_PATH"] = f"{ld_library_path}:{env['DYLD_LIBRARY_PATH']}"
        else:
            env["DYLD_LIBRARY_PATH"] = ld_library_path
        prefix = f"env DYLD_LIBRARY_PATH={env['DYLD_LIBRARY_PATH']} "
    else:
        if "LD_LIBRARY_PATH" in env:
            env["LD_LIBRARY_PATH"] = f"{ld_library_path}:{env['LD_LIBRARY_PATH']}"
        else:
            env["LD_LIBRARY_PATH"] = ld_library_path
        prefix = f"env LD_LIBRARY_PATH={env['LD_LIBRARY_PATH']} "

    # run the test code
    print(f"+ {prefix}{shlex.quote(str(exe))}")
    subprocess.run([exe], env=env, check=True)


def test_2669():
    """
    `dpi=…` should scale the SVG `viewBox` as well as the overall size
    https://gitlab.com/graphviz/graphviz/-/issues/2669
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2669.dot"
    assert input.exists(), "unexpectedly missing test case"

    def parse(xml: str) -> tuple[int, int, tuple[float, float]]:
        """
        parse an SVG

        Args:
            xml: The text content of an SVG image

        Returns:
            (width, height, (viewBox width, viewBox height))
        """

        root = ET.fromstring(xml)

        assert root.attrib["width"].endswith("pt")
        width = int(root.attrib["width"][:-2])

        assert root.attrib["height"].endswith("pt")
        height = int(root.attrib["height"][:-2])

        viewbox = re.match(
            r"\d+(\.\d+)?\s+\d+(\.\d+)?\s+(?P<width>\d+(\.\d+)?)\s+(?P<height>\d+(\.\d+)?)$",
            root.attrib["viewBox"],
        )
        assert viewbox is not None, "unexpected SVG viewBox format"

        vb_width = float(viewbox.group("width"))
        vb_height = float(viewbox.group("height"))

        return width, height, (vb_width, vb_height)

    # run this through Graphviz as normal
    svg1 = dot("svg", input)

    # confirm the width and height roughly match the `viewBox`
    width, height, viewbox = parse(svg1)
    assert math.isclose(width, viewbox[0], abs_tol=1.0), "mismatched SVG widths"
    assert math.isclose(height, viewbox[1], abs_tol=1.0), "mismatched SVG heights"

    # run this with a modified DPI
    svg2 = run("dot", "-Tsvg", "-Gdpi=60", input)

    # confirm the width and height roughly match the `viewBox`
    width, height, viewbox = parse(svg2)
    assert math.isclose(width, viewbox[0], abs_tol=1.0), "mismatched SVG widths"
    assert math.isclose(height, viewbox[1], abs_tol=1.0), "mismatched SVG heights"


def test_2682():
    """
    processing a graph with `pack` attributes should not cause a crash
    https://gitlab.com/graphviz/graphviz/-/issues/2682
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2682.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("dot", input)


def test_2683():
    """
    processing a graph with `packmode` attributes should not cause a crash
    https://gitlab.com/graphviz/graphviz/-/issues/2683
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2683.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    dot("dot", input)


@pytest.mark.skipif(shutil.which("ps2pdf") is None, reason="ps2pdf not available")
def test_2699():
    """
    `showboxes` should generate a valid PS file
    https://gitlab.com/graphviz/graphviz/-/issues/2699
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2699.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through Graphviz
    ps = dot("ps", input)

    # run this through `ps2pdf`, an arbitrary PS-consuming program to validate what
    # Graphviz gave us
    run_raw("ps2pdf", "-", os.devnull, input=ps)


def test_2705(tmp_path: Path):
    """
    round tripping a graph through a file should not alter its node defaults
    https://gitlab.com/graphviz/graphviz/-/issues/2705
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2705.c").resolve()
    assert c_src.exists(), "missing test case"

    from_memory = tmp_path / "memory.dot"
    from_file = tmp_path / "file.dot"

    # run it
    link = ["cgraph"]
    if is_static_build():
        # in static builds, we also need transitive dependencies
        link += ["cdt"]
    run_c(c_src, tmp_path, args=[from_memory, from_file], link=link)

    original = from_memory.read_text(encoding="utf-8")
    round_tripped = from_file.read_text(encoding="utf-8")
    assert (
        original == round_tripped
    ), "round tripping graph through file was not idempotent"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr is not available")
def test_2707():
    """
    gvpr should not perform a double-free while processing this example
    https://gitlab.com/graphviz/graphviz/-/issues/2707
    """

    # find our test sources
    program = (Path(__file__).parent / "2707.gvpr").resolve()
    assert program.exists(), "missing test case"
    graph = (Path(__file__).parent / "share/unix.gv").resolve()
    assert graph.exists(), "missing test case"

    gvpr_bin = which("gvpr")
    run(gvpr_bin, "-f", program, graph)


def test_2712():
    """
    Graphviz on macOS should not crash while processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2712
    """

    source = 'digraph G { abc [URL=" "]; }'

    # process this to a JPEG
    dot("jpe", source=source)


def test_2716():
    """
    version components should be exposed that work at compile-time
    https://gitlab.com/graphviz/graphviz/-/issues/2716
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "2716.c").resolve()
    assert c_src.exists(), "missing test case"

    # generate a graph and pass it through dot
    compile_c(c_src)


@pytest.mark.skipif(which("fdp") is None, reason="fdp is not available")
@pytest.mark.xfail(
    strict=not is_ndebug_defined(),
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2717",
)
def test_2717():
    """
    processing the given graph with fdp should not crash
    https://gitlab.com/graphviz/graphviz/-/issues/2717
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2717.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through fdp
    fdp = which("fdp")
    run(fdp, "-o", os.devnull, input)


@pytest.mark.skipif(which("osage") is None, reason="osage is not available")
def test_2721():
    """
    osage should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2721
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2721.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through osage
    osage = which("osage")
    run(osage, "-Tpng", "-o", os.devnull, input)


def test_2722():
    """
    the `CDT_VERSION` macro should be updated whenever its API changes
    https://gitlab.com/graphviz/graphviz/-/issues/2722
    """

    # The SHA1 digest of ../lib/cdt/cdt.h. This should be updated whenever you update
    # ../lib/cdt/cdt.h.
    reference = "43c41531381ed1cec4259a08a80dd69b53189100"

    # read in the current cdt.h, accounting for Windows vs Unix line ending differences
    cdt_h = Path(__file__).absolute().parents[1] / "lib/cdt/cdt.h"
    content = cdt_h.read_text(encoding="utf-8")

    # hash the content
    m = hashlib.sha1()
    m.update(content.encode("utf-8"))

    # Check they match. The intent here is that this assertion will fail whenever cdt.h
    # is updated without taking this test case into account. The failure should prompt
    # the developer to update `CDT_VERSION`.
    assert (
        reference == m.hexdigest()
    ), "cdt.h has changed; update test_2722 and remember to update `CDT_VERSION`"


@pytest.mark.xfail(
    strict=which("dot") is not None and is_asan_instrumented(which("dot")),
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2723",
)
def test_2723():
    """
    Graphviz should not crash while processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2723
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2723.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this
    dot("png", input)


def test_2758():
    """
    malformed layout separation should not create an INT_MAX-sized canvas
    https://gitlab.com/graphviz/graphviz/-/issues/2758
    """

    input = Path(__file__).parent / "2758.dot"
    assert input.exists(), "unexpectedly missing test case"

    p = subprocess.run(
        ["dot", "-Kdot", "-Txdot", input],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert p.returncode == 1, "out-of-range nodesep was not rejected"
    assert b"nodesep" in p.stderr, "missing nodesep diagnostic"
    assert (
        re.search(rb"\bAddressSanitizer: heap-buffer-overflow\b", p.stderr) is None
    ), "malformed input caused a buffer overflow"

    bb_match = re.search(rb'\bbb="([^"]+)"', p.stdout)
    assert bb_match is not None, "layout output has no bounding box"
    bb = [float(v) for v in bb_match.group(1).split(b",")]
    assert bb[2] - bb[0] < 10000, "malformed nodesep produced an oversized width"
    assert bb[3] - bb[1] < 10000, "malformed nodesep produced an oversized height"


@pytest.mark.parametrize("attribute", ("nodesep", "ranksep"))
def test_layout_separations_reject_out_of_range_values(attribute: str):
    """
    Dot layout separations are stored as integer points and must reject overflow.
    """

    source = f'digraph {{ graph [{attribute}="40000000d"]; a -> b }}'
    proc = subprocess.run(
        ["dot", "-Kdot", "-Txdot"],
        input=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert proc.returncode == 1, f"out-of-range {attribute} was not rejected"
    assert attribute in proc.stderr, f"missing {attribute} diagnostic"
    bb_match = re.search(r'\bbb="([^"]+)"', proc.stdout)
    assert bb_match is not None, "layout output has no bounding box"
    bb = [float(v) for v in bb_match.group(1).split(",")]
    assert bb[2] - bb[0] < 10000, f"{attribute} produced an oversized width"
    assert bb[3] - bb[1] < 10000, f"{attribute} produced an oversized height"


def test_2727():
    """
    the label “<>” should be accepted
    https://gitlab.com/graphviz/graphviz/-/issues/2727
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2727.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this
    dot("svg", input)


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr is not available")
def test_2731():
    """
    gvpr should produce output for this example
    https://gitlab.com/graphviz/graphviz/-/issues/2731
    """

    # find our test source
    graph = (Path(__file__).parent / "graphs/unix.gv").resolve()

    gvpr_bin = which("gvpr")
    result = run(gvpr_bin, "-c", 'N{label="\\N";}', graph)

    assert result.strip() != "", "gvpr output missing"


@pytest.mark.parametrize(
    "fmt",
    (
        "png",
        pytest.param(
            "png:cairo:gdk",
            marks=pytest.mark.skipif(
                is_macos() or platform.system() == "Windows",
                reason="GDK plugin not supported",
            ),
        ),
        pytest.param(
            "jpg",
            marks=pytest.mark.xfail(
                is_fedora() or is_ubuntu_2604(),
                strict=True,
                reason="https://gitlab.com/graphviz/graphviz/-/issues/2732",
            ),
        ),
        pytest.param(
            "jpg:cairo:gd",
            marks=pytest.mark.skipif(
                is_cmake() and is_mingw(),
                reason="libgd not detected on CMake+MinGW",
            ),
        ),
    ),
)
def test_2732(fmt: str):
    """
    back ends should not produce empty files
    https://gitlab.com/graphviz/graphviz/-/issues/2732

    Args:
        fmt: Format to pass to dot’s `-T…`.
    """

    # an arbitrary, trivial graph
    source = "graph G { a -- b; }"

    # confirm this produces non-empty output
    output = dot(fmt, source=source)
    assert output != b"", "empty output produced for valid graph"


def test_2734():
    """
    this graph should not be drawn with sharply angled curves
    https://gitlab.com/graphviz/graphviz/-/issues/2734
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2734.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this
    svg = dot("svg", input)

    # parse the SVG
    root = ET.fromstring(svg)

    # look at each path
    for path in root.findall(".//{http://www.w3.org/2000/svg}path"):
        # get the definition and make it slightly easier to parse
        d = path.get("d")
        points_str = d.replace("C", " ").replace("M", " ")

        # parse it into a list of points
        points = [
            (float(x), float(y))
            for x, y in [p.split(",") for p in points_str.split(" ") if p]
        ]

        # examine the angles along the path, looking for an abnormally large one
        gradient: Optional[float] = None
        last_point: Optional[tuple[float, float]] = None
        for p in points:
            if last_point is None:
                last_point = p
                continue
            # for simplicity, skip paths with vertical lines because we know the
            # problematic one we are scanning for does not have any
            if p[0] == last_point[0]:
                break
            this_gradient = (p[1] - last_point[1]) / (p[0] - last_point[0])
            if gradient is not None:
                angle = math.atan(
                    (gradient - this_gradient) / (1 + gradient * this_gradient)
                )
                assert math.degrees(angle) < 10, "unnecessarily sharp edge generated"
            gradient = this_gradient


def test_2743():
    """
    Graphviz should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2743
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2743.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    dot("dot", src)


def test_2757():
    """
    Graphviz should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2757
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2757.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz and inspect geometry to prove layout was produced
    # (not just that process did not crash)
    layout = dot("json", src)
    data = json.loads(layout)

    # Ensure the output contains positioned graph objects.
    node_positions = [obj["pos"] for obj in data["objects"] if "pos" in obj]
    assert node_positions, "layout output has no node coordinates"

    for pos in node_positions:
        x, y = (float(v) for v in pos.split(","))
        assert math.isfinite(x), "non-finite node x coordinate"
        assert math.isfinite(y), "non-finite node y coordinate"

    # Ensure at least one edge has an actual polyline with finite points.
    edge_points = [
        segment["points"]
        for edge in data["edges"]
        for segment in edge["_draw_"]
        if "points" in segment
    ]
    assert edge_points, "layout output has no routed edges"

    for points in edge_points:
        for x, y in points:
            assert math.isfinite(float(x)), "non-finite edge x coordinate"
            assert math.isfinite(float(y)), "non-finite edge y coordinate"

    # The bounding box should be non-empty.
    bb = [float(v) for v in data["bb"].split(",")]
    assert bb[2] > bb[0], "invalid or collapsed bounding box (x)"
    assert bb[3] > bb[1], "invalid or collapsed bounding box (y)"


def test_2760():
    """
    Graphviz should successfully lay out this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2760
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2760.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    output = dot("dot", src)

    # a successful result must include a completed layout
    assert "pos=" in output


@pytest.mark.parametrize("issue", [2759, 2762, 2766])
def test_rankset_cluster_malformed_no_crash(issue: int):
    """
    Malformed ranksets inside clusters should fail gracefully rather than
    dereferencing empty cluster or rank structures.
    https://gitlab.com/graphviz/graphviz/-/issues/2759
    https://gitlab.com/graphviz/graphviz/-/issues/2762
    https://gitlab.com/graphviz/graphviz/-/issues/2766
    """

    src = Path(__file__).parent / f"{issue}.dot"
    assert src.exists(), "unexpectedly missing test case"

    try:
        dot("dot", src)
    except subprocess.CalledProcessError as e:
        # allow a diagnostic failure for malformed input; only fail on a crash
        if e.returncode != 1:
            raise


@pytest.mark.xfail(
    raises=subprocess.CalledProcessError,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2778",
    strict=which("dot") is not None and is_asan_instrumented(which("dot")),
)
def test_2778():
    """
    Graphviz should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2778
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2778.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    try:
        dot("dot", src)
    except subprocess.CalledProcessError as e:
        # only fail if we crashed, not exited with failure
        if e.returncode != 1:
            raise


def test_2781():
    """
    Graphviz should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2781
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2781.dot"
    assert src.exists(), "unexpectedly missing test case"

    # malformed input should be rejected without overflowing while processing
    proc = subprocess.run(["dot", "-Tdot", "-o", os.devnull, src], stderr=subprocess.PIPE)

    assert proc.returncode == 1, "invalid input was not rejected"
    assert (
        re.search(rb"\bAddressSanitizer: heap-buffer-overflow\b", proc.stderr) is None
    ), "malformed input caused a buffer overflow"


def test_2781_negative_control():
    """
    Well-formed adjacent flat splines should still lay out normally
    """

    src = """
    digraph {
      graph [rankdir=LR]
      { rank=same; a; b }
      a -> b [label="x"]
      b -> a [label="y"]
    }
    """

    proc = subprocess.run(
        ["dot", "-Tdot", "-o", os.devnull],
        input=textwrap.dedent(src),
        text=True,
        stderr=subprocess.PIPE,
        check=True,
    )

    stderr = remove_asan_summary(remove_xtype_warnings(proc.stderr)).strip()
    assert stderr == "", "legal adjacent flat splines produced warnings"


def test_2782():
    """
    Graphviz should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2782
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2782.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    try:
        dot("dot", src)
    except subprocess.CalledProcessError as e:
        # allow failure; only fail this test case on a crash
        if e.returncode != 1:
            raise


@pytest.mark.xfail(
    platform.system() == "Windows",
    raises=subprocess.CalledProcessError,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2784",
    strict=which("dot") is not None and is_asan_instrumented(which("dot")),
)
def test_2784():
    """
    Graphviz should not crash when processing this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2784
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2784.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    try:
        dot("dot", src)
    except subprocess.CalledProcessError as e:
        # allow failure; only fail this test case on a crash
        if e.returncode != 1:
            raise


@pytest.mark.xfail(
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2796", strict=True
)
def test_2796():
    """
    Graphviz should be able to triangulate the points in this graph
    https://gitlab.com/graphviz/graphviz/-/issues/2796
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2796.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this
    p = subprocess.run(
        ["dot", "-Tpdf", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )

    assert (
        re.search(r"\btrouble in init_rank\b", p.stderr) is None
    ), "triangulation failed"


@pytest.mark.skipif(not is_cmake(), reason="only relevant in CMake builds")
@pytest.mark.skipif(is_static_build(), reason="only relevant in shared library builds")
def test_2798(tmp_path: Path):
    """
    are the CMake exported configs enough to build the examples?
    https://gitlab.com/graphviz/graphviz/-/issues/2798
    """

    # a directory containing a CMake file that describes building the examples
    src = Path(__file__).parent / "2798"

    # suppress any compiler path tweaks because we do not want to be able to see paths
    # in the Graphviz source tree
    env = os.environ.copy()
    for v in (
        "CPATH",  # GNU CPP
        "C_INCLUDE_PATH",  # GNU CPP
        "CPLUS_INCLUDE_PATH",  # GNU CPP
        "OBJC_INCLUDE_PATH",  # GNU CPP
        "LIBRARY_PATH",  # GNU LD
        "LD_LIBRARY_PATH",  # *nix ld.so
        "DYLD_LIBRARY_PATH",  # macOS dyld
        "INCLUDE",  # MSVC
        "LIB",  # MSVC
        "LIBPATH",  # MSVC
    ):
        if v in env:
            del env[v]

    # configure this
    args = ["cmake", "-B", tmp_path, "-S", src]
    if platform.system() == "Windows" and not is_mingw():
        args += ["-A", os.environ["project_platform"]]
    run(*args, env=env)

    # compile the examples
    args = ["cmake", "--build", tmp_path, "--parallel=1", "--verbose"]
    if platform.system() == "Windows" and not is_mingw():
        # Windows decides which C Run-Time (CRT) library to link based on the
        # configuration, which must be the same our dependencies were linked against
        args += [f"--config={os.environ['configuration']}"]
    run(*args, env=env)


def test_2801():
    """
    `colorscheme` should work correctly
    https://gitlab.com/graphviz/graphviz/-/issues/2801
    """

    # locate our associated test case in this directory
    input = Path(__file__).parent / "2801.dot"
    assert input.exists(), "unexpectedly missing test case"

    # process this
    warnings = run("dot", "-Tpng", "-o", os.devnull, input, stderr=subprocess.STDOUT)

    assert "is not a known color" not in warnings, "`colorscheme` not working"


def test_2825():
    """
    Graphviz should not crash when `rebuild_vlists` returns -1
    https://gitlab.com/graphviz/graphviz/-/issues/2825
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2825.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    try:
        dot("png", src)
    except subprocess.CalledProcessError as e:
        # only fail if we crashed, not exited with failure
        if e.returncode != 1:
            raise


def test_2827(tmp_path: Path):
    """
    page number state should not be carried across between unrelated graphs
    https://gitlab.com/graphviz/graphviz/-/issues/2827
    """

    # write two identical trivial graphs
    src1 = tmp_path / "a.dot"
    src1.write_text("graph { a -- b; }", encoding="utf-8")
    src2 = tmp_path / "b.dot"
    src2.write_text("graph { a -- b; }", encoding="utf-8")

    # translate these to SVG
    run("dot", "-Tsvg", "-O", src1, src2)

    # the output images should be identical
    out1 = tmp_path / "a.dot.svg"
    svg1 = out1.read_text(encoding="utf-8")
    out2 = tmp_path / "b.dot.svg"
    svg2 = out2.read_text(encoding="utf-8")
    assert svg1 == svg2, "state from one graph carried to another"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr is not available")
@pytest.mark.xfail(
    raises=AssertionError,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2835",
    strict=True,
)
def test_2835():
    """
    gvpr should apply cluster attributes to the correct graph, not the root graph
    https://gitlab.com/graphviz/graphviz/-/issues/2835
    """

    # find our test sources
    program = Path(__file__).parent / "2835.gvpr"
    graph = Path(__file__).parent / "2835.dot"

    # run this through gvpr
    gvpr_bin = which("gvpr")
    output = run(gvpr_bin, "-f", program, graph)

    # Now extract various text chunks from the final graph. Doing this with regexes is
    # not reliable, but we do not want to depend on dot itself for this in case another
    # bug masks what we are trying to validate.

    graph_start = re.search(
        r"/+ final graph /+\n(?P<graph>(.|\n)+)", output, flags=re.MULTILINE
    )
    assert graph_start is not None, "failed to find final graph output"
    graph = graph_start.group("graph")

    first_cluster = re.search(r"\bsubgraph cluster", graph).start()

    graph_color = re.search(r"\bcolor\s*=[^,\]]*", graph[:first_cluster])
    assert graph_color is None, "color attribute was added to the root graph"

    graph_style = re.search(r'\bstyle\s*="[^"]*"', graph[:first_cluster])
    assert graph_style is None, "style attribute was added to the root graph"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr is not available")
def test_2849():
    """
    gvpr should not infinite-loop on this input
    https://gitlab.com/graphviz/graphviz/-/work_items/2849
    """

    # find our test sources
    program = Path(__file__).parent / "2849.gvpr"
    graph = Path(__file__).parent / "2849.dot"

    # run this through gvpr
    gvpr_bin = which("gvpr")
    run_raw(gvpr_bin, "-f", program, graph, timeout=10)


def test_2851():
    """
    Graphviz should handle `topsort` situations without crashing
    https://gitlab.com/graphviz/graphviz/-/work_items/2851
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2851.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    dot("dot", src)


@pytest.mark.skipif(which("sfdp") is None, reason="sfdp not available")
def test_2852():
    """
    Graphviz should not crash when processing this input
    https://gitlab.com/graphviz/graphviz/-/work_items/2852
    https://forum.graphviz.org/t/sfdp-crash-regression-in-15-1-1/3392
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2852.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through sfdp
    sfdp = which("sfdp")
    p = subprocess.run(
        [sfdp, "-Tpng", "-o", os.devnull, src],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    # if sfdp was built without libgts, it will not handle anything non-trivial
    no_gts_error = "remove_overlap: Graphviz not built with triangulation library"
    if no_gts_error in p.stderr:
        assert p.returncode != 0, "sfdp returned success after an error message"
        return

    p.check_returncode()


@pytest.mark.xfail(
    raises=subprocess.SubprocessError,
    reason="https://gitlab.com/graphviz/graphviz/-/issues/2854",
    strict=not is_ndebug_defined(),
)
def test_2854():
    """
    Graphviz should not crash when processing this graph
    https://forum.graphviz.org/t/bug-report-clustered-subgraph-leads-to-crash-in-dot/3395
    https://gitlab.com/graphviz/graphviz/-/issues/2854
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2854_1.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    run("dot", "-o", os.devnull, "-Tdot", src, timeout=60)


def test_2855():
    """
    with `ordering="in"`, `nop` should not reorder edges
    https://gitlab.com/graphviz/graphviz/-/issues/2855
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "2855.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through `nop`
    out = run("nop", src)

    # extract edges in order from the input
    reference: list[str] = []
    regex = r"\b(?P<from>[a-zA-Z_]\w*)\s*->\s*(?P<to>[a-zA-Z_]\w*)\b"
    for f, t in re.findall(regex, src.read_text(encoding="utf-8")):
        reference += [f"{f}->{t}"]

    # extract edges in order from the output
    seen: list[str] = []
    for f, t in re.findall(regex, out):
        seen += [f"{f}->{t}"]

    assert reference == seen, '`nop` does not respect `ordering="in"`'


def test_2857_1(tmp_path: Path):
    """
    `cluster` should not crash when processing a large chained graph
    https://gitlab.com/graphviz/graphviz/-/issues/2857
    """

    # create a large chain of nodes
    src = tmp_path / "src.dot"
    with open(src, "wt", encoding="utf-8") as f:
        f.write("graph G {\n")
        for i in range(20000):
            f.write(f"n{i} -- n{(i + 1) % 20000};\n")
        f.write("}\n")

    # run this through `cluster`
    cluster = which("cluster")
    try:
        run(cluster, "-C", "0", "-c", "1", src)
    except subprocess.CalledProcessError as e:
        # only fail if we crashed, not exited with failure
        if e.returncode != 1:
            raise


def test_2857_2():
    """
    `cluster` should not crash when processing a degenerate graph
    https://gitlab.com/graphviz/graphviz/-/issues/2857
    """

    # a degenerate graph
    src = "graph G {}"

    # run this through `cluster`
    cluster = which("cluster")
    try:
        run(cluster, input=src)
    except subprocess.CalledProcessError as e:
        # only fail if we crashed, not exited with failure
        if e.returncode != 1:
            raise


def test_2857_3():
    """
    `cluster` should not crash when given a malformed `-C` option
    https://gitlab.com/graphviz/graphviz/-/issues/2857
    """

    # run `cluster` without a parameter to `-C`
    cluster = which("cluster")
    try:
        run(cluster, "-C")
    except subprocess.CalledProcessError as e:
        # only fail if we crashed, not exited with failure
        if e.returncode != 1:
            raise


def test_2857_4(tmp_path: Path):
    """
    `edgepaint` should not crash when given malformed options
    https://gitlab.com/graphviz/graphviz/-/issues/2857
    """

    # run `edgepaint` with malformed options
    edgepaint = which("edgepaint")
    try:
        run(
            edgepaint, "-o", "--lightness", "-s", "-v", "--share_endpoint", cwd=tmp_path
        )
    except subprocess.CalledProcessError as e:
        # only fail if we crashed, not exited with failure
        if e.returncode != 1:
            raise


def test_698066():
    """
    Graphviz should not crash when processing this graph
    https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=698066
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "698066.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    dot("dot", src)


def test_698066_2():
    """
    Graphviz should not crash when processing this graph
    https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=698066
    """
    src = r'digraph g {v [label="fmnosyy cemnnoopt definr\Einoprx\Eeionprssx  aceert"]}'
    dot("dot", source=src)


def test_700813():
    """
    Graphviz should not crash when processing this graph
    https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=700813
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "700813.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    dot("dot", src)


def test_894589():
    """
    Graphviz should not crash when processing this graph
    https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=894589
    """

    # locate our associated test case in this directory
    src = Path(__file__).parent / "894589.dot"
    assert src.exists(), "unexpectedly missing test case"

    # run this through Graphviz
    dot("svg", src)


@pytest.mark.parametrize("package", ("Tcldot", "Tclpathplan"))
@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
@pytest.mark.xfail(
    is_autotools() and is_macos(),
    reason="Autotools on macOS does not detect TCL",
    strict=True,
)
def test_import_tcl_package(package: str):
    """
    The given TCL package should be loadable
    """

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # ask TCL to import the given package
    response = run(
        "tclsh",
        stderr=subprocess.STDOUT,
        input=f"package require {package};",
        env=env,
    )

    assert "can't find package" not in response, f"{package} cannot be loaded by TCL"


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="pexpect.spawn is not available on Windows "
    "(https://pexpect.readthedocs.io/en/stable/overview.html#pexpect-on-windows)",
)
@pytest.mark.xfail(
    is_cmake() and is_macos(),
    reason="FIXME: 'vgpane' command is unrecognized for unknown reasons",
    strict=True,
)
@pytest.mark.xfail(
    is_autotools() and is_macos(),
    reason="Autotools on macOS does not detect TCL",
    strict=True,
)
def test_triangulation_overflow():
    """
    running Tclpathplan `triangulate` with a malformed polygon should be rejected
    """

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # startup TCL and load the pathplan module
    proc = pexpect_spawn_tclsh(timeout=1, env=env)
    proc.expect("% ")
    proc.sendline("package require Tclpathplan")
    proc.expect("% ")

    # Create a pane. We assume the first created pane will be index 0, though
    # this is not technically required.
    proc.sendline("vgpane")
    proc.expect("vgpane0")
    proc.expect("% ")

    # add a “polygon” with only a single point
    proc.sendline("vgpane0 insert 4 5")
    proc.expect("1")
    proc.expect("% ")

    # attempt triangulation on this polygon
    proc.sendline("vgpane0 triangulate 1")
    proc.expect("cannot be triangulated")
    proc.expect("% ")

    # delete the pane to clean up, to exit ASan-clean
    proc.sendline("vgpane0 delete")
    proc.expect("% ")


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="pexpect.spawn is not available on Windows "
    "(https://pexpect.readthedocs.io/en/stable/overview.html#pexpect-on-windows)",
)
@pytest.mark.xfail(
    is_cmake() and is_macos(),
    reason="FIXME: 'vgpane' command is unrecognized for unknown reasons",
    strict=True,
)
@pytest.mark.xfail(
    is_autotools() and is_macos(),
    reason="Autotools on macOS does not detect TCL",
    strict=True,
)
def test_vgpane_bad_triangulation():
    """
    running Tclpathplan `triangulate` with incorrect arguments should be rejected
    """

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # startup TCL and load the pathplan module
    proc = pexpect_spawn_tclsh(timeout=1, env=env)
    proc.expect("% ")
    proc.sendline("package require Tclpathplan")
    proc.expect("% ")

    # Create a pane. We assume the first created pane will be index 0, though
    # this is not technically required.
    proc.sendline("vgpane")
    proc.expect("vgpane0")
    proc.expect("% ")

    # bind the triangulation callback to something ending in a trailing '%'
    proc.sendline("vgpane0 bind triangle %")
    proc.expect("% ")

    # run triangulation with no polygon ID, which should be rejected
    proc.sendline("vgpane0 triangulate")
    proc.expect("wrong # args")

    # delete the pane to clean up, to exit ASan-clean
    proc.sendline("vgpane0 delete")
    proc.expect("% ")


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="tclsh not available")
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="pexpect.spawn is not available on Windows "
    "(https://pexpect.readthedocs.io/en/stable/overview.html#pexpect-on-windows)",
)
@pytest.mark.xfail(
    is_cmake() and is_macos(),
    reason="FIXME: 'vgpane' command is unrecognized for unknown reasons",
    strict=True,
)
@pytest.mark.xfail(
    is_autotools() and is_macos(),
    reason="Autotools on macOS does not detect TCL",
    strict=True,
)
def test_vgpane_delete():
    """
    it should be possible to delete an existing `vgpane`
    """

    # if this appears to be an ASan-enabled CI job, teach `tclsh` to load ASan’s
    # supporting library because it is otherwise unaware that Tcldot depends on this
    # being loaded first
    env = os.environ.copy()
    dot_exe = which("dot")
    if is_asan_instrumented(dot_exe):
        cc = os.environ.get("CC", "gcc")
        libasan = run(cc, "-print-file-name=libasan.so").strip()
        print(f"setting LD_PRELOAD={libasan}")
        env["LD_PRELOAD"] = libasan

    # startup TCL and load the pathplan module
    proc = pexpect_spawn_tclsh(timeout=1, env=env)
    proc.expect("% ")
    proc.sendline("package require Tclpathplan")
    proc.expect("% ")

    # Create a pane. We assume the first created pane will be index 0, though
    # this is not technically required.
    proc.sendline("vgpane")
    proc.expect("vgpane0")
    proc.expect("% ")

    # delete the pane to clean up
    proc.sendline("vgpane0 delete")
    # `pexpect.expect` returns an index of which given expectation was matched. We
    # expect this to return no output (not the invalid handle message) and therefore
    # timeout.
    is_valid = proc.expect(['Invalid handle: "vgpane0"', pexpect.TIMEOUT]) == 1
    assert is_valid, "created vgpane was considered an invalid handle"


def test_changelog_dates():
    """
    Check the dates of releases in the changelog are correctly formatted
    """
    changelog = Path(__file__).parent / "../CHANGELOG.md"
    with open(changelog, "rt", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            m = re.match(r"## \[\d+\.\d+\.\d+\] [-–] (?P<date>.*)$", line)
            if m is None:
                continue
            d = re.match(r"\d{4}-\d{2}-\d{2}", m.group("date"))
            assert (
                d is not None
            ), f"CHANGELOG.md:{lineno}: date in incorrect format: {line}"


@pytest.mark.skipif(which("gvpack") is None, reason="gvpack not available")
def test_duplicate_hard_coded_metrics_warnings():
    """
    Check “no hard-coded metrics” warnings are not repeated
    """

    # use the #2239 test case that happens to provoke this
    input = Path(__file__).parent / "2239.dot"
    assert input.exists(), "unexpectedly missing test case"

    # run it through gvpack
    gvpack = which("gvpack")
    p = subprocess.run(
        [gvpack, "-u", "-o", os.devnull, input],
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert (
        p.stderr.count("no hard-coded metrics for 'sans'") <= 1
    ), "multiple identical “no hard-coded metrics” warnings printed"


@pytest.mark.parametrize("branch", (0, 1, 2, 3))
@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_gvpr_switches(branch: int):
    """
    confirm the behavior of GVPR switch statements
    """

    # an input GVPR program with multiple blocks and switches
    program = textwrap.dedent(
        f"""\
    BEGIN {{
      switch ({branch}) {{
        case 0:
          printf("begin 0\\n");
          break;
        case 1:
          printf("begin 1\\n");
          break;
        case 2:
          printf("begin 2\\n");
          break;
        default:
          printf("begin 3\\n");
          break;
      }}
    }}

    END {{
      switch ({branch}) {{
        case 0:
          printf("end 0\\n");
          break;
        case 1:
          printf("end 1\\n");
          break;
        case 2:
          printf("end 2\\n");
          break;
        default:
          printf("end 3\\n");
          break;
      }}
    }}
    """
    )

    # run this through GVPR with no input graph
    gvpr_bin = which("gvpr")
    result = run(gvpr_bin, program, stdin=subprocess.DEVNULL)

    # confirm we got the expected output
    assert result == f"begin {branch}\nend {branch}\n", "incorrect GVPR switch behavior"


@pytest.mark.parametrize(
    "statement,expected",
    (
        ('printf("%d", 5)', "5"),
        ('printf("%d", 0)', "0"),
        ('printf("%.0d", 0)', ""),
        ('printf("%.0d", 1)', "1"),
        ('printf("%.d", 2)', "2"),
        ('printf("%d", -1)', "-1"),
        ('printf("%.3d", 5)', "005"),
        ('printf("%.3d", -5)', "-005"),
        ('printf("%5.3d", 5)', "  005"),
        ('printf("%-5.3d", -5)', "-005 "),
        ('printf("%-d", 5)', "5"),
        ('printf("%-+d", 5)', "+5"),
        ('printf("%+-d", 5)', "+5"),
        ('printf("%+d", -5)', "-5"),
        ('printf("% d", 5)', " 5"),
        ('printf("% .0d", 0)', " "),
        ('printf("%03d", 5)', "005"),
        ('printf("%03d", -5)', "-05"),
        ('printf("% +d", 5)', "+5"),
        ('printf("%-03d", -5)', "-5 "),
        ('printf("%o", 5)', "5"),
        ('printf("%o", 8)', "10"),
        ('printf("%o", 0)', "0"),
        ('printf("%.0o", 0)', ""),
        ('printf("%.0o", 1)', "1"),
        ('printf("%.3o", 5)', "005"),
        ('printf("%.3o", 8)', "010"),
        ('printf("%5.3o", 5)', "  005"),
        ('printf("%u", 5)', "5"),
        ('printf("%u", 0)', "0"),
        ('printf("%.0u", 0)', ""),
        ('printf("%.0u", 1)', "1"),
        ('printf("%.3u", 5)', "005"),
        ('printf("%5.3u", 5)', "  005"),
        ('printf("%u", 5)', "5"),
        ('printf("%u", 0)', "0"),
        ('printf("%.0u", 0)', ""),
        ('printf("%.0u", 1)', "1"),
        ('printf("%.3u", 5)', "005"),
        ('printf("%5.3u", 5)', "  005"),
        ('printf("%-x", 5)', "5"),
        ('printf("%03x", 5)', "005"),
        ('printf("%-x", 5)', "5"),
        ('printf("%03x", 5)', "005"),
        ('printf("%-X", 5)', "5"),
        ('printf("%03X", 5)', "005"),
        ('printf("%.2s", "abc")', "ab"),
        ('printf("%.6s", "abc")', "abc"),
        ('printf("%5s", "abc")', "  abc"),
        ('printf("%-5s", "abc")', "abc  "),
        ('printf("%5.2s", "abc")', "   ab"),
        ('printf("%%")', "%"),
    ),
)
@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_gvpr_printf(statement: str, expected: str):
    """
    check various behaviors of `printf` in a GVPR program
    """

    # a program that performs the given `printf`
    program = f"BEGIN {{ {statement}; }}"

    # run this through GVPR with no input graph
    gvpr_bin = which("gvpr")
    result = run(gvpr_bin, program, stdin=subprocess.DEVNULL)

    # confirm we got the expected output
    assert result == expected, "incorrect GVPR printf behavior"


usage_info = """\
Usage: dot [-Vv?] [-(GNEA)name=val] [-(KTlso)<val>] <dot files>
(additional options for neato)    [-x] [-n<v>]
(additional options for fdp)      [-L(gO)] [-L(nUCT)<val>]
(additional options for config)  [-cv]

 -V          - Print version and exit
 -v[n]       - Enable verbose mode at level 1, or set to the level 'n' if provided
 -Gname=val  - Set graph attribute 'name' to 'val'
 -Nname=val  - Set node attribute 'name' to 'val'
 -Ename=val  - Set edge attribute 'name' to 'val'
 -Aname=val  - Set attribute 'name' to 'val' for graph, node, and edge
 -Tv         - Set output format to 'v'
 -Kv         - Set layout engine to 'v' (overrides default based on command name)
 -lv         - Use external library 'v'
 -ofile      - Write output to 'file'
 -O          - Automatically generate an output filename based on the input filename with a .'format' appended. (Causes all -ofile options to be ignored.) 
 -P          - Internally generate a graph of the current plugins. 
 -q[l]       - Set level of message suppression (=1)
 -s[v]       - Scale input by 'v' (=72)
 -y          - Invert y coordinate in output

 -n[v]       - No layout mode 'v' (=1)
 -x          - Reduce graph

 -Lg         - Don't use grid
 -LO         - Use old attractive force
 -Lm<l>      - Set m-limit to l
 -Ln<i>      - Set number of iterations to i
 -LU<i>      - Set unscaled factor to i
 -LC<v>      - Set overlap expansion factor to v
 -LT[*]<v>   - Set temperature (temperature factor) to v

 -c          - Configure plugins (Writes $prefix/lib/graphviz/config 
               with available plugin information.  Needs write privilege.)
 -?          - Print usage and exit
"""


def test_dot_questionmarkV():
    """
    test the output from two short options combined
    """

    out = run("dot", "-?V")

    assert out == usage_info, "unexpected usage info"


def test_dot_randomV():
    """
    test the output from a malformed command
    """

    expected = f"Error: dot: option -r unrecognized\n\n{usage_info}"

    proc = subprocess.run(
        ["dot", "-randomV"],
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert proc.returncode != 0, "malformed options were accepted"

    assert proc.stderr == expected, "unexpected usage info"


def test_dot_V(tmp_path: Path):
    """
    test the output from `dot -V`
    """

    proc = subprocess.run(["dot", "-V"], stderr=subprocess.PIPE, text=True, check=True)

    c_src = (Path(__file__).parent / "get-package-version.c").resolve()
    assert c_src.exists(), "missing test case"
    package_version, _ = run_c(c_src, tmp_path)

    assert proc.stderr.startswith(
        f"dot - graphviz version {package_version.strip()} ("
    ), "unexpected -V info"


def test_dot_Vquestionmark(tmp_path: Path):
    """
    test the output from two short options combined
    """

    proc = subprocess.run(["dot", "-V?"], stderr=subprocess.PIPE, text=True, check=True)

    c_src = (Path(__file__).parent / "get-package-version.c").resolve()
    assert c_src.exists(), "missing test case"
    package_version, _ = run_c(c_src, tmp_path)

    assert proc.stderr.startswith(
        f"dot - graphviz version {package_version.strip()} ("
    ), "unexpected -V info"


def test_dot_Vrandom(tmp_path: Path):
    """
    test the output from a short option mixed with long
    """

    proc = subprocess.run(
        ["dot", "-Vrandom"], stderr=subprocess.PIPE, text=True, check=True
    )

    c_src = (Path(__file__).parent / "get-package-version.c").resolve()
    assert c_src.exists(), "missing test case"
    package_version, _ = run_c(c_src, tmp_path)

    assert proc.stderr.startswith(
        f"dot - graphviz version {package_version.strip()} ("
    ), "unexpected -V info"


def test_pic_font_size():
    """
    font size in PIC output format should not be clamped down to 1
    related to https://gitlab.com/graphviz/graphviz/-/issues/2487
    """

    # run a basic graph through PIC generation
    src = "graph { a -- b; }"
    pic = dot("pic", source=src)

    # confirm we got a non-1 font size
    m = re.search(r"^\.ps (\d+)", pic, flags=re.MULTILINE)
    assert int(m.group(1)) > 1, "font size clamped down to 1"


@pytest.mark.skipif(which("mm2gv") is None, reason="mm2gv not available")
def test_mm_banner_overflow(tmp_path: Path):
    """mm2gv should be robust against files with a corrupted banner"""

    # construct a file with a corrupted banner > MM_MAX_TOKEN_LENGTH and < MM_MAX_LINE_LENGTH
    mm = tmp_path / "matrix.mm"
    mm.write_text(f"%{'a' * 10000}", encoding="utf-8")

    # run this through mm2gv
    mm2gv = which("mm2gv")
    ret = subprocess.call([mm2gv, "-o", os.devnull, mm])

    assert ret in (0, 1), "mm2gv crashed when processing malformed input"
    assert ret == 1, "mm2gv did not reject malformed input"


def test_control_characters_in_error():
    """
    malformed input should not result in misleading control data making it to the
    output terminal unfiltered
    """

    # Run something through Graphviz that will trigger an error where the error message
    # will contain a color control sequence. This could be used to disrupt the user’s
    # terminal in confusing ways.
    src = 'graph { a[image="\033[31mfoo"]; }'
    ret = subprocess.run(
        ["dot", "-Tsvg", "-o", os.devnull],
        input=src,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert "\033" not in ret.stderr, "control character appears in error message"

    # Now try something more malicious. Use the backspace character to display a different
    # filename in the error message to what was referenced.
    src = 'graph { a[image="foo.svg\010\010\010png"]; }'
    ret = subprocess.run(
        ["dot", "-Tsvg", "-o", os.devnull],
        input=src,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert "\010" not in ret.stderr, "control character appears in error message"


def test_fig_max_colors():
    """
    using a large number of colors should not crash the FIG renderer
    """

    # contruct a graph that uses well over 256 colors
    buf = io.StringIO()
    buf.write("graph {\n")
    for red in range(256):
        for green in range(10):
            buf.write(f'  n_{red}_{green}[color="#{red:02x}{green:02x}00"];\n')
    buf.write("}\n")

    # render this using the FIG renderer
    dot("fig", source=buf.getvalue())


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_gvpr_s2f():
    """
    casting a string to floating point in GVPR should work
    """

    # a GVPR program that casts a string to floating point and prints the result
    program = 'BEGIN { float x = (float)"1.5"; printf("%0.1f\\n", x); }'

    # run this through GVPR with no input graph
    gvpr_bin = which("gvpr")
    result = run(gvpr_bin, program, stdin=subprocess.DEVNULL)

    # confirm we got the expected output
    assert result == "1.5\n", "incorrect GVPR float cast behavior"


def test_changelog():
    """
    sanity checks on ../CHANGELOG.md
    """

    changelog = Path(__file__).parent / "../CHANGELOG.md"
    assert changelog.exists(), "CHANGELOG.md missing"

    with open(changelog, "rt", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            ignore_h2 = False

            # an exception for an old heading
            if line == "## [2.42.3] and earlier\n":
                ignore_h2 = True

            # an exception for unreleased versions
            if line.startswith("## ") and "Unreleased" in line:
                ignore_h2 = True

            if (m := re.match("##(?P<remainder>[^#].*)$", line)) and not ignore_h2:
                expected_format = r" \[\d+\.\d+\.\d+\] [\-–] \d{4}-\d{2}-\d{2}$"
                assert re.match(expected_format, m.group("remainder")), (
                    f"CHANGELOG.md:{lineno}: second-level heading did not match "
                    f'regex r"{expected_format}": {line}'
                )

            if m := re.match("###(?P<remainder>.*)$", line):
                assert m.group("remainder") in (
                    " Added",
                    " Changed",
                    " Fixed",
                    " Removed",
                ), f"CHANGELOG.md:{lineno}: unexpected third-level heading: {line}"

            if m := re.match(
                r"\[(?P<version>\d+\.\d+\.\d+)\]:(?P<remainder>.*)$", line
            ):
                prefix = " https://gitlab.com/graphviz/graphviz/compare/"
                assert m.group("remainder").startswith(
                    prefix
                ), f"CHANGELOG.md:{lineno}: unexpected finalized history link: {line}"
                remainder = m.group("remainder")[len(prefix) :]

                assert m.group("remainder").endswith(
                    f'...{m.group("version")}'
                ), f"CHANGELOG.md:{lineno}: history link is for wrong version: {line}"

                version_range = re.match(
                    r"(?P<start_major>\d+)\.(?P<start_minor>\d+)\.(?P<start_patch>\d+)"
                    r"\.\.\."
                    r"(?P<end_major>\d+)\.(?P<end_minor>\d+)\.(?P<end_patch>\d+)$",
                    remainder,
                )
                assert (
                    version_range
                ), f"CHANGELOG.md:{lineno}: unexpected finalized history link: {line}"

                start = tuple(
                    int(version_range.group(v))
                    for v in ("start_major", "start_minor", "start_patch")
                )
                end = tuple(
                    int(version_range.group(v))
                    for v in ("end_major", "end_minor", "end_patch")
                )
                assert (
                    start < end
                ), f"CHANGELOG.md:{lineno}: invalid version range: {line}"


def test_agxbuf_print_nul(tmp_path: Path):
    """
    `agxbprint` should not account for nor append a NUL byte
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "agxbuf-print-nul.c").resolve()
    assert c_src.exists(), "missing test case"

    lib = Path(__file__).parents[1] / "lib"
    if platform.system() == "Windows" and not is_mingw():
        cflags = [f"/I{lib}"]
    else:
        # gnu17 needed for `strndup`
        cflags = ["-std=gnu17", f"-I{lib}"]

    run_c(c_src, tmp_path, cflags=cflags)


def test_agxbuf_use_implicit_nul(tmp_path: Path):
    """
    `agxbuf` should be able to use its entire memory as an inline string
    """

    # find co-located test source
    c_src = (Path(__file__).parent / "agxbuf-use-implicit-nul.c").resolve()
    assert c_src.exists(), "missing test case"

    lib = Path(__file__).parents[1] / "lib"
    if platform.system() == "Windows" and not is_mingw():
        cflags = [f"/I{lib}"]
    else:
        # gnu17 needed for `strndup`
        cflags = ["-std=gnu17", f"-I{lib}"]

    run_c(c_src, tmp_path, cflags=cflags)


@pytest.mark.skipif(which("edgepaint") is None, reason="edgepaint not available")
def test_edgepaint_error_message():
    """
    when failing to open its output, edgepaint should not dereference a null
    pointer
    """

    # try to open a non-existent file
    edgepaint = which("edgepaint")
    proc = subprocess.run(
        [edgepaint, "-o", "/a/nonexistent/path"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    # edgepaint should name itself in the error message, not “(null)”
    assert re.search(
        r"\bedgepaint\b", proc.stderr
    ), "edgepaint does not know its own name"


@pytest.mark.skipif(which("gvpr") is None, reason="gvpr not available")
def test_lock_graph():
    """GVPR’s `lock` should not misinterpret numbers >INT_MAX"""

    # find co-located test sources
    program1 = Path(__file__).parent / "lock_graph1.gvpr"
    assert program1.exists(), "missing test case"
    program2 = Path(__file__).parent / "lock_graph2.gvpr"
    assert program2.exists(), "missing test case"

    # a basic graph
    src = "digraph { a -> b; }"

    # process this with a conventional locking program
    gvpr_bin = which("gvpr")
    output = run(gvpr_bin, "-f", program1, input=src)
    assert output == "0\n1\n", "locking a graph did not work"

    # now try this with a large integer for the locking operation
    output = run(gvpr_bin, "-f", program2, input=src)
    assert output == "0\n1\n", "locking a graph using a large integer did not work"


def test_duplicate_font_family():
    """
    SVG output should not contain duplicate `font-family` items
    https://gitlab.com/graphviz/graphviz/-/merge_requests/4298
    """

    # a sample graph to exercise font families
    source = textwrap.dedent(
        """\
    graph G {
      graph [fontnames=svg];
      N [label="node" fontname="Helvetica"];
    }
    """
    )

    # convert this to SVG
    svg = dot("svg", source=source)

    # extract font families
    for ff in re.findall(r'font-family="(?P<families>[^"]*)', svg):
        families = [f.strip() for f in ff.split(",")]
        assert len(families) == len(set(families)), "duplicate font families listed"


def test_plugin_version_cmake():
    """confirm the plugin version defined in CMake matches Autotools"""
    autotools_current, autotools_revision, autotools_age = plugin_version()

    # the CMake build system assumes the last component is 0 for now
    cmake_age = 0

    assert (
        autotools_age == cmake_age
    ), "CMake build system assumes plugin age is 0 and it is not"

    # parse the equivalent out of the CMake build system
    cmakelists = Path(__file__).resolve().parents[1] / "CMakeLists.txt"
    cmake_current: Optional[int] = None
    cmake_revision: Optional[int] = None
    with open(cmakelists, "rt", encoding="utf-8") as f:
        for line in f:
            if m := re.match(
                r"\s*set\s*\(\s*GVPLUGIN_CURRENT\s+(?P<current>\d+)\s*\)\s*$",
                line,
            ):
                cmake_current = int(m.group("current"))
                if cmake_revision is not None:
                    break
            if m := re.match(
                r"\s*set\s*\(\s*GVPLUGIN_REVISION\s+(?P<revision>\d+)\s*\)\s*$",
                line,
            ):
                cmake_revision = int(m.group("revision"))
                if cmake_current is not None:
                    break
    assert (
        cmake_current is not None
    ), "failed to parse CMake build system’s plugin current version"
    assert (
        cmake_revision is not None
    ), "failed to parse CMake build system’s plugin revision"

    assert (
        autotools_current == cmake_current
    ), "Autotools and CMake build systems disagree on plugin current version"
    assert (
        autotools_revision == cmake_revision
    ), "Autotools and CMake build systems disagree on plugin revision"


def test_plugin_version_redhat():
    """confirm the plugin version defined in Red Hat spec files matches Autotools"""
    autotools_current, _, _ = plugin_version()

    # parse the equivalent out of the spec file
    spec = Path(__file__).resolve().parents[1] / "redhat/graphviz.spec.fedora.in"
    rpm_current: Optional[int] = None
    with open(spec, "rt", encoding="utf-8") as f:
        for line in f:
            if m := re.match(
                r"\s*%\s*global\s+pluginsver\s+(?P<current>\d+)\s*$",
                line,
            ):
                rpm_current = int(m.group("current"))
                break
    assert (
        rpm_current is not None
    ), "failed to parse Red Hat spec file’s plugin current version"

    assert (
        autotools_current == rpm_current
    ), "Autotools and Red Hat spec file disagree on plugin current version"


@pytest.mark.parametrize("lib", ("cdt", "cgraph", "gvc", "gvpr", "pathplan", "xdot"))
def test_library_so_version(lib: str):
    """test library SO versions are consistently defined"""

    # parse the canonical version out of Autotools
    makefile_am = Path(__file__).resolve().parents[1] / "lib" / lib / "Makefile.am"
    version: Optional[tuple[int, int, int]] = None
    with open(makefile_am, "rt", encoding="utf-8") as f:
        for line in f:
            if m := re.match(
                r"\s*"
                + lib.upper()
                + r'_VERSION\s*=\s*"(?P<current>\d+):(?P<revision>\d+):(?P<age>\d+)"\s*$',
                line,
            ):
                version = (
                    int(m.group("current")),
                    int(m.group("revision")),
                    int(m.group("age")),
                )
                break
    assert version is not None, "failed to parse library version"

    # parse the equivalent out of the CMake build system
    cmakelists = Path(__file__).resolve().parents[1] / "lib" / lib / "CMakeLists.txt"
    cmake_version: Optional[tuple[int, int, int]] = None
    cmake_soversion: Optional[int] = None
    with open(cmakelists, "rt", encoding="utf-8") as f:
        for line in f:
            if m := re.match(
                r"\s*VERSION\s+(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\s*$", line
            ):
                cmake_version = (
                    int(m.group("major")),
                    int(m.group("minor")),
                    int(m.group("patch")),
                )
                if cmake_soversion is not None:
                    break
                continue
            if m := re.match(r"\s*SOVERSION\s+(?P<major>\d+)\s*$", line):
                cmake_soversion = int(m.group("major"))
                if cmake_version is not None:
                    break
                continue
    assert cmake_version is not None, "failed to parse version from CMake build system"
    assert (
        cmake_soversion is not None
    ), "failed to parse SO version from CMake build system"

    assert cmake_version[0] == cmake_soversion, "major version and SO version disagree"

    # unconditionally use the mapping rule `major = current - age` even though this is
    # platform-dependent, because all the platforms we support use this mapping
    assert (
        cmake_version[0] == version[0] - version[2]
    ), "CMake and Autotools build systems disagree on library major version"

    assert (
        cmake_version[1] == version[2]
    ), "CMake and Autotools build systems disagree on library minor version"

    assert (
        cmake_version[2] == version[1]
    ), "CMake and Autotools build systems disagree on library patch version"

    # parse the equivalent out of the Debian rules
    rules = Path(__file__).resolve().parents[1] / "debian/rules"
    deb_soname: Optional[int] = None
    with open(rules, "rt", encoding="utf-8") as f:
        for line in f:
            if m := re.match(
                r"\s*" + lib.upper() + r"_SONAME\s*=\s*(?P<soversion>\d+)\s*$", line
            ):
                deb_soname = int(m.group("soversion"))
                break
    assert deb_soname is not None, "failed to parse SONAME from Debian rules"

    # again, we use a common mapping rule `major = current - age`
    assert (
        version[0] - version[2] == deb_soname
    ), "Autotools and Debian rules disagree on library version"

    # parse the equivalent out of the Debian lintian overrides
    overrides = (
        Path(__file__).resolve().parents[1] / "debian/libgraphviz4.lintian-overrides"
    )
    overrides_version1: Optional[int] = None
    overrides_version2: Optional[int] = None
    with open(overrides, "rt", encoding="utf-8") as f:
        for line in f:
            if m := re.search(r"\blib" + lib + r"\.so\.(?P<soversion>\d+)\b", line):
                overrides_version1 = int(m.group("soversion"))
                if overrides_version2 is not None:
                    break
                continue
            if m := re.search(r"\blib" + lib + r"(?P<soversion>\d+)\b", line):
                overrides_version2 = int(m.group("soversion"))
                if overrides_version1 is not None:
                    break
                continue
    # libgvpr has no overrides
    if lib == "gvpr":
        assert overrides_version1 is None
        assert overrides_version2 is None
        return
    assert (
        overrides_version1 is not None
    ), f"failed to parse lib{lib}.so.* from Debian lintian overrides"
    assert (
        overrides_version2 is not None
    ), f"failed to parse lib{lib}* from Debian lintian overrides"

    # again, we use a common mapping rule `major = current - age`
    assert (
        version[0] - version[2] == overrides_version1
    ), "Autotools and Debian lintian overrides disagree on library version"
    assert (
        version[0] - version[2] == overrides_version2
    ), "Autotools and Debian lintian overrides disagree on library version"


@pytest.mark.skipif(which("mm2gv") is None, reason="mm2gv not available")
def test_mm2gv_cmplx():
    """mm2gv should not crash when processing matrices with complex elements"""

    # locate our associated test case in this directory, Matrix Market input that
    # specifies a matrix with elements of complex type
    src = Path(__file__).parent / "mm-cmplx.mm"
    assert src.exists(), "unexpectedly missing test case"

    # run this through mm2gv
    mm2gv = which("mm2gv")
    proc = subprocess.run([mm2gv, "-o", os.devnull, src], check=False)

    assert proc.returncode in (0, 1), "mm2gv crashed"


def test_negative_dpi():
    """can Graphviz deal with an illegal negative `dpi` value?"""

    # locate our associated test case in this directory
    src = Path(__file__).parent / "negative-dpi.dot"
    assert src.exists(), "unexpectedly missing test case"

    run("dot", "-Tpng", "-o", os.devnull, src, timeout=10)


def test_postaction():
    """the legacy `postaction` attribute should not be usable to crash Graphviz"""
    source = 'digraph G { graph [postaction="]"]; a -> b; }'
    dot("svg", source=source)
