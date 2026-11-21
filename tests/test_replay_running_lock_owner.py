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


def test_build_data_leaves_replay_running_of_a_running_extraction(tmp_path: Path) -> None:
    data_cache = tmp_path / "cache"
    data_cache.mkdir()
    lock = data_cache / "replay_running"
    lock.write_text("1111\n")
    # Extracted from a cache tar with its original mtime, it looks hours old
    os.utime(lock, (0, 0))
    extract_lock = tmp_path / "cache.lock"
    extract_lock.touch()
    env = {**os.environ, "CI_JOB_ID": "3333", "MAX_LOCK_WAIT": "1"}

    result = subprocess.run(
        ["flock", "-x", str(extract_lock), "bash", str(BUILD_DATA), "img:tag",
         f"--data-cache={data_cache}", "--run-script=/bin/true"],
        env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1
    assert "cache-manager is still extracting into" in result.stdout
    assert lock.read_text() == "1111\n"


def _cache_lookup_step(job: str, cache_manager: Path) -> str:
    templates = yaml.load(TEMPLATE.read_text(), Loader=_GitLabLoader)
    step: str = templates[job]["script"][1]
    return step.replace("/tmp/cache-manager.sh", str(cache_manager))


def _stub(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize(("get_status", "replays"), [(2, False), (1, True)])
def test_cache_lookup_replays_only_on_a_miss(tmp_path: Path, job: str, get_status: int, replays: bool) -> None:
    cache_manager = _stub(tmp_path / "cache-manager.sh", f'[[ "$1" == get ]] && exit {get_status}; exit 0')
    replayed = tmp_path / "replayed"
    build_data = _stub(tmp_path / "build_data.sh", f'touch "{replayed}"')
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _stub(scripts / "run_hived_img.sh", "exit 0")
    # The hive job fetches run_hived_img.sh; a curl stub keeps the test offline
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "curl", 'out=""; while [[ $# -gt 0 ]]; do [[ "$1" == -o ]] && out="$2"; shift; done; : > "$out"')
    data_cache = tmp_path / "data"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "DATA_CACHE_DIR": str(data_cache), "HAF_COMMIT": "abc", "HIVE_COMMIT": "abc", "HAF_CACHE_TYPE": "haf",
           "CACHE_NFS_PATH": str(tmp_path / "nfs"), "BUILD_DATA_SCRIPT": str(build_data),
           "SCRIPTS_PATH": str(scripts), "HIVE_SCRIPTS_REF": "develop", "CI_PROJECT_DIR": str(tmp_path)}

    result = subprocess.run(["bash", "-eo", "pipefail", "-c", _cache_lookup_step(job, cache_manager)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, check=False)

    assert replayed.exists() == replays, result.stdout + result.stderr
    if replays:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1
        assert "still extracting" in result.stdout
        assert not (data_cache / "datadir").exists()
