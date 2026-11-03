"""replay_running lock: build_data.sh records its job id, the data-preparation after_script
releases the lock only for that job and leaves another job's lock in place."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BUILD_DATA = ROOT / "scripts" / "build_data.sh"
TEMPLATE = ROOT / "templates" / "haf_data_preparation.gitlab-ci.yml"
JOBS = [".prepare_haf_data_5m", ".prepare_hived_data_5m"]


# Built with type() because PyYAML ships no type stubs to subclass from
_GitLabLoader: Any = type("_GitLabLoader", (yaml.SafeLoader,), {})
_GitLabLoader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _lock_release_step(job: str) -> str:
    templates = yaml.load(TEMPLATE.read_text(), Loader=_GitLabLoader)
    step: str = templates[job]["after_script"][0]
    return step


def _run_after_script(job: str, data_cache: Path, job_id: str) -> "subprocess.CompletedProcess[str]":
    env = {**os.environ, "DATA_CACHE_DIR": str(data_cache), "CI_JOB_ID": job_id}
    return subprocess.run(["bash", "-c", _lock_release_step(job)], env=env,
                          capture_output=True, text=True, check=True)


@pytest.mark.parametrize("job", JOBS)
def test_after_script_leaves_another_jobs_lock(tmp_path: Path, job: str) -> None:
    lock = tmp_path / "replay_running"
    lock.write_text("1111\n")

    result = _run_after_script(job, tmp_path, "2222")

    assert lock.read_text() == "1111\n"
    assert "belongs to another job (1111)" in result.stdout


@pytest.mark.parametrize("job", JOBS)
def test_after_script_removes_own_lock(tmp_path: Path, job: str) -> None:
    lock = tmp_path / "replay_running"
    lock.write_text("2222\n")

    result = _run_after_script(job, tmp_path, "2222")

    assert not lock.exists()
    assert f"Lock file removed: {lock}" in result.stdout


@pytest.mark.parametrize("job", JOBS)
def test_after_script_leaves_ownerless_lock(tmp_path: Path, job: str) -> None:
    lock = tmp_path / "replay_running"
    lock.write_text("")

    _run_after_script(job, tmp_path, "2222")

    assert lock.exists()


def test_build_data_writes_job_id_into_lock(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # curl is the first network step after the lock is taken; failing it stops the script there
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\nexit 1\n")
    curl.chmod(0o755)
    data_cache = tmp_path / "cache"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "CI_JOB_ID": "3333"}

    result = subprocess.run(
        ["bash", str(BUILD_DATA), "img:tag", f"--data-cache={data_cache}", "--run-script=/bin/true"],
        env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert (data_cache / "replay_running").read_text() == "3333\n"
    _run_after_script(JOBS[0], data_cache, "3333")
    assert not (data_cache / "replay_running").exists()
