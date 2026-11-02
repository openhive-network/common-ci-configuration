"""python-test-tools publishes its tool versions as pip constraints.

The Dockerfile's pins come from docker-bake.hcl and are what the image installs;
scripts/python/python-test-tools-constraints.py reads them back from the image's
label in the registry, and the .python_test_tools_constraints template runs it.
"""

import importlib.util
import json
import re
import subprocess
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Self

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile.python-test-tools"
BAKE = ROOT / "docker-bake.hcl"
BASE_TEMPLATE = ROOT / "templates" / "base.gitlab-ci.yml"
SCRIPT = ROOT / "scripts" / "python" / "python-test-tools-constraints.py"
CONSTRAINTS_FILE = "/etc/python-test-tools/constraints.txt"
# python-test-tools version args that are not Python packages
NON_PIP_ARGS = {"PYTHON_RUNTIME_VERSION", "SHELLCHECK_VERSION", "POSTGREST_VERSION", "PSQL_VERSION"}
IMAGE = "registry.example/hive/common-ci-configuration/python-test-tools:develop"
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPT.parent))
constraints = _load("python_test_tools_constraints", SCRIPT)


def _bake_defaults() -> Dict[str, str]:
    return dict(re.findall(r'variable "(\w+)" \{\s*default = "([^"]*)"', BAKE.read_text()))


def _dockerfile_pins() -> List[str]:
    """PYTHON_TEST_TOOLS_PINS of the Dockerfile, its continuation lines joined."""
    text = DOCKERFILE.read_text().replace("\\\n", "")
    match = re.search(r'^ARG PYTHON_TEST_TOOLS_PINS="([^"]*)"', text, re.MULTILINE)
    assert match, "no ARG PYTHON_TEST_TOOLS_PINS in Dockerfile.python-test-tools"
    return match.group(1).split()


def _expanded_pins() -> List[str]:
    defaults = _bake_defaults()
    return [re.sub(r"\$\{(\w+)\}", lambda m: defaults[m.group(1)], pin) for pin in _dockerfile_pins()]


def test_pins_name_every_python_tool_arg_once() -> None:
    version_args = set(re.findall(r"^ARG (\w+_VERSION)$", DOCKERFILE.read_text(), re.MULTILINE))
    pinned = [re.fullmatch(r"[\w-]+==\$\{(\w+)\}", pin) for pin in _dockerfile_pins()]

    assert all(pinned), _dockerfile_pins()
    variables = [match.group(1) for match in pinned if match]
    assert sorted(variables) == sorted(version_args - NON_PIP_ARGS)


def test_pins_expand_to_the_bake_versions() -> None:
    defaults = _bake_defaults()

    assert "pytest-rerunfailures==" + defaults["PYTEST_RERUNFAILURES_VERSION"] in _expanded_pins()
    assert "pytest-asyncio==" + defaults["PYTEST_ASYNCIO_VERSION"] in _expanded_pins()
    assert constraints.parse_constraints(" ".join(_expanded_pins())) == _expanded_pins()


def test_label_and_install_use_the_pins() -> None:
    lines = [line for line in DOCKERFILE.read_text().splitlines() if not line.lstrip().startswith("#")]
    text = "\n".join(lines).replace("\\\n", " ")

    assert f'LABEL {constraints.CONSTRAINTS_LABEL}="${{PYTHON_TEST_TOOLS_PINS}}"' in text
    assert f"printf '%s\\n' ${{PYTHON_TEST_TOOLS_PINS}} > {CONSTRAINTS_FILE}" in text
    installs = re.findall(r"\bpip3? install\b[^&]*", text)
    assert [install.strip() for install in installs] == [f"pip install --no-cache-dir -r {CONSTRAINTS_FILE}"]


class _Response:
    def __init__(self: Self, body: bytes) -> None:
        self.body = body

    def __enter__(self: Self) -> "_Response":
        return self

    def __exit__(self: Self, *_: Any) -> None:
        return None

    def read(self: Self) -> bytes:
        return self.body


def _http_error(url: str, code: int, challenge: Optional[str] = None) -> urllib.error.HTTPError:
    headers = Message()
    if challenge:
        headers["WWW-Authenticate"] = challenge
    return urllib.error.HTTPError(url, code, "error", headers, None)


class _Registry:
    """Fake urlopen serving `paths` (path under /v2/<repository>/ -> JSON body or
    HTTP status), behind a bearer challenge until a token is presented."""

    CHALLENGE = 'Bearer realm="https://auth.example/jwt",service="registry",scope="repository:x:pull"'

    def __init__(self: Self, paths: Dict[str, Any]) -> None:
        self.paths = paths
        self.requests: List[Any] = []

    def __call__(self: Self, request: Any, timeout: int) -> _Response:
        self.requests.append(request)
        url = request.full_url
        if url.startswith("https://auth.example/"):
            return _Response(b'{"token": "pull-token"}')
        if request.get_header("Authorization") != "Bearer pull-token":
            raise _http_error(url, 401, self.CHALLENGE)
        answer = self.paths.get(url.split("/python-test-tools/", 1)[1], 404)
        if isinstance(answer, int):
            raise _http_error(url, answer)
        return _Response(json.dumps(answer).encode())


def _image(labels: Optional[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "manifests/develop": {"mediaType": MANIFEST_TYPE, "config": {"digest": "sha256:c0"}},
        "blobs/sha256:c0": {"config": {"Labels": labels}},
    }


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, paths: Dict[str, Any]) -> int:
    monkeypatch.setattr(constraints.urllib.request, "urlopen", _Registry(paths))
    monkeypatch.delenv("CI_JOB_TOKEN", raising=False)
    status: int = constraints.main(["python-test-tools-constraints.py", IMAGE, str(tmp_path / "constraints.txt")])
    return status


def test_writes_the_label_pins_one_per_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    label = "pytest==9.1.1 pytest-asyncio==1.4.1\npytest-xdist==3.8.0"

    assert _run(monkeypatch, tmp_path, _image({constraints.CONSTRAINTS_LABEL: label})) == 0
    assert (tmp_path / "constraints.txt").read_text() == (
        "pytest==9.1.1\npytest-asyncio==1.4.1\npytest-xdist==3.8.0\n"
    )


def test_an_image_index_resolves_to_linux_amd64(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _image({constraints.CONSTRAINTS_LABEL: "pytest==9.1.1"})
    paths["manifests/sha256:amd64"] = paths.pop("manifests/develop")
    paths["manifests/develop"] = {
        "mediaType": INDEX_TYPE,
        "manifests": [
            {"digest": "sha256:arm64", "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "sha256:amd64", "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }

    assert _run(monkeypatch, tmp_path, paths) == 0
    assert (tmp_path / "constraints.txt").read_text() == "pytest==9.1.1\n"


def test_the_pull_token_is_not_sent_on_redirects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _Registry(_image({constraints.CONSTRAINTS_LABEL: "pytest==9.1.1"}))
    monkeypatch.setattr(constraints.urllib.request, "urlopen", registry)
    monkeypatch.setenv("CI_JOB_TOKEN", "job-token")

    constraints.image_constraints(IMAGE)

    authorized = [r for r in registry.requests if r.get_header("Authorization")]
    assert {r.full_url.split("?")[0].rsplit("/", 2)[-2] for r in authorized} == {"auth.example", "manifests", "blobs"}
    assert all("Authorization" in r.unredirected_hdrs for r in authorized)


@pytest.mark.parametrize(
    "paths,cause",
    [
        ({}, "tag 'develop' is not in the registry (HTTP 404)"),
        ({"manifests/develop": 500}, "the registry answered HTTP 500"),
        (_image(None), f"the image has no {constraints.CONSTRAINTS_LABEL} label"),
        (_image({"other": "x"}), f"the image has no {constraints.CONSTRAINTS_LABEL} label"),
        (_image({constraints.CONSTRAINTS_LABEL: ""}), "is not a list of name==version pins"),
        (_image({constraints.CONSTRAINTS_LABEL: "pytest>=9 ruff==0.1"}), "is not a list of name==version pins"),
        (_image({constraints.CONSTRAINTS_LABEL: "pytest=="}), "is not a list of name==version pins"),
    ],
)
def test_failure_names_the_image_and_the_cause_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    paths: Dict[str, Any], cause: str,
) -> None:
    assert _run(monkeypatch, tmp_path, paths) == 1
    error = capsys.readouterr().err
    assert error.startswith(f"ERROR: no pip constraints for {IMAGE}: ")
    assert cause in error
    assert not (tmp_path / "constraints.txt").exists()


def test_an_unreachable_registry_fails_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def urlopen(request: Any, timeout: int) -> _Response:
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(constraints.urllib.request, "urlopen", urlopen)

    assert constraints.main(["x", IMAGE, str(tmp_path / "c.txt")]) == 1
    assert "the registry registry.example is unreachable: Name or service not known" in capsys.readouterr().err


@pytest.mark.parametrize("reference", ["python-test-tools:develop", "registry.example/repo", "registry.example/a:b/c"])
def test_a_malformed_reference_fails(reference: str) -> None:
    with pytest.raises(constraints.ConstraintsError, match="image reference"):
        constraints.split_reference(reference)


@pytest.mark.parametrize(
    "reference,parts",
    [
        (IMAGE, ("registry.example", "hive/common-ci-configuration/python-test-tools", "develop")),
        ("localhost:5000/p/python-test-tools@sha256:ab", ("localhost:5000", "p/python-test-tools", "sha256:ab")),
    ],
)
def test_split_reference(reference: str, parts: Any) -> None:
    assert constraints.split_reference(reference) == parts


def _template_script() -> str:
    loader: Any = type("_Loader", (yaml.SafeLoader,), {})
    loader.add_constructor("!reference", lambda ldr, node: ldr.construct_sequence(node))
    template = yaml.load(BASE_TEMPLATE.read_text(), Loader=loader)
    return "\n".join(template[".python_test_tools_constraints"]["script"])


def _run_template(tmp_path: Path, curl: str, python3: str) -> "subprocess.CompletedProcess[str]":
    """The template's script under bash -e (as GitLab runs it) with stub curl and python3."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("curl", curl), ("python3", python3)):
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PYTHON_TEST_TOOLS_IMAGE": IMAGE,
        "PYTHON_TEST_TOOLS_CONSTRAINTS": str(tmp_path / "constraints.txt"),
        "LOG": str(tmp_path / "log"),
    }
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", _template_script() + "\necho after"],
        env=env, capture_output=True, text=True, check=False,
    )


def test_template_runs_the_script_on_the_tools_image(tmp_path: Path) -> None:
    result = _run_template(
        tmp_path,
        curl='echo "$@" >> "$LOG"; while [ "$1" != -o ]; do shift; done; : > "$2"',
        python3='echo "$@" >> "$LOG"',
    )

    assert result.returncode == 0, result.stderr
    *curl_args, python_args = (tmp_path / "log").read_text().splitlines()
    downloaded = [args.split()[1].rsplit("/-/raw/develop/scripts/python/", 1)[1] for args in curl_args]
    assert downloaded == ["python-test-tools-constraints.py", "registry_auth.py"]
    assert all((SCRIPT.parent / name).exists() for name in downloaded)
    assert python_args.split()[1:] == [IMAGE, str(tmp_path / "constraints.txt")]


@pytest.mark.parametrize(
    "curl,python3,message",
    [
        ("exit 22", "exit 0", f"ERROR: no pip constraints for {IMAGE}: could not download"),
        ('while [ "$1" != -o ]; do shift; done; : > "$2"', "exit 1", None),
    ],
)
def test_template_fails_the_job_when_the_constraints_cannot_be_obtained(
    tmp_path: Path, curl: str, python3: str, message: Optional[str]
) -> None:
    result = _run_template(tmp_path, curl, python3)

    assert result.returncode == 1
    assert "after" not in result.stdout
    if message:
        assert message in result.stdout
