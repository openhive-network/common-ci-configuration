"""CI/test tool versions are defined once, as docker-bake.hcl variables.

Dockerfiles get them as build args without defaults, and templates run their
test and lint jobs in the tools images instead of pinning or installing tools.
"""

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BAKE = ROOT / "docker-bake.hcl"
GITLAB_CI = ROOT / ".gitlab-ci.yml"
PNPM_CATALOG = ROOT / "pnpm-config" / "pnpm-workspace.yaml"
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
    "LIGHTHOUSE_VERSION",
    "DOCKER_VERSION",
    "DOCKER_COMPOSE_PLUGIN_VERSION",
    "DOCKER_BUILDX_PLUGIN_VERSION",
)
# Base image versions, passed to Dockerfiles like the tool versions
BASE_IMAGE_VARIABLES = ("EMSCRIPTEN_VERSION", "ALPINE_VERSION")
BUILD_ARG_VARIABLES = TOOL_VARIABLES + BASE_IMAGE_VARIABLES
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
# An emsdk, node or alpine image (or mirrors/<name>-<tag> copy), or a docker-<version>
# download, named with a literal version instead of a variable (docker:<port> is a
# DinD service address, not an image)
LITERAL_IMAGE_TAG = re.compile(r"\b(?:(?:emsdk|node|alpine)[:-]|docker-)(v?\d[^\s\"'/@]*)")
# Template variables pinning the tag of an image this project builds: templates
# cannot read bake variables, so each names the tag the bake target builds, or an
# earlier one while a new tag is not published yet. A new tag reaches a template
# in a second MR, merged once the first MR's develop pipeline has published it
# (CLAUDE.md, docs/common-ci-images.md "Moving templates to a new image tag").
# template variable -> (template, bake target)
TEMPLATE_PINNED_TAGS = {
    "EMSCRIPTEN_IMAGE_TAG": ("templates/npm_projects.gitlab-ci.yml", "emsdk"),
    "ALPINE_IMAGE_TAG": ("templates/base.gitlab-ci.yml", "alpine"),
}
# Template variables restating a bake version for downstream projects, without
# its "v" (official node: image tags have none):
# template variable -> (template, bake variable)
TEMPLATE_RESTATED_VERSIONS = {
    "NODE_IMAGE_VERSION": ("templates/base.gitlab-ci.yml", "NODE_VERSION"),
}
DOCKER_BUILD_JOB = ".docker_build_job_template"
# pnpm catalog (catalog, package) -> docker-bake.hcl variable it must equal:
# consumers install these from the catalog and run them against the images'
# copies (Playwright's browsers are per-version)
CATALOG_PACKAGES = {
    ("playwright-toolset", "playwright"): "PLAYWRIGHT_VERSION",
    ("playwright-toolset", "@playwright/test"): "PLAYWRIGHT_VERSION",
    ("typescript-toolset", "typescript"): "TYPESCRIPT_VERSION",
}
# Catalog packages deliberately kept at another version than docker-bake.hcl,
# each with the reason
CATALOG_EXCEPTIONS = {
    ("typescript-toolset", "typescript"): (
        "consumers' typedoc and rollup TypeScript plugins need the compiler JS API,"
        " which TypeScript 7 does not ship, so the catalog stays on TypeScript 6.0"
    ),
}
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


def _bake_target_tag(target: str) -> str:
    """Version tag bake target `target` is published under, its variables expanded."""
    tag = re.search(
        rf'target "{re.escape(target)}" \{{(?:(?!\ntarget ).)*?'
        rf'tags = generate-tags\("{re.escape(target)}", "([^"]+)"\)',
        BAKE.read_text(),
        re.DOTALL,
    )
    assert tag, f"no generate-tags call in bake target {target}"
    return _expand(tag.group(1), _bake_defaults())


def _tag_order(tag: str) -> Tuple[int, ...]:
    """Numbers of an image tag, for comparing two tags of one image ("5.0.2-7" ->
    (5, 0, 2, 7)); a digest after the tag is ignored."""
    return tuple(int(number) for number in re.findall(r"\d+", tag.split("@", 1)[0]))


def _is_pinned_tag_line(path: Path, line: str) -> bool:
    """`line` is a TEMPLATE_PINNED_TAGS or TEMPLATE_RESTATED_VERSIONS variable
    definition in its template."""
    relative = str(path.relative_to(ROOT))
    return any(
        template == relative and re.fullmatch(rf'{variable}: "[^"]*"', line.strip())
        for variable, (template, _) in [
            *TEMPLATE_PINNED_TAGS.items(),
            *TEMPLATE_RESTATED_VERSIONS.items(),
        ]
    )


def _version_uses(value: str, text: str) -> List[str]:
    """Lines of `text` naming `value` as a version (not as part of a longer one)."""
    bare = re.escape(value.lstrip("v"))
    pattern = re.compile(rf"(?<![\w.])v?{bare}(?![\w.])")
    return [line for line in text.splitlines() if pattern.search(line)]


def _catalog_drift(
    catalogs: Dict[str, Any], bake: Dict[str, str], exceptions: Dict[Tuple[str, str], str]
) -> List[str]:
    """`<catalog> <package>: pnpm catalog <version>, docker-bake.hcl <variable>=<version>`
    per compared package whose versions differ and is not an exception."""
    drift = []
    for (catalog, package), variable in CATALOG_PACKAGES.items():
        version = str((catalogs.get(catalog) or {}).get(package))
        if (catalog, package) not in exceptions and version != bake[variable]:
            drift.append(
                f"{catalog} {package}: pnpm catalog {version},"
                f" docker-bake.hcl {variable}={bake[variable]}"
            )
    return drift


def _catalogs() -> Dict[str, Any]:
    return yaml.safe_load(PNPM_CATALOG.read_text())["catalogs"]


def _dockerfiles() -> List[Path]:
    return sorted(ROOT.glob("Dockerfile*"))


def _templates() -> List[Path]:
    return sorted((ROOT / "templates").glob("*.yml"))


def _repo_files() -> List[Path]:
    """Tracked text files of the checkout (not build or test output).

    This file is left out: its examples spell versions out.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode()
    files = [
        ROOT / name
        for name in listed.split("\0")
        if name and (ROOT / name).is_file() and ROOT / name != Path(__file__).resolve()
    ]
    return sorted(path for path in files if b"\0" not in path.read_bytes())


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

    assert set(BUILD_ARG_VARIABLES) <= set(defaults)


@pytest.mark.parametrize("variable", BUILD_ARG_VARIABLES)
def test_tool_version_appears_only_in_its_bake_variable(variable: str) -> None:
    value = _bake_defaults()[variable]
    # A bare major ("18") also matches unrelated numbers; the ARG check covers it.
    if "." not in value:
        pytest.skip(f"{variable}={value} is not a dotted version")

    elsewhere = {
        str(path.relative_to(ROOT)): uses
        for path in _dockerfiles() + _templates()
        if (
            uses := [
                line
                for line in _version_uses(value, path.read_text())
                if not _is_pinned_tag_line(path, line)
            ]
        )
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
    pattern = re.compile(rf"^ARG ({'|'.join(BUILD_ARG_VARIABLES)})=", re.MULTILINE)
    defaults = {
        str(path.relative_to(ROOT)): pattern.findall(path.read_text())
        for path in _dockerfiles()
    }

    assert {name: found for name, found in defaults.items() if found} == {}


def test_tool_version_is_passed_to_a_build() -> None:
    bake = BAKE.read_text()
    passed = set(re.findall(r"^\s+(\w+) = (?:\"\$\{)?\1\b", bake, re.MULTILINE))

    assert set(BUILD_ARG_VARIABLES) <= passed


def test_templates_and_dockerfiles_name_no_literal_image_tag() -> None:
    literal = {
        str(path.relative_to(ROOT)): tags
        for path in _dockerfiles() + _templates()
        if (tags := LITERAL_IMAGE_TAG.findall(path.read_text()))
    }

    assert literal == {}


@pytest.mark.parametrize(
    "text,tags",
    [
        ('EMSCRIPTEN_IMAGE: "registry/hive/common-ci-configuration/emsdk:5.0.2-4"', ["5.0.2-4"]),
        ("  image: node:20", ["20"]),
        ("FROM registry/mirrors/alpine-3.17.0 as build", ["3.17.0"]),
        ("  image: alpine:3.17.0", ["3.17.0"]),
        ('curl -fsSLO "https://download.docker.com/linux/static/stable/x86_64/docker-28.0.1.tgz"', ["28.0.1.tgz"]),
        ("node-v24.21.0-linux-x64.tar.xz", ["v24.21.0-linux-x64.tar.xz"]),
        ("emsdk:5.0.2-6@sha256:9aec", ["5.0.2-6"]),
        ("FROM registry/mirrors/alpine-${ALPINE_VERSION}", []),
        ('image: "${NODE_TEST_TOOLS_IMAGE}"', []),
        ("registry/hive/common-ci-configuration/emsdk:$EMSCRIPTEN_IMAGE_TAG", []),
        ("FROM registry/mirrors/docker-${DOCKER_VERSION}-cli AS docker-cli", []),
        ("FROM registry/mirrors/openresty-openresty-alpine", []),
        ("node-test-tools:develop", []),
        ('DOCKER_HOST: "tcp://docker:2375"', []),
    ],
)
def test_literal_image_tag_finds_versioned_image_names(text: str, tags: List[str]) -> None:
    assert LITERAL_IMAGE_TAG.findall(text) == tags


@pytest.mark.parametrize("variable", TEMPLATE_PINNED_TAGS)
def test_template_pinned_tag_is_not_ahead_of_bake_tag(variable: str) -> None:
    template, target = TEMPLATE_PINNED_TAGS[variable]
    pinned = _load_template(ROOT / template)["variables"][variable]

    assert _tag_order(pinned) <= _tag_order(_bake_target_tag(target))


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        ("5.0.2-7", "5.0.2-8"),
        ("5.0.2-9", "5.0.2-10"),
        ("5.0.2-8", "5.0.3-1"),
        ("3.21.3", "3.22"),
        ("5.0.2-7@sha256:9aec", "5.0.2-8"),
    ],
)
def test_tag_order_orders_tags_of_one_image(earlier: str, later: str) -> None:
    assert _tag_order(earlier) < _tag_order(later)


@pytest.mark.parametrize("variable", TEMPLATE_RESTATED_VERSIONS)
def test_template_restated_version_equals_bake_version(variable: str) -> None:
    template, bake_variable = TEMPLATE_RESTATED_VERSIONS[variable]

    assert (
        _load_template(ROOT / template)["variables"][variable]
        == _bake_defaults()[bake_variable].lstrip("v")
    )


def _docker_build_args(tmp_path: Path, job_variables: Dict[str, str]) -> Dict[str, str]:
    """Build args the docker build job passes (the last of a name wins, as in
    docker), running its script against a stub docker under the templates'
    variables overridden by `job_variables`."""
    jobs = _load_jobs()
    stub = tmp_path / "bin" / "docker"
    stub.parent.mkdir()
    stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >> "$DOCKER_ARGS_LOG"\n')
    stub.chmod(0o755)
    log = tmp_path / "docker-args"
    variables = {
        **_template_variables(),
        **_resolved(jobs, DOCKER_BUILD_JOB, "variables"),
        **job_variables,
    }
    env = {
        "PATH": f"{stub.parent}:/usr/bin:/bin",
        "DOCKER_ARGS_LOG": str(log),
        "CI_REGISTRY": "registry.example",
        "CI_REGISTRY_IMAGE": "registry.example/hive/app",
        "CI_COMMIT_SHORT_SHA": "abcd1234",
        **{name: _expand(str(value), variables) for name, value in variables.items()},
    }
    script = "\n".join(_commands(_resolved(jobs, DOCKER_BUILD_JOB, "script")))
    subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, check=True, capture_output=True)

    args = log.read_text().splitlines()
    return dict(
        value.split("=", 1) for flag, value in zip(args, args[1:]) if flag == "--build-arg"
    )


def test_docker_build_passes_ccc_node_version(tmp_path: Path) -> None:
    node = _bake_defaults()["NODE_VERSION"].lstrip("v")

    assert _docker_build_args(tmp_path, {})["NODE_VERSION"] == node


def test_docker_build_args_override_ccc_node_version(tmp_path: Path) -> None:
    args = _docker_build_args(tmp_path, {"DOCKER_BUILD_ARGS": "NODE_VERSION=22.1.0 OTHER=x"})

    assert args == {"NODE_VERSION": "22.1.0", "OTHER": "x"}


@pytest.mark.parametrize("variable", TEMPLATE_RESTATED_VERSIONS)
def test_template_restated_version_equals_bake_version(variable: str) -> None:
    template, bake_variable = TEMPLATE_RESTATED_VERSIONS[variable]

    assert (
        _load_template(ROOT / template)["variables"][variable]
        == _bake_defaults()[bake_variable].lstrip("v")
    )


def _docker_build_args(tmp_path: Path, job_variables: Dict[str, str]) -> Dict[str, str]:
    """Build args the docker build job passes (the last of a name wins, as in
    docker), running its script against a stub docker under the templates'
    variables overridden by `job_variables`."""
    jobs = _load_jobs()
    stub = tmp_path / "bin" / "docker"
    stub.parent.mkdir()
    stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >> "$DOCKER_ARGS_LOG"\n')
    stub.chmod(0o755)
    log = tmp_path / "docker-args"
    variables = {
        **_template_variables(),
        **_resolved(jobs, DOCKER_BUILD_JOB, "variables"),
        **job_variables,
    }
    env = {
        "PATH": f"{stub.parent}:/usr/bin:/bin",
        "DOCKER_ARGS_LOG": str(log),
        "CI_REGISTRY": "registry.example",
        "CI_REGISTRY_IMAGE": "registry.example/hive/app",
        "CI_COMMIT_SHORT_SHA": "abcd1234",
        **{name: _expand(str(value), variables) for name, value in variables.items()},
    }
    script = "\n".join(_commands(_resolved(jobs, DOCKER_BUILD_JOB, "script")))
    subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, check=True, capture_output=True)

    args = log.read_text().splitlines()
    return dict(
        value.split("=", 1) for flag, value in zip(args, args[1:]) if flag == "--build-arg"
    )


def test_docker_build_passes_ccc_node_version(tmp_path: Path) -> None:
    node = _bake_defaults()["NODE_VERSION"].lstrip("v")

    assert _docker_build_args(tmp_path, {})["NODE_VERSION"] == node


def test_docker_build_args_override_ccc_node_version(tmp_path: Path) -> None:
    args = _docker_build_args(tmp_path, {"DOCKER_BUILD_ARGS": "NODE_VERSION=22.1.0 OTHER=x"})

    assert args == {"NODE_VERSION": "22.1.0", "OTHER": "x"}


def test_bake_target_tag_expands_bake_variables() -> None:
    defaults = _bake_defaults()

    assert _bake_target_tag("emsdk") == f"{defaults['EMSCRIPTEN_VERSION']}-13"
    assert _bake_target_tag("alpine") == defaults["ALPINE_VERSION"]


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


def test_pnpm_catalog_matches_bake_versions() -> None:
    drift = _catalog_drift(_catalogs(), _bake_defaults(), CATALOG_EXCEPTIONS)

    assert not drift, "pnpm catalog differs from docker-bake.hcl:\n" + "\n".join(drift)


def test_catalog_exceptions_name_compared_packages_with_a_reason() -> None:
    assert {
        key for key, reason in CATALOG_EXCEPTIONS.items() if key in CATALOG_PACKAGES and reason.strip()
    } == set(CATALOG_EXCEPTIONS)


def test_catalog_exceptions_are_still_differences() -> None:
    drift = _catalog_drift(_catalogs(), _bake_defaults(), {})

    assert [
        key for key in CATALOG_EXCEPTIONS if not any(d.startswith(f"{key[0]} {key[1]}:") for d in drift)
    ] == []


_FIXTURE_CATALOGS = {
    "playwright-toolset": {"@playwright/test": "1.64.1", "playwright": "1.64.1"},
    "typescript-toolset": {"typescript": "7.0.2"},
}
_FIXTURE_BAKE = {"PLAYWRIGHT_VERSION": "1.64.1", "TYPESCRIPT_VERSION": "7.0.2"}


def test_matching_catalog_has_no_drift() -> None:
    assert not _catalog_drift(_FIXTURE_CATALOGS, _FIXTURE_BAKE, {})


@pytest.mark.parametrize(
    "catalogs,bake,expected",
    [
        (
            {**_FIXTURE_CATALOGS, "playwright-toolset": {"@playwright/test": "1.64.1", "playwright": "1.59.1"}},
            _FIXTURE_BAKE,
            ["playwright-toolset playwright: pnpm catalog 1.59.1, docker-bake.hcl PLAYWRIGHT_VERSION=1.64.1"],
        ),
        (
            _FIXTURE_CATALOGS,
            {**_FIXTURE_BAKE, "PLAYWRIGHT_VERSION": "1.65.0"},
            [
                "playwright-toolset playwright: pnpm catalog 1.64.1, docker-bake.hcl PLAYWRIGHT_VERSION=1.65.0",
                "playwright-toolset @playwright/test: pnpm catalog 1.64.1,"
                " docker-bake.hcl PLAYWRIGHT_VERSION=1.65.0",
            ],
        ),
    ],
)
def test_catalog_or_bake_change_alone_is_reported_with_both_versions(
    catalogs: Dict[str, Any], bake: Dict[str, str], expected: List[str]
) -> None:
    assert _catalog_drift(catalogs, bake, {}) == expected


def test_a_recorded_catalog_exception_is_not_drift() -> None:
    catalogs = {**_FIXTURE_CATALOGS, "typescript-toolset": {"typescript": "5.7.3"}}
    key = ("typescript-toolset", "typescript")

    assert _catalog_drift(catalogs, _FIXTURE_BAKE, {}) == [
        "typescript-toolset typescript: pnpm catalog 5.7.3, docker-bake.hcl TYPESCRIPT_VERSION=7.0.2"
    ]
    assert not _catalog_drift(catalogs, _FIXTURE_BAKE, {key: "reason"})
