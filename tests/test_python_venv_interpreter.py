"""`.configuration_template` builds its venv with the Python PYTHON_VERSION names.

The template's before_script is run as GitLab's bash shell runs it, against fake
interpreters that report a configured version, and fake `poetry`/`pip` that log
their calls.
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "python_projects.gitlab-ci.yml"
IMAGE = "registry.example/ci-base-image:test"

# Versions no real host ships, so a python on the test machine never satisfies them.
FAKE_PYTHON = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python3"
  printf 'PATH="%s/bin:$PATH"\\n' "$3" > "$3/bin/activate"
  echo "$(basename "$0")" > "$3/created-by"
  exit 0
fi
case "$*" in
  -V) echo "Python {reported}.0" ;;
  *) echo "{reported}" ;;
esac
"""

FAKE_TOOL = """#!/bin/sh
echo "$(basename "$0") $*" >> "$TOOL_CALLS"
"""


class _Reference(List[Any]):
    """A GitLab `!reference [job, key, ...]` path."""


_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor(
    "!reference", lambda loader, node: _Reference(loader.construct_sequence(node))
)


def _before_script() -> str:
    jobs = yaml.load(TEMPLATE.read_text(), Loader=_Loader)
    return "\n".join(jobs[".configuration_template"]["before_script"])


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, shell_opts: str, pythons: Dict[str, str], python_version: Optional[str]
) -> "subprocess.CompletedProcess[str]":
    """Run the before_script with `pythons` (command name -> version it reports) on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, reported in pythons.items():
        _executable(bin_dir / name, FAKE_PYTHON.replace("{reported}", reported))
    for tool in ("poetry", "pip"):
        _executable(bin_dir / tool, FAKE_TOOL)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path / "home"),
        "PYPROJECT_DIR": str(tmp_path),
        "CI_JOB_IMAGE": IMAGE,
        "TOOL_CALLS": str(tmp_path / "tool.calls"),
    }
    if python_version is not None:
        env["PYTHON_VERSION"] = python_version
    return subprocess.run(
        ["bash", "-c", f"{shell_opts}\n{_before_script()}"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )


def _tool_calls(tmp_path: Path) -> List[str]:
    calls = tmp_path / "tool.calls"
    return calls.read_text().splitlines() if calls.exists() else []


SHELL_OPTS = pytest.mark.parametrize("shell_opts", ["set -eo pipefail", "set +e +o pipefail"])


@SHELL_OPTS
def test_venv_uses_the_requested_python_not_the_default(tmp_path: Path, shell_opts: str) -> None:
    proc = _run(tmp_path, shell_opts, {"python3": "9.97", "python9.98": "9.98"}, "9.98")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "home" / "venv-9.98" / "created-by").read_text() == "python9.98\n"
    assert "Python 9.98.0" in proc.stdout
    assert "poetry -C" in _tool_calls(tmp_path)[0]


@SHELL_OPTS
def test_python_missing_from_image_fails_before_installing(tmp_path: Path, shell_opts: str) -> None:
    proc = _run(tmp_path, shell_opts, {"python3": "9.97"}, "9.98")

    assert proc.returncode == 1
    assert "Python 9.98" in proc.stderr
    assert IMAGE in proc.stderr
    assert not (tmp_path / "home" / "venv-9.98").exists()
    assert not _tool_calls(tmp_path)


@SHELL_OPTS
def test_interpreter_reporting_another_version_fails_before_installing(
    tmp_path: Path, shell_opts: str
) -> None:
    proc = _run(tmp_path, shell_opts, {"python9.98": "9.97"}, "9.98")

    assert proc.returncode == 1
    assert "Python 9.97, not PYTHON_VERSION=9.98" in proc.stderr
    assert IMAGE in proc.stderr
    assert not _tool_calls(tmp_path)


@pytest.mark.parametrize("python_version", [None, ""])
def test_without_python_version_the_images_python3_is_used(
    tmp_path: Path, python_version: Optional[str]
) -> None:
    proc = _run(tmp_path, "set -eo pipefail", {"python3": "9.97"}, python_version)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "home" / "venv-" / "created-by").read_text() == "python3\n"
    assert "Python 9.97.0" in proc.stdout
