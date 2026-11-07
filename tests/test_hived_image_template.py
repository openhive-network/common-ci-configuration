"""templates/hived_image.gitlab-ci.yml: the hived lookup jobs run as GitLab would run them.

A job's before_script and script (extends and !reference resolved) run in bash against a
local hive-like repo, with curl serving hive's source-patterns.sh, wget serving this
repository's scripts and docker answering `manifest inspect` from an allowlist.
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
REGISTRY = "registry.gitlab.syncad.com/hive/hive"
PATTERNS_URL = "https://gitlab.syncad.com/hive/hive/-/raw/{ref}/scripts/ci-helpers/source-patterns.sh"
MAINNET = ".find_mainnet_hived_image_template"
TESTNET = ".find_testnet_hived_image_template"

SOURCE_PATTERNS_SH = """#!/bin/sh
echo "libraries/,programs/"
"""

STUB_CURL = """#!/bin/sh
for url; do :; done
echo "$url" >> "$CURL_STUB_LOG"
for ref in $CURL_STUB_REFS; do
    if [ "$url" = "https://gitlab.syncad.com/hive/hive/-/raw/$ref/scripts/ci-helpers/source-patterns.sh" ]; then
        cat "$CURL_STUB_PATTERNS"
        exit 0
    fi
done
exit 22
"""

STUB_WGET = """#!/bin/sh
# wget -q -O <dest> <url>
cp "$WGET_STUB_SCRIPTS/$(basename "$4")" "$3"
"""

STUB_DOCKER = """#!/bin/sh
if [ "$1" = manifest ] && [ "$2" = inspect ]; then
    grep -qxF "$3" "$DOCKER_STUB_ALLOWLIST"
    exit $?
fi
echo "unexpected docker call: $*" >&2
exit 2
"""


class _Reference(List[Any]):
    """A GitLab `!reference [job, key, ...]` path."""


# Built with type() because PyYAML ships no type stubs to subclass from
_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor(
    "!reference", lambda loader, node: _Reference(loader.construct_sequence(node))
)


def _load_jobs() -> Dict[str, Any]:
    jobs: Dict[str, Any] = {}
    for name in ("source_change_detection.gitlab-ci.yml", "hived_image.gitlab-ci.yml"):
        jobs.update(yaml.load((TEMPLATES / name).read_text(), Loader=_Loader))
    return jobs


def _merged(jobs: Dict[str, Any], name: str) -> Dict[str, Any]:
    """The job after `extends`: hashes merged deeply, other values replaced (as GitLab does)."""
    job = jobs[name]
    parents = job.get("extends", [])
    merged: Dict[str, Any] = {}
    for parent in [parents] if isinstance(parents, str) else parents:
        merged = _deep_merge(merged, _merged(jobs, parent))
    return _deep_merge(merged, {k: v for k, v in job.items() if k != "extends"})


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _commands(jobs: Dict[str, Any], value: Any) -> List[str]:
    if isinstance(value, _Reference):
        target: Any = jobs
        for part in value:
            target = target[part]
        return _commands(jobs, target)
    if isinstance(value, list):
        return [cmd for item in value for cmd in _commands(jobs, item)]
    return [value]


@pytest.fixture(name="hive_repo")
def _hive_repo(tmp_path: Path) -> Tuple[Path, Dict[str, str]]:
    """develop with a source commit, then a unit-test commit, then a docs commit."""
    repo = tmp_path / "hive"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "develop")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    commits: Dict[str, str] = {}
    for label, path in (("source", "libraries/chain.cpp"), ("unit", "tests/unit/chain_test.cpp"),
                        ("docs", "doc/readme.md")):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(f"{label}\n")
        git("add", ".")
        git("commit", "-q", "-m", label)
        commits[label] = git("rev-parse", "--short=8", "HEAD")
    return repo, commits


def _run_job(tmp_path: Path, job: str, available: List[str], variables: Dict[str, str],
             pattern_refs: str = "develop") -> Tuple["subprocess.CompletedProcess[str]", Dict[str, str], List[str]]:
    """Run `job` in tmp_path; return the process, its dotenv report and the URLs curl got."""
    jobs = _load_jobs()
    resolved = _merged(jobs, job)
    script = "\n".join(_commands(jobs, resolved["before_script"]) + _commands(jobs, resolved["script"]))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool, body in (("curl", STUB_CURL), ("wget", STUB_WGET), ("docker", STUB_DOCKER)):
        (bin_dir / tool).write_text(body)
        (bin_dir / tool).chmod(0o755)
    (tmp_path / "allowlist").write_text("".join(f"{ref}\n" for ref in available))
    (tmp_path / "source-patterns.sh").write_text(SOURCE_PATTERNS_SH)
    curl_log = tmp_path / "curl.log"
    curl_log.touch()

    env = {
        **os.environ,
        **{k: str(v) for k, v in resolved["variables"].items()},
        **variables,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CURL_STUB_LOG": str(curl_log),
        "CURL_STUB_REFS": pattern_refs,
        "CURL_STUB_PATTERNS": str(tmp_path / "source-patterns.sh"),
        "WGET_STUB_SCRIPTS": str(ROOT / "scripts" / "bash"),
        "DOCKER_STUB_ALLOWLIST": str(tmp_path / "allowlist"),
    }
    proc = subprocess.run(["bash", "-c", "set -eo pipefail\n" + script], env=env, cwd=tmp_path,
                          capture_output=True, text=True, check=False)
    dotenv = tmp_path / resolved["artifacts"]["reports"]["dotenv"]
    report: Dict[str, str] = {}
    if dotenv.exists():
        report = dict(line.split("=", 1) for line in dotenv.read_text().splitlines() if line)
    return proc, report, curl_log.read_text().splitlines()


@pytest.mark.parametrize("job", [".find_hived_image", MAINNET, TESTNET])
def test_job_writes_the_file_its_dotenv_report_names(job: str) -> None:
    resolved = _merged(_load_jobs(), job)

    assert resolved["variables"]["UPSTREAM_OUTPUT"] == resolved["artifacts"]["reports"]["dotenv"]
    assert resolved["stage"] == ".pre"
    assert resolved["needs"] == []


def test_mainnet_finds_image_of_last_source_commit(tmp_path: Path, hive_repo: Tuple[Path, Dict[str, str]]) -> None:
    repo, commits = hive_repo
    proc, report, urls = _run_job(
        tmp_path, MAINNET,
        [f"{REGISTRY}:{commits['source']}", f"{REGISTRY}/testnet:{commits['unit']}"],
        {"UPSTREAM_REPO_URL": f"file://{repo}"},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert urls == [PATTERNS_URL.format(ref="develop")]
    assert report["HIVED_UPSTREAM_IMAGE"] == f"{REGISTRY}:{commits['source']}"
    assert report["HIVED_UPSTREAM_TAG"] == commits["source"]
    assert report["HIVED_UPSTREAM_CACHE_HIT"] == "true"
    assert not any(key.startswith("UPSTREAM_") for key in report)


def test_testnet_counts_unit_tests_as_source(tmp_path: Path, hive_repo: Tuple[Path, Dict[str, str]]) -> None:
    repo, commits = hive_repo
    proc, report, _ = _run_job(
        tmp_path, TESTNET,
        [f"{REGISTRY}/testnet:{commits['source']}", f"{REGISTRY}/testnet:{commits['unit']}"],
        {"UPSTREAM_REPO_URL": f"file://{repo}"},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert report["HIVED_TESTNET_UPSTREAM_IMAGE"] == f"{REGISTRY}/testnet:{commits['unit']}"
    assert report["HIVED_TESTNET_UPSTREAM_REGISTRY"] == f"{REGISTRY}/testnet"
    assert "HIVED_UPSTREAM_IMAGE" not in report


def test_override_tag_skips_lookup(tmp_path: Path) -> None:
    proc, report, urls = _run_job(
        tmp_path, MAINNET, [f"{REGISTRY}:v1.27.0"],
        {"HIVED_OVERRIDE_TAG": "v1.27.0", "UPSTREAM_REPO_URL": "file:///nonexistent"},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert urls == []
    assert report["HIVED_UPSTREAM_IMAGE"] == f"{REGISTRY}:v1.27.0"
    assert report["HIVED_UPSTREAM_OVERRIDE"] == "true"


def test_override_tag_missing_from_registry_fails(tmp_path: Path) -> None:
    proc, _, _ = _run_job(tmp_path, TESTNET, [], {"HIVED_OVERRIDE_TAG": "v1.27.0"})

    assert proc.returncode != 0
    assert f"Override image not found: {REGISTRY}/testnet:v1.27.0" in proc.stdout


def test_patterns_ref_without_patterns_falls_back_to_develop(
        tmp_path: Path, hive_repo: Tuple[Path, Dict[str, str]]) -> None:
    repo, commits = hive_repo
    proc, report, urls = _run_job(
        tmp_path, MAINNET, [f"{REGISTRY}:{commits['source']}"],
        {"UPSTREAM_REPO_URL": f"file://{repo}", "HIVED_PATTERNS_REF": "old-release"},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert urls == [PATTERNS_URL.format(ref="old-release"), PATTERNS_URL.format(ref="develop")]
    assert "HIVED_PATTERNS_REF 'old-release' has no source-patterns.sh" in proc.stdout
    assert report["HIVED_UPSTREAM_IMAGE"] == f"{REGISTRY}:{commits['source']}"


def test_unreachable_patterns_fail_the_job(tmp_path: Path, hive_repo: Tuple[Path, Dict[str, str]]) -> None:
    repo, commits = hive_repo
    proc, report, _ = _run_job(
        tmp_path, MAINNET, [f"{REGISTRY}:{commits['source']}"],
        {"UPSTREAM_REPO_URL": f"file://{repo}"}, pattern_refs="",
    )

    assert proc.returncode != 0
    assert "ERROR: Could not fetch source-patterns.sh from hive" in proc.stdout
    assert report == {}


def test_no_image_fails_the_job(tmp_path: Path, hive_repo: Tuple[Path, Dict[str, str]]) -> None:
    repo, _ = hive_repo
    proc, _, _ = _run_job(tmp_path, MAINNET, [], {"UPSTREAM_REPO_URL": f"file://{repo}"})

    assert proc.returncode != 0
    assert "No upstream image available" in proc.stderr
