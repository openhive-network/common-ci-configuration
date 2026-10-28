"""The `.npm_based_job_base` install: frozen, unfrozen only on a ccc catalog change.

The template's install script is run as GitLab's bash shell runs it, against a
fake `pnpm` that fails the frozen install the way pnpm does.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
REFRESHED_LOCKFILE = "pnpm-lock.catalog-refreshed.yaml"

FAKE_PNPM = """#!/bin/sh
echo "$*" >> "$PNPM_CALLS"
case "$*" in
  *--frozen-lockfile*)
    case "$FROZEN_RESULT" in
      ok) echo "Lockfile is up to date" ;;
      mismatch)
        echo ' ERR_PNPM_LOCKFILE_CONFIG_MISMATCH  Cannot proceed with the frozen installation. The current "catalogs" configuration doesn'"'"'t match the value found in the lockfile' >&2
        exit 1 ;;
      outdated)
        echo ' ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with "frozen-lockfile" because pnpm-lock.yaml is not up to date with package.json' >&2
        exit 1 ;;
    esac ;;
  *--no-frozen-lockfile*) echo "refreshed" > pnpm-lock.yaml ;;
esac
"""


class _Reference(List[Any]):
    """A GitLab `!reference [job, key, ...]` path."""


_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor(
    "!reference", lambda loader, node: _Reference(loader.construct_sequence(node))
)


def _jobs() -> Dict[str, Any]:
    jobs: Dict[str, Any] = {}
    for path in sorted(TEMPLATES.glob("*.gitlab-ci.yml")):
        jobs.update(yaml.load(path.read_text(), Loader=_Loader) or {})
    return jobs


def _commands(jobs: Dict[str, Any], value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, _Reference):
        target: Any = jobs
        for part in value:
            target = target.get(part) if isinstance(target, dict) else None
        return _commands(jobs, target)
    if isinstance(value, list):
        return [cmd for item in value for cmd in _commands(jobs, item)]
    return []


def _resolve(jobs: Dict[str, Any], name: str, key: str) -> Any:
    job = jobs[name]
    if key in job:
        return job[key]
    parents = job.get("extends", [])
    for parent in reversed([parents] if isinstance(parents, str) else parents):
        if parent in jobs:
            value = _resolve(jobs, parent, key)
            if value is not None:
                return value
    return None


def _extends_npm_base(jobs: Dict[str, Any], name: str) -> bool:
    parents = jobs[name].get("extends", [])
    for parent in [parents] if isinstance(parents, str) else parents:
        if parent == ".npm_based_job_base" or (parent in jobs and _extends_npm_base(jobs, parent)):
            return True
    return False


def _run_install(
    tmp_path: Path, frozen_result: str, shell_opts: str, extra_env: Optional[Dict[str, str]] = None
) -> "subprocess.CompletedProcess[str]":
    jobs = _jobs()
    script = "\n".join(_commands(jobs, _Reference([".pnpm_install_with_catalog_fallback", "script"])))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(FAKE_PNPM)
    pnpm.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "pnpm-lock.yaml").write_text("committed\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PNPM_CALLS": str(tmp_path / "pnpm.calls"),
        "FROZEN_RESULT": frozen_result,
        "CI_PROJECT_DIR": str(project),
        "TMPDIR": str(tmp_path),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", "-c", f"{shell_opts}\n{script}"],
        cwd=project, env=env, capture_output=True, text=True, check=False,
    )


def _calls(tmp_path: Path) -> List[str]:
    return (tmp_path / "pnpm.calls").read_text().splitlines()


# GitLab's bash shell runs with errexit and pipefail; the script must not depend on either.
SHELL_OPTS = pytest.mark.parametrize("shell_opts", ["set -eo pipefail", "set +e +o pipefail"])


@SHELL_OPTS
def test_matching_lockfile_installs_once_frozen(tmp_path: Path, shell_opts: str) -> None:
    proc = _run_install(tmp_path, "ok", shell_opts)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _calls(tmp_path) == ["--recursive install --frozen-lockfile"]
    assert "ERR_PNPM_LOCKFILE_CONFIG_MISMATCH" not in proc.stdout
    assert not (tmp_path / "project" / REFRESHED_LOCKFILE).exists()


@SHELL_OPTS
def test_catalog_mismatch_falls_back_to_unfrozen_install(tmp_path: Path, shell_opts: str) -> None:
    proc = _run_install(tmp_path, "mismatch", shell_opts)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _calls(tmp_path) == [
        "--recursive install --frozen-lockfile",
        "--recursive install --no-frozen-lockfile",
    ]
    assert "The ccc catalog changed" in proc.stdout
    assert (tmp_path / "project" / REFRESHED_LOCKFILE).read_text() == "refreshed\n"


@SHELL_OPTS
def test_outdated_lockfile_still_fails(tmp_path: Path, shell_opts: str) -> None:
    proc = _run_install(tmp_path, "outdated", shell_opts)
    assert proc.returncode == 1
    assert "ERR_PNPM_OUTDATED_LOCKFILE" in proc.stdout
    assert _calls(tmp_path) == ["--recursive install --frozen-lockfile"]
    assert not (tmp_path / "project" / REFRESHED_LOCKFILE).exists()


def test_npm_jobs_log_submodules_and_keep_the_refreshed_lockfile() -> None:
    jobs = _jobs()
    npm_jobs = [name for name in jobs if isinstance(jobs[name], dict) and _extends_npm_base(jobs, name)]
    assert npm_jobs
    for name in [".npm_based_job_base", *npm_jobs]:
        before_script = _commands(jobs, _resolve(jobs, name, "before_script"))
        assert any("submodule status --recursive" in cmd for cmd in before_script), name
        assert any("--frozen-lockfile" in cmd for cmd in before_script), name
        artifacts = _resolve(jobs, name, "artifacts") or {}
        assert REFRESHED_LOCKFILE in artifacts.get("paths", []), name


def _monorepo(tmp_path: Path, react: str, workspace: str) -> Dict[str, str]:
    """A monorepo with its own workspace file and a ccc submodule pinning `react`; its job env."""
    ccc = tmp_path / "project" / "ccc"
    (ccc / "scripts" / "python").mkdir(parents=True)
    (ccc / "pnpm-config").mkdir()
    for script in ("pnpm-catalog-sync.py", "pnpm_catalogs.py"):
        shutil.copy(ROOT / "scripts" / "python" / script, ccc / "scripts" / "python" / script)
    (ccc / "pnpm-config" / "pnpm-workspace.yaml").write_text(f"catalogs:\n  react-toolset:\n    react: {react}\n")
    (tmp_path / "project" / "pnpm-workspace.yaml").write_text(workspace)
    return {"CCC_SUBMODULE_DIR": "ccc"}


@SHELL_OPTS
def test_monorepo_merges_bumped_ccc_catalog_then_falls_back(tmp_path: Path, shell_opts: str) -> None:
    env = _monorepo(tmp_path, "19.3.1", "packages:\n  - apps/*\ncatalogs:\n  local:\n    lodash: 4.17.21\n")

    proc = _run_install(tmp_path, "mismatch", shell_opts, env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    workspace = yaml.safe_load((tmp_path / "project" / "pnpm-workspace.yaml").read_text())
    assert workspace["catalogs"] == {"react-toolset": {"react": "19.3.1"}, "local": {"lodash": "4.17.21"}}
    assert workspace["packages"] == ["apps/*"]
    assert _calls(tmp_path) == [
        "--recursive install --frozen-lockfile",
        "--recursive install --no-frozen-lockfile",
    ]
    assert (tmp_path / "project" / REFRESHED_LOCKFILE).read_text() == "refreshed\n"


@SHELL_OPTS
def test_monorepo_with_unmergeable_workspace_fails_before_installing(tmp_path: Path, shell_opts: str) -> None:
    env = _monorepo(tmp_path, "19.3.1", "catalogs:\n  react-toolset:\n    react: 18.3.1\n")

    proc = _run_install(tmp_path, "ok", shell_opts, env)

    assert proc.returncode == 2
    assert "named like ccc catalogs" in proc.stderr
    assert not (tmp_path / "pnpm.calls").exists()
