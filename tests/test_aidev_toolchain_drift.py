"""docker-bake.hcl's tool versions match aidev's published toolchain definition.

aidev publishes its toolchain.yaml publicly; this repository reads that copy
anonymously (it must hold no credential for ai/aidev) and never the reverse.
"""

import email.message
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

import pytest
import yaml

BAKE = Path(__file__).resolve().parent.parent / "docker-bake.hcl"
DEFAULT_TOOLCHAIN_URL = "https://gitlab.syncad.com/hive/aidev-toolchain/-/raw/main/toolchain.yaml"
# aidev toolchain.yaml tool entry -> docker-bake.hcl variable. A tool only one
# side defines is not compared.
TOOLS = {
    "pytest": "PYTEST_VERSION",
    "pytest-xdist": "PYTEST_XDIST_VERSION",
    "pytest-timeout": "PYTEST_TIMEOUT_VERSION",
    "tavern": "TAVERN_VERSION",
    "deepdiff": "DEEPDIFF_VERSION",
    "jsondiff": "JSONDIFF_VERSION",
    "prettytable": "PRETTYTABLE_VERSION",
    "ruff": "RUFF_VERSION",
    "mypy": "MYPY_VERSION",
    "pylint": "PYLINT_VERSION",
    "yamllint": "YAMLLINT_VERSION",
    "sqlfluff": "SQLFLUFF_VERSION",
    "shellcheck": "SHELLCHECK_VERSION",
    "tox": "TOX_VERSION",
    "postgrest": "POSTGREST_VERSION",
    "node": "NODE_VERSION",
    "pnpm": "PNPM_VERSION",
    "playwright": "PLAYWRIGHT_VERSION",
    "typescript": "TYPESCRIPT_VERSION",
}
# Tools (or platform.<key>) docker-bake.hcl deliberately keeps at another
# version than aidev, each with the reason
EXCEPTIONS: Dict[str, str] = {}
_TIMEOUT_SECONDS = 30


def _bake_defaults(text: str) -> Dict[str, str]:
    pattern = re.compile(r'variable "(\w+)" \{\s*default = "([^"]*)"')
    return dict(pattern.findall(text))


def _same_version(aidev: str, ccc: str) -> bool:
    """Equal ignoring a leading `v`; a less precise aidev version (node's major) is a prefix."""
    aidev_parts = aidev.lstrip("v").split(".")
    ccc_parts = ccc.lstrip("v").split(".")
    return ccc_parts[: len(aidev_parts)] == aidev_parts


def _platform(bake: Mapping[str, str]) -> Dict[str, str]:
    """Python and Ubuntu versions of PYTHON_RUNTIME_VERSION (`<python>-u<ubuntu>-<n>`)."""
    match = re.fullmatch(r"(\d+\.\d+)-u(\d+\.\d+)-\w+", bake.get("PYTHON_RUNTIME_VERSION", ""))
    return {"python": match.group(1), "ubuntu": match.group(2)} if match else {}


def _drift(toolchain: Mapping[str, Any], bake: Mapping[str, str],
           exceptions: Mapping[str, str]) -> List[str]:
    """`<name>: aidev <version>, docker-bake.hcl <variable>=<version>` per difference."""
    pairs = []
    tools = toolchain.get("tools") or {}
    for tool, variable in TOOLS.items():
        entry = tools.get(tool)
        if isinstance(entry, dict) and "version" in entry and variable in bake:
            pairs.append((tool, str(entry["version"]), variable, bake[variable]))
    platform = _platform(bake)
    for key, version in (toolchain.get("platform") or {}).items():
        if key in platform:
            pairs.append((f"platform.{key}", str(version), "PYTHON_RUNTIME_VERSION", platform[key]))
    return [
        f"{name}: aidev {aidev}, docker-bake.hcl {variable}={ccc}"
        for name, aidev, variable, ccc in pairs
        if name not in exceptions and not _same_version(aidev, ccc)
    ]


def _fetch_toolchain(url: str) -> Dict[str, Any]:
    """aidev's toolchain.yaml from `url`, sent without credentials; skips when unavailable."""
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        pytest.skip(f"aidev toolchain.yaml unreachable: {url}: HTTP {error.code}")
    except (urllib.error.URLError, OSError, UnicodeDecodeError) as error:
        pytest.skip(f"aidev toolchain.yaml unreachable: {url}: {error}")
    try:
        toolchain = yaml.safe_load(text)
    except yaml.YAMLError as error:
        pytest.skip(f"aidev toolchain.yaml unreachable: {url}: unparseable: {error}")
    if not isinstance(toolchain, dict) or not isinstance(toolchain.get("tools"), dict):
        pytest.skip(f"aidev toolchain.yaml unreachable: {url}: no `tools` mapping")
    return toolchain


def test_docker_bake_matches_aidev_toolchain() -> None:
    toolchain = _fetch_toolchain(os.environ.get("AIDEV_TOOLCHAIN_URL") or DEFAULT_TOOLCHAIN_URL)
    drift = _drift(toolchain, _bake_defaults(BAKE.read_text()), EXCEPTIONS)
    assert not drift, "docker-bake.hcl drifts from aidev's toolchain.yaml:\n" + "\n".join(drift)


def test_exceptions_name_compared_tools_with_a_reason() -> None:
    known = set(TOOLS) | {"platform.python", "platform.ubuntu"}
    assert {name for name, reason in EXCEPTIONS.items() if name in known and reason.strip()} == set(EXCEPTIONS)


_FIXTURE_BAKE = """
variable "PYTHON_RUNTIME_VERSION" {
  default = "3.14-u26.04-1"
}
variable "PYLINT_VERSION" {
  default = "4.1.3"
}
variable "SHELLCHECK_VERSION" {
  default = "v0.11.0"
}
variable "NODE_VERSION" {
  default = "v24.21.0"
}
variable "TYPESCRIPT_VERSION" {
  default = "5.9.3"
}
variable "TOX_VERSION" {
  default = "4.67.1"
}
"""
_FIXTURE_TOOLCHAIN = """
schema: 1
platform:
  ubuntu: "26.04"
  python: "3.14"
tools:
  pylint: {version: "4.1.3", pypi: pylint}
  shellcheck: {version: "v0.11.0", image: koalaman/shellcheck-alpine}
  node:
    version: "24"
  typescript: {version: "5.9.3", npm: typescript}
  semgrep: {version: "1.159.0", pypi: semgrep}
"""


def _fixture_drift(toolchain_edits: Optional[Mapping[str, str]] = None,
                   bake_edits: Optional[Mapping[str, str]] = None,
                   exceptions: Optional[Mapping[str, str]] = None) -> List[str]:
    toolchain_text, bake_text = _FIXTURE_TOOLCHAIN, _FIXTURE_BAKE
    for old, new in (toolchain_edits or {}).items():
        toolchain_text = toolchain_text.replace(old, new)
    for old, new in (bake_edits or {}).items():
        bake_text = bake_text.replace(old, new)
    return _drift(yaml.safe_load(toolchain_text), _bake_defaults(bake_text), exceptions or {})


def test_matching_definitions_have_no_drift() -> None:
    # node 24 vs v24.21.0, v0.11.0 vs v0.11.0; semgrep and tox exist on one side only
    assert not _fixture_drift()


def test_each_differing_tool_is_reported_with_both_versions() -> None:
    drift = _fixture_drift(
        toolchain_edits={'version: "4.1.3"': 'version: "4.1.4"', 'version: "24"': 'version: "22"'},
        bake_edits={'"v0.11.0"': '"v0.10.0"'},
    )
    assert drift == [
        "pylint: aidev 4.1.4, docker-bake.hcl PYLINT_VERSION=4.1.3",
        "shellcheck: aidev v0.11.0, docker-bake.hcl SHELLCHECK_VERSION=v0.10.0",
        "node: aidev 22, docker-bake.hcl NODE_VERSION=v24.21.0",
    ]


def test_a_leading_v_is_ignored() -> None:
    assert not _fixture_drift(toolchain_edits={'"v0.11.0"': '"0.11.0"'})


def test_platform_differences_are_reported() -> None:
    drift = _fixture_drift(bake_edits={"3.14-u26.04-1": "3.13-u24.04-2"})
    assert drift == [
        "platform.ubuntu: aidev 26.04, docker-bake.hcl PYTHON_RUNTIME_VERSION=24.04",
        "platform.python: aidev 3.14, docker-bake.hcl PYTHON_RUNTIME_VERSION=3.13",
    ]


def test_a_recorded_exception_is_not_drift() -> None:
    edits = {'version: "5.9.3"': 'version: "7.0.2"'}
    assert _fixture_drift(toolchain_edits=edits) == [
        "typescript: aidev 7.0.2, docker-bake.hcl TYPESCRIPT_VERSION=5.9.3"
    ]
    assert not _fixture_drift(toolchain_edits=edits, exceptions={"typescript": "reason"})


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def read(self) -> bytes:
        return self._body


@pytest.mark.parametrize(
    "failure, reason",
    [
        (urllib.error.HTTPError("u", 404, "Not Found", email.message.Message(), None), "HTTP 404"),
        (urllib.error.URLError("Name or service not known"), "Name or service not known"),
        (TimeoutError("timed out"), "timed out"),
        (_Response(b"tools: [unclosed"), "unparseable"),
        (_Response(b"<html>sign in</html>"), "no `tools` mapping"),
    ],
)
def test_an_unavailable_toolchain_skips_naming_the_url(
    monkeypatch: pytest.MonkeyPatch, failure: Union[Exception, "_Response"], reason: str
) -> None:
    url = "https://example.invalid/toolchain.yaml"
    requests: List[Any] = []

    def urlopen(request: Any, timeout: float) -> _Response:
        requests.append((request, timeout))
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setenv("AIDEV_TOOLCHAIN_URL", url)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(pytest.skip.Exception) as skipped:
        test_docker_bake_matches_aidev_toolchain()
    assert str(skipped.value).startswith(f"aidev toolchain.yaml unreachable: {url}: ")
    assert reason in str(skipped.value)
    # A plain URL: no Request object that could carry an auth header
    assert requests == [(url, 30)]


def test_the_fetch_sends_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: List[Any] = []

    def urlopen(request: Any, timeout: float) -> _Response:
        requests.append(request)
        return _Response(_FIXTURE_TOOLCHAIN.encode())

    monkeypatch.delenv("AIDEV_TOOLCHAIN_URL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert _fetch_toolchain(DEFAULT_TOOLCHAIN_URL)["tools"]["pylint"]["version"] == "4.1.3"
    assert requests == [DEFAULT_TOOLCHAIN_URL]
