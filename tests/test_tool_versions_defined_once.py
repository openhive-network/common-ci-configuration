"""CI/test tool versions are defined once, as docker-bake.hcl variables.

Dockerfiles get them as build args without defaults, and templates run their
test and lint jobs in the tools images instead of pinning or installing tools.
"""

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BAKE = ROOT / "docker-bake.hcl"
TOOL_VARIABLES = (
    "PYTEST_VERSION",
    "PYTEST_XDIST_VERSION",
    "PYTEST_TIMEOUT_VERSION",
    "TAVERN_VERSION",
    "DEEPDIFF_VERSION",
    "JSONDIFF_VERSION",
    "PRETTYTABLE_VERSION",
    "RUFF_VERSION",
    "MYPY_VERSION",
    "PYLINT_VERSION",
    "YAMLLINT_VERSION",
    "SQLFLUFF_VERSION",
    "SHELLCHECK_VERSION",
    "TOX_VERSION",
    "POSTGREST_VERSION",
    "PSQL_VERSION",
    "NODE_VERSION",
    "PNPM_VERSION",
    "PLAYWRIGHT_VERSION",
    "TYPESCRIPT_VERSION",
)
TOOL_PACKAGES = (
    "pytest",
    "pytest-xdist",
    "tavern",
    "deepdiff",
    "jsondiff",
    "ruff",
    "mypy",
    "pylint",
    "yamllint",
    "sqlfluff",
    "tox",
)
TOOLS_IMAGE_JOBS = (
    ".haf_app_lint_bash",
    ".haf_app_lint_sql",
    ".lint_code_with_ruff_template",
    ".type_check_with_mypy_template",
)


def _bake_defaults() -> Dict[str, str]:
    pattern = re.compile(r'variable "(\w+)" \{\s*default = "([^"]*)"')
    return dict(pattern.findall(BAKE.read_text()))


def _version_uses(value: str, text: str) -> List[str]:
    """Lines of `text` naming `value` as a version (not as part of a longer one)."""
    bare = re.escape(value.lstrip("v"))
    pattern = re.compile(rf"(?<![\w.])v?{bare}(?![\w.])")
    return [line for line in text.splitlines() if pattern.search(line)]


def _dockerfiles() -> List[Path]:
    return sorted(ROOT.glob("Dockerfile*"))


def _templates() -> List[Path]:
    return sorted((ROOT / "templates").glob("*.yml"))


def _load_jobs() -> Dict[str, Any]:
    loader: Any = type("_Loader", (yaml.SafeLoader,), {})
    loader.add_constructor("!reference", lambda ldr, node: ldr.construct_sequence(node))
    jobs: Dict[str, Any] = {}
    for path in _templates():
        jobs.update(yaml.load(path.read_text(), Loader=loader) or {})
    return jobs


# A pip install command (with its backslash-continued lines) naming a tool
_TOOL_INSTALL = re.compile(
    r"pip3?[ \t]+install\b(?:[^\n]|\\\n)*?[ \t\"']"
    rf"(?:{'|'.join(map(re.escape, TOOL_PACKAGES))})(?:[\s\"'<>=~]|$)",
    re.MULTILINE,
)


def _commands(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [cmd for item in value for cmd in _commands(item)]
    return []


def test_every_tool_version_is_a_bake_variable() -> None:
    defaults = _bake_defaults()

    assert set(TOOL_VARIABLES) <= set(defaults)


@pytest.mark.parametrize("variable", TOOL_VARIABLES)
def test_tool_version_appears_only_in_its_bake_variable(variable: str) -> None:
    value = _bake_defaults()[variable]
    # A bare major ("18") also matches unrelated numbers; the ARG check covers it.
    if "." not in value:
        pytest.skip(f"{variable}={value} is not a dotted version")

    elsewhere = {
        str(path.relative_to(ROOT)): uses
        for path in _dockerfiles() + _templates()
        if (uses := _version_uses(value, path.read_text()))
    }
    in_bake = [line for line in _version_uses(value, BAKE.read_text()) if "default =" not in line]

    assert elsewhere == {}
    assert in_bake == []


def test_dockerfiles_declare_tool_args_without_defaults() -> None:
    pattern = re.compile(rf"^ARG ({'|'.join(TOOL_VARIABLES)})=", re.MULTILINE)
    defaults = {
        str(path.relative_to(ROOT)): pattern.findall(path.read_text())
        for path in _dockerfiles()
    }

    assert {name: found for name, found in defaults.items() if found} == {}


def test_tool_version_is_passed_to_a_build() -> None:
    bake = BAKE.read_text()
    passed = set(re.findall(r"^\s+(\w+) = (?:\"\$\{)?\1\b", bake, re.MULTILINE))

    assert set(TOOL_VARIABLES) <= passed


@pytest.mark.parametrize("job", TOOLS_IMAGE_JOBS)
def test_lint_jobs_run_in_python_test_tools(job: str) -> None:
    assert _load_jobs()[job]["image"] == "${PYTHON_TEST_TOOLS_IMAGE}"


def test_templates_install_no_test_tool_at_job_time() -> None:
    offenders = {
        name: cmds
        for name, job in _load_jobs().items()
        if isinstance(job, dict)
        for cmds in [
            [
                cmd
                for key in ("before_script", "script")
                for cmd in _commands(job.get(key))
                if _TOOL_INSTALL.search(cmd)
            ]
        ]
        if cmds
    }

    assert offenders == {}


def test_templates_select_no_upstream_tool_image() -> None:
    upstream = re.compile(r"koalaman/shellcheck|sqlfluff/sqlfluff|postgrest/postgrest:")

    assert {str(p.relative_to(ROOT)) for p in _templates() if upstream.search(p.read_text())} == set()


def test_tavern_install_deps_installs_nothing() -> None:
    script = "\n".join(_commands(_load_jobs()[".tavern_install_deps"]["script"]))

    assert re.search(r"\bpip3?\s+install\b|\bvenv\b", script) is None


@pytest.mark.parametrize(
    "line,value,expected",
    [
        ('default = "v12.0.2"', "v12.0.2", True),
        ("FROM mirrors/postgrest-postgrest-v12.0.2", "v12.0.2", True),
        ("playwright@1.59.1 \\", "1.59.1", True),
        ("version 1.59.10", "1.59.1", False),
        ("python 21.59.1", "1.59.1", False),
    ],
)
def test_version_uses_matches_whole_versions_only(line: str, value: str, expected: bool) -> None:
    assert bool(_version_uses(value, line)) is expected


@pytest.mark.parametrize(
    "command,expected",
    [
        ('pip install --quiet \\\n  "pytest>=${PYTEST_VERSION:-8.0.0}" \\\n  "tavern>=2.10.0"', True),
        ("pip3 install ruff==0.1.0", True),
        ("pip install -r requirements.txt", False),
        ("pip install -e ./tests_api\npytest -n 4 .", False),
    ],
)
def test_tool_install_matches_continued_pip_install(command: str, expected: bool) -> None:
    assert bool(_TOOL_INSTALL.search(command)) is expected
