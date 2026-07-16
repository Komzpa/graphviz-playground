#!/usr/bin/env python3

"""Graphviz test coverage analysis script"""

import argparse
import copy
import logging
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Union
import xml.etree.ElementTree as ET

# logging output stream, setup in main()
log = None


def run(
    args: list[Union[str, Path]],
) -> None:
    """run a command, echoing it beforehand"""

    print(f"+ {shlex.join(str(x) for x in args)}", flush=True)
    subprocess.check_call(args)


def split_cobertura_by_sources(
    input_xml: Path, output_dir: Path, max_sources: int = 100
) -> None:
    """Split a Cobertura XML report to stay below GitLab's <source> limit."""

    tree = ET.parse(input_xml)
    root = tree.getroot()
    sources = root.find("sources")
    if sources is None:
        output_dir.mkdir(parents=True, exist_ok=True)
        tree.write(output_dir / "coverage.xml", encoding="utf-8", xml_declaration=True)
        return

    source_texts = [s.text or "" for s in sources.findall("source")]
    if len(source_texts) <= max_sources:
        output_dir.mkdir(parents=True, exist_ok=True)
        tree.write(
            output_dir / "coverage-000.xml", encoding="utf-8", xml_declaration=True
        )
        return

    packages = root.find("packages")
    if packages is None:
        output_dir.mkdir(parents=True, exist_ok=True)
        tree.write(
            output_dir / "coverage-000.xml", encoding="utf-8", xml_declaration=True
        )
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("coverage-*.xml"):
        stale.unlink()

    class_source_indexes: dict[tuple[int, int], set[int]] = {}
    for package_index, package in enumerate(packages.findall("package")):
        classes = package.find("classes")
        if classes is None:
            continue
        for class_index, class_element in enumerate(classes.findall("class")):
            filename = class_element.get("filename", "")
            matching_sources = {
                source_index
                for source_index, source in enumerate(source_texts)
                if (Path(source) / filename).exists()
            }
            if not matching_sources:
                continue
            class_source_indexes[(package_index, class_index)] = matching_sources

    source_chunks = [
        source_texts[i : i + max_sources]
        for i in range(0, len(source_texts), max_sources)
    ]
    for index, chunk in enumerate(source_chunks):
        chunk_source_indexes = set(
            range(index * max_sources, index * max_sources + len(chunk))
        )
        chunk_root = copy.deepcopy(root)
        chunk_sources = chunk_root.find("sources")
        assert chunk_sources is not None
        chunk_sources.clear()
        for source in chunk:
            source_element = ET.SubElement(chunk_sources, "source")
            source_element.text = source

        chunk_packages = chunk_root.find("packages")
        assert chunk_packages is not None
        for package_index, package in enumerate(list(chunk_packages.findall("package"))):
            classes = package.find("classes")
            if classes is None:
                continue
            for class_index, class_element in enumerate(list(classes.findall("class"))):
                source_indexes = class_source_indexes.get((package_index, class_index))
                if source_indexes is not None and source_indexes.isdisjoint(
                    chunk_source_indexes
                ):
                    classes.remove(class_element)
            if not list(classes.findall("class")):
                chunk_packages.remove(package)

        ET.ElementTree(chunk_root).write(
            output_dir / f"coverage-{index:03d}.xml",
            encoding="utf-8",
            xml_declaration=True,
        )


def main(args: list[str]) -> int:
    """entry point"""

    # setup logging to print to stderr
    global log
    ch = logging.StreamHandler()
    log = logging.getLogger("test_coverage.py")
    log.addHandler(ch)

    # parse command line arguments
    parser = argparse.ArgumentParser(
        description="Graphviz test coverage analysis script"
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Capture initial zero coverage data before running any test",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Analyze test coverage after running tests",
    )
    options = parser.parse_args(args[1:])

    if not options.init and not options.analyze:
        log.error("Must specify --init or --analyze; refusing to run")
        return -1

    cwd = Path.cwd()

    generated_files = [
        cwd / "build/cmd/tools/gmlparse.c",
        cwd / "build/cmd/tools/gmlscan.c",
        cwd / "build/lib/cgraph/grammar.c",
        cwd / "build/lib/cgraph/scan.c",
        cwd / "build/lib/common/htmlparse.c",
        cwd / "build/lib/expr/exparse.c",
        cwd / "build/cmd/gvedit/gvedit_autogen/EWIEGA46WW/qrc_mdi.cpp",
    ]

    # files that are generated but only need to be excluded during init
    init_generated_files = [
        cwd / "build/tclpkg/gv/CMakeFiles/gv_d.dir/gvD_wrap.cxx",
        cwd / "build/tclpkg/gv/CMakeFiles/gv_go.dir/gvGO_wrap.cxx",
        cwd / "build/tclpkg/gv/CMakeFiles/gv_sharp.dir/gvCSHARP_wrap.cxx",
        cwd / "build/tclpkg/gv/CMakeFiles/gv_tcl.dir/gvTCL_wrap.cxx",
    ]

    excluded_files = generated_files
    init_excluded_files = init_generated_files

    exclude_options = [f"--exclude={f}" for f in excluded_files]
    init_exclude_options = [f"--exclude={f}" for f in init_excluded_files]

    if options.init:
        run(
            [
                "lcov",
                "--capture",
                "--initial",
                "--directory=.",
                "--branch-coverage",
                "--no-external",
            ]
            + exclude_options
            + init_exclude_options
            + ["--output-file=app_base.info"]
        )

        return 0

    if options.analyze:
        # capture test coverage data
        run(
            [
                "lcov",
                "--capture",
                "--directory=.",
                "--branch-coverage",
                "--no-external",
                "--rc=check_data_consistency=0",
            ]
            + exclude_options
            + ["--output-file=app_test.info"]
        )
        # combine baseline and test coverage data
        run(
            [
                "lcov",
                "--branch-coverage",
                "--add-tracefile=app_base.info",
                "--add-tracefile=app_test.info",
                "--output-file=app_total.info",
                "--rc=check_data_consistency=0",
            ]
        )
        # generate coverage html pages using lcov which are nicer than gcovr's
        Path("coverage/lcov").mkdir(parents=True, exist_ok=True)
        run(
            [
                "genhtml",
                f"--prefix={cwd}",
                "--branch-coverage",
                "--output-directory=coverage/lcov",
                "--rc=check_data_consistency=0",
                "--show-details",
                "app_total.info",
            ]
        )
        # generate coverage info for GitLab's Test Coverage Visualization
        Path("coverage/gcovr").mkdir(parents=True, exist_ok=True)
        cobertura_report = Path("coverage/gcovr/coverage.xml")
        run(
            ["gcovr"]
            + exclude_options
            + [f"--gcov-exclude={f}" for f in generated_files]
            + [
                "--xml-pretty",
                "--html-details=coverage/gcovr/index.html",
                "--exclude-unreachable-branches",
                "--gcov-ignore-errors=no_working_dir_found",
                "--print-summary",
                f"--output={cobertura_report}",
                f"--root={cwd}",
            ]
        )
        split_cobertura_by_sources(cobertura_report, Path("coverage/cobertura"))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
