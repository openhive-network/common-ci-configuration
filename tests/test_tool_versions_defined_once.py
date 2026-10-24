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
GITLAB_CI = ROOT / ".gitlab-ci.yml"
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
    "DOCKER_VERSION",
    "DOCKER_COMPOSE_PLUGIN_VERSION",
    "DOCKER_BUILDX_PLUGIN_VERSION",
)
# Versions no file but docker-bake.hcl (and the CI variable below) may spell out
REPO_WIDE_VARIABLES = (
    "DOCKER_VERSION",
    "DOCKER_COMPOSE_PLUGIN_VERSION",
    "DOCKER_BUILDX_PLUGIN_VERSION",
)
# .gitlab-ci.yml image and service tags cannot read bake variables; it pins these
# through a CI variable of the same name that must hold the same value
CI_PINNED_VARIABLES = ("DOCKER_VERSION",)
# A docker:<tag>-cli / -dind image or its mirrors/docker-<tag>-cli / -dind copy
DOCKER_IMAGE_TAG = re.compile(r"\bdocker[:-]([^\s\"'/]+?)-(?:cli|dind)\b")
DOCKER_TAG_PLACEHOLDERS = ("${DOCKER_VERSION}", "<DOCKER_VERSION>")
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
TAVERN_SCRIPTS = (".tavern_install_deps", ".tavern_run_tests")
# Images templates define for downstream tavern jobs to run in
RUNNER_IMAGE_VARIABLES = ("HAF_APP_TEST_RUNNER_IMAGE",)
REGISTRY = "registry.gitlab.syncad.com/hive/common-ci-configuration/"
PYTHON_TEST_TOOLS = f"{REGISTRY}python-test-tools:"


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


def _repo_files() -> List[Path]:
    """Text files of the checkout outside hidden directories, plus .gitlab-ci.yml.

    This file is left out: its examples spell versions out.
    """
    files = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path != Path(__file__).resolve()
        and not any(part.startswith(".") for part in path.relative_to(ROOT).parts)
    ]
    texts = [path for path in files if b"\0" not in path.read_bytes()]
    return sorted([*texts, GITLAB_CI])


def _load_template(path: Path) -> Dict[str, Any]:
    loader: Any = type("_Loader", (yaml.SafeLoader,), {})
    loader.add_constructor("!reference", lambda ldr, node: ldr.construct_sequence(node))
    return yaml.load(path.read_text(), Loader=loader) or {}


def _load_jobs() -> Dict[str, Any]:
    jobs: Dict[str, Any] = {}
    for path in _templates():
        jobs.update(_load_template(path))
    return jobs


def _template_variables() -> Dict[str, str]:
    variables: Dict[str, str] = {}
    for path in _templates():
        variables.update(_load_template(path).get("variables") or {})
    return variables


def _resolved(jobs: Dict[str, Any], name: str, key: str) -> Any:
    """`key` of job `name` after `extends` (the job itself, then later parents, win)."""
    job = jobs[name]
    if key in job:
        return job[key]
    parents = job.get("extends", [])
    for parent in reversed([parents] if isinstance(parents, str) else parents):
        value = _resolved(jobs, parent, key)
        if value is not None:
            return value
    return None


def _expand(value: str, variables: Dict[str, str]) -> str:
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), m.group(0)), value)
    return value


def _final_base(dockerfile: str) -> str:
    """Image of the last FROM of `dockerfile` (the stage the build outputs)."""
    return re.findall(r"^FROM\s+(?:--\S+\s+)*(\S+)", dockerfile, re.MULTILINE)[-1]


def _derives_from_python_test_tools(image: str) -> bool:
    if image.startswith(PYTHON_TEST_TOOLS):
        return True
    if not image.startswith(REGISTRY):
        return False
    target = re.split(r"[:@]", image[len(REGISTRY):])[0]
    bake_dockerfiles = dict(
        re.findall(r'target "([\w-]+)" \{\s*dockerfile = "([^"]+)"', BAKE.read_text())
    )
    if target not in bake_dockerfiles:
        return False
    return _final_base((ROOT / bake_dockerfiles[target]).read_text()).startswith(PYTHON_TEST_TOOLS)


def _tool_job_images() -> Dict[str, str]:
    """Image of every template lint and tavern job, and of each runner image variable."""
    jobs = _load_jobs()
    variables = _template_variables()
    tavern_jobs = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict)
        and name not in TAVERN_SCRIPTS
        and set(TAVERN_SCRIPTS)
        & {cmd for key in ("before_script", "script") for cmd in _commands(job.get(key))}
    ]
    images = {
        name: _expand(_resolved(jobs, name, "image") or "", variables)
        for name in [*TOOLS_IMAGE_JOBS, *tavern_jobs]
    }
    images.update({name: _expand(variables[name], variables) for name in RUNNER_IMAGE_VARIABLES})
    return images


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


@pytest.mark.parametrize("variable", REPO_WIDE_VARIABLES)
def test_version_appears_nowhere_but_bake_and_ci_variable(variable: str) -> None:
    value = _bake_defaults()[variable]
    allowed = {
        BAKE: lambda line: "default =" in line,
        GITLAB_CI: lambda line: line.strip() == f'{variable}: "{value}"',
    }

    elsewhere = {
        str(path.relative_to(ROOT)): uses
        for path in _repo_files()
        if (
            uses := [
                line
                for line in _version_uses(value, path.read_text(errors="replace"))
                if not allowed.get(path, lambda _: False)(line)
            ]
        )
    }

    assert elsewhere == {}


@pytest.mark.parametrize("variable", CI_PINNED_VARIABLES)
def test_ci_variable_equals_bake_variable(variable: str) -> None:
    assert _load_template(GITLAB_CI)["variables"][variable] == _bake_defaults()[variable]


def test_docker_images_take_their_tag_from_docker_version() -> None:
    literal = {
        str(path.relative_to(ROOT)): tags
        for path in _repo_files()
        if (
            tags := [
                tag
                for tag in DOCKER_IMAGE_TAG.findall(path.read_text(errors="replace"))
                if tag not in DOCKER_TAG_PLACEHOLDERS
            ]
        )
    }

    assert literal == {}


def test_ci_docker_image_and_service_use_docker_version() -> None:
    jobs = _load_template(GITLAB_CI)

    assert jobs[".build_docker_image"]["image"] == "docker:${DOCKER_VERSION}-cli"
    assert "docker:${DOCKER_VERSION}-dind" in jobs["build_docker_dind_image"]["services"]


@pytest.mark.parametrize(
    "text,tags",
    [
        ("image: docker:26.1.4-cli", ["26.1.4"]),
        ("FROM registry/mirrors/docker-29.9.2-dind", ["29.9.2"]),
        ("- docker:${DOCKER_VERSION}-dind", ["${DOCKER_VERSION}"]),
        ("docker:29.9.2-cli-alpine3.24", ["29.9.2"]),
        ("hive/common-ci-configuration/docker-dind:latest", []),
        ("docker-builder:latest", []),
    ],
)
def test_docker_image_tag_finds_cli_and_dind_tags(text: str, tags: List[str]) -> None:
    assert DOCKER_IMAGE_TAG.findall(text) == tags


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


def test_tavern_jobs_are_checked_for_their_image() -> None:
    assert {".haf_app_tavern_tests", ".haf_app_dind_tavern_tests"} <= set(_tool_job_images())


def test_tavern_and_lint_images_derive_from_python_test_tools() -> None:
    offenders = {
        name: image
        for name, image in _tool_job_images().items()
        if not _derives_from_python_test_tools(image)
    }

    assert offenders == {}


@pytest.mark.parametrize(
    "dockerfile,expected",
    [
        (
            f"ARG T\nFROM {REGISTRY}mirrors/docker-cli AS cli\nFROM {PYTHON_TEST_TOOLS}${{T}} AS b\n",
            True,
        ),
        (f"FROM {REGISTRY}ci-base-image-ubuntu:${{V}}\nCOPY --from=x /a /a\n", False),
    ],
)
def test_final_base_is_the_last_from(dockerfile: str, expected: bool) -> None:
    assert _final_base(dockerfile).startswith(PYTHON_TEST_TOOLS) is expected


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

    assert re.search(r"\bpip3?\s+install\b|-m\s+venv\b|\bvirtualenv\b", script) is None


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
