#!/usr/bin/env python3
"""Convert pylint's json2 output to a JUnit report, one test case per checked module.

    pylint2junit.py PYLINT_JSON2 JUNIT_XML [MODULE...]

MODULEs are the paths pylint was given; json2 doesn't list the modules it checked,
only those with messages. A module with messages fails, its failure body one
`path:line: [symbol] message` line per message; a clean module passes. Messages for
a path that isn't a MODULE (e.g. pylint's "Command line or configuration file")
get a failing case of their own.
"""
import json
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List


def _group_by_path(messages: List[Dict[str, Any]], modules: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {module: [] for module in modules}
    for message in messages:
        grouped.setdefault(message["path"], []).append(message)
    return grouped


def _testcase(path: str, messages: List[Dict[str, Any]]) -> ET.Element:
    case = ET.Element("testcase", classname="pylint", name=path)
    if messages:
        failure = ET.SubElement(
            case, "failure", type="pylint", message=f"{len(messages)} pylint message(s)"
        )
        failure.text = "\n".join(
            f"{m['path']}:{m['line']}: [{m['symbol']}] {m['message']}" for m in messages
        )
    return case


def convert(pylint_json2: str, junit_xml: str, modules: List[str]) -> None:
    """Write JUNIT_XML from the json2 report at PYLINT_JSON2 for the given modules.

    Raises OSError when a file can't be read or written and ValueError (including
    json.JSONDecodeError) or KeyError when PYLINT_JSON2 isn't pylint json2 output.
    """
    with open(pylint_json2, encoding="utf-8") as handle:
        report = json.load(handle)
    grouped = _group_by_path(report["messages"], modules)
    suite = ET.Element(
        "testsuite",
        name="pylint",
        tests=str(len(grouped)),
        failures=str(sum(1 for messages in grouped.values() if messages)),
        errors="0",
        skipped="0",
    )
    suite.extend(_testcase(path, messages) for path, messages in grouped.items())
    ET.ElementTree(suite).write(junit_xml, encoding="UTF-8", xml_declaration=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(f"usage: {sys.argv[0]} PYLINT_JSON2 JUNIT_XML [MODULE...]")
    convert(sys.argv[1], sys.argv[2], sys.argv[3:])
