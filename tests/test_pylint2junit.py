"""misc/pylint2junit.py on json2 reports shaped like pylint 4's."""

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from typing import Any, Dict, List

SCRIPT = Path(__file__).resolve().parent.parent / "misc" / "pylint2junit.py"


def _message(path: str, line: int, symbol: str, text: str) -> Dict[str, Any]:
    return {
        "type": "warning", "symbol": symbol, "message": text, "messageId": "W0000",
        "confidence": "HIGH", "module": Path(path).stem, "obj": "", "line": line,
        "column": 0, "endLine": None, "endColumn": None, "path": path,
        "absolutePath": f"/w/{path}",
    }


def _convert(tmp_path: Path, messages: List[Dict[str, Any]], modules: List[str]) -> ET.Element:
    report = tmp_path / "pylint-result.json"
    report.write_text(json.dumps({
        "messages": messages,
        "statistics": {"messageTypeCount": {}, "modulesLinted": len(modules), "score": 10.0},
    }))
    junit = tmp_path / "pylint-junit-result.xml"
    subprocess.run([sys.executable, str(SCRIPT), str(report), str(junit), *modules], check=True)
    return ET.parse(junit).getroot()


def _failure_text(case: ET.Element) -> str:
    failure = case.find("failure")
    assert failure is not None and failure.text is not None
    return failure.text


def test_clean_modules_pass(tmp_path: Path) -> None:
    suite = _convert(tmp_path, [], ["scripts/python/a.py", "scripts/python/b.py"])

    assert suite.tag == "testsuite"
    assert (suite.get("tests"), suite.get("failures")) == ("2", "0")
    assert [case.get("name") for case in suite] == ["scripts/python/a.py", "scripts/python/b.py"]
    assert all(len(case) == 0 for case in suite)


def test_module_with_messages_fails_naming_them(tmp_path: Path) -> None:
    messages = [
        _message("scripts/python/a.py", 3, "unused-import", "Unused import os"),
        _message("scripts/python/a.py", 9, "invalid-name", 'Constant name "x" is bad'),
    ]
    suite = _convert(tmp_path, messages, ["scripts/python/a.py", "scripts/python/b.py"])

    assert (suite.get("tests"), suite.get("failures")) == ("2", "1")
    failing, clean = list(suite)
    assert failing.get("name") == "scripts/python/a.py"
    assert _failure_text(failing).splitlines() == [
        "scripts/python/a.py:3: [unused-import] Unused import os",
        'scripts/python/a.py:9: [invalid-name] Constant name "x" is bad',
    ]
    assert clean.get("name") == "scripts/python/b.py" and len(clean) == 0


def test_message_outside_the_modules_gets_its_own_failing_case(tmp_path: Path) -> None:
    config = "Command line or configuration file"
    suite = _convert(tmp_path, [_message(config, 1, "bad-plugin-value", "no plugin")], ["a.py"])

    assert (suite.get("tests"), suite.get("failures")) == ("2", "1")
    assert [case.get("name") for case in suite] == ["a.py", config]
    assert "[bad-plugin-value] no plugin" in _failure_text(suite[1])


def test_empty_module_list_gives_an_empty_suite(tmp_path: Path) -> None:
    suite = _convert(tmp_path, [], [])

    assert suite.tag == "testsuite"
    assert (suite.get("tests"), suite.get("failures")) == ("0", "0")
    assert len(suite) == 0
