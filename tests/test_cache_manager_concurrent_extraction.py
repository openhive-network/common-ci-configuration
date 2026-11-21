"""cache-manager get on a destination another job is extracting into: a waiter that times out
reports "extraction in progress" (exit 2) instead of a miss unless the extraction completed,
a failed extraction leaves no partial tree, and per-run state never travels in a cache tar."""

import io
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parent.parent
CACHE_MANAGER = ROOT / "scripts" / "cache-manager.sh"


def _run(tmp_path: Path, *args: str, **extra_env: str) -> "subprocess.CompletedProcess[str]":
    env = {**os.environ, "CACHE_NFS_PATH": str(tmp_path / "nfs"), "CACHE_LOCAL_PATH": str(tmp_path / "local"),
           "SHARED_BLOCK_LOG_LOCAL": str(tmp_path / "absent"), "SHARED_BLOCK_LOG_NFS": str(tmp_path / "absent"),
           **extra_env}
    return subprocess.run(["bash", str(CACHE_MANAGER), *args], env=env, capture_output=True, text=True, check=False)


def _write_local_tar(tmp_path: Path, members: "dict[str, str]") -> None:
    (tmp_path / "local").mkdir(exist_ok=True)
    with tarfile.open(tmp_path / "local" / "myapp_key1.tar", "w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content.encode())
            info.mtime = 0
            tar.addfile(info, io.BytesIO(content.encode()))


@pytest.fixture
def held_dest_lock(tmp_path: Path) -> Iterator[Path]:
    """The destination of an extraction another job is running: its lock is held."""
    dest = tmp_path / "dest"
    lock = tmp_path / "dest.lock"
    lock.touch()
    holder = subprocess.Popen(["flock", "-x", str(lock), "-c", "echo held; sleep 60"],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None
    assert holder.stdout.readline() == "held\n"
    yield dest
    holder.kill()
    holder.wait()


def test_waiter_timing_out_on_running_extraction_reports_in_progress(tmp_path: Path, held_dest_lock: Path) -> None:
    _write_local_tar(tmp_path, {"./datadir/state": "payload\n"})
    (held_dest_lock / "datadir").mkdir(parents=True)
    (held_dest_lock / "datadir" / "half_written").write_text("x\n")

    result = _run(tmp_path, "get", "myapp", "key1", str(held_dest_lock), CACHE_EXTRACT_LOCK_TIMEOUT="1")

    assert result.returncode == 2, result.stderr
    assert "still in progress (not a cache miss)" in result.stderr
    assert (held_dest_lock / "datadir" / "half_written").read_text() == "x\n"


def test_waiter_timing_out_reuses_extraction_completed_meanwhile(tmp_path: Path, held_dest_lock: Path) -> None:
    _write_local_tar(tmp_path, {"./datadir/state": "payload\n"})
    (held_dest_lock / "datadir").mkdir(parents=True)
    (held_dest_lock / ".extraction_complete").write_text("job_id=1\n")

    result = _run(tmp_path, "get", "myapp", "key1", str(held_dest_lock), CACHE_EXTRACT_LOCK_TIMEOUT="1")

    assert result.returncode == 0, result.stderr
    assert "another job's extraction into" in result.stderr
    assert not (held_dest_lock / "datadir" / "state").exists()


def test_partial_tree_without_marker_is_replaced(tmp_path: Path) -> None:
    _write_local_tar(tmp_path, {"./datadir/state": "payload\n"})
    dest = tmp_path / "dest"
    (dest / "datadir" / "leftover").mkdir(parents=True)

    result = _run(tmp_path, "get", "myapp", "key1", str(dest))

    assert result.returncode == 0, result.stderr
    assert not (dest / "datadir" / "leftover").exists()
    assert (dest / "datadir" / "state").read_text() == "payload\n"
    assert (dest / ".extraction_complete").is_file()


def test_failed_extraction_logs_first_failing_path_and_removes_partial_tree(tmp_path: Path) -> None:
    # ./datadir/sub is a file, so tar cannot create ./datadir/sub/file under it
    _write_local_tar(tmp_path, {"./datadir/state": "payload\n", "./datadir/sub": "file\n",
                                "./datadir/sub/file": "unreachable\n"})
    dest = tmp_path / "dest"

    result = _run(tmp_path, "get", "myapp", "key1", str(dest))

    assert result.returncode == 1, result.stderr
    assert f"First failing path: ./datadir/sub/file; {dest}/datadir/sub is uid {os.getuid()}" in result.stderr
    assert "regular file" in result.stderr
    assert not dest.exists()


def test_extracted_tree_carries_no_replay_running_from_an_old_tar(tmp_path: Path) -> None:
    _write_local_tar(tmp_path, {"./replay_running": "1111\n", "./datadir/state": "payload\n"})
    dest = tmp_path / "dest"

    result = _run(tmp_path, "get", "myapp", "key1", str(dest))

    assert result.returncode == 0, result.stderr
    assert not (dest / "replay_running").exists()
    assert (dest / "datadir" / "state").read_text() == "payload\n"


def test_put_leaves_per_run_state_out_of_the_tar(tmp_path: Path) -> None:
    (tmp_path / "nfs").mkdir()
    (tmp_path / "local").mkdir()
    source = tmp_path / "source"
    (source / "datadir").mkdir(parents=True)
    (source / "datadir" / "state").write_text("payload\n")
    (source / "replay_running").write_text("1111\n")
    (source / "consumers").mkdir()
    (source / "consumers" / "1111").touch()
    (source / ".extraction_complete").write_text("job_id=1\n")

    result = _run(tmp_path, "put", "myapp", "key1", str(source))

    assert result.returncode == 0, result.stderr
    # A CACHE_NFS_PATH that is not an NFS mount makes this the NFS host, storing only the NFS tar
    with tarfile.open(tmp_path / "nfs" / "myapp" / "key1.tar") as tar:
        names = set(tar.getnames())
    assert "./datadir/state" in names
    assert not names & {"./replay_running", "./consumers", "./consumers/1111", "./.extraction_complete"}
