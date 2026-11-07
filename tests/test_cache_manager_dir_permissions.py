"""cache-manager put: the NFS cache directories it creates are usable by every job UID,
and a directory it cannot make world-accessible fails the put instead of being ignored."""

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_MANAGER = ROOT / "scripts" / "cache-manager.sh"


def _put(tmp_path: Path, extra_path: str = "") -> "subprocess.CompletedProcess[str]":
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "data").write_text("payload\n")
    path = f"{extra_path}{os.pathsep}{os.environ['PATH']}" if extra_path else os.environ["PATH"]
    env = {**os.environ, "PATH": path,
           "CACHE_NFS_PATH": str(tmp_path / "nfs"), "CACHE_LOCAL_PATH": str(tmp_path / "local")}
    # umask 077 is what a root CI job runs with
    return subprocess.run(
        ["bash", "-c", 'umask 077 && exec bash "$0" put myapp key1 "$1"', str(CACHE_MANAGER), str(source)],
        env=env, capture_output=True, text=True, check=False,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_put_under_restrictive_umask_leaves_every_cache_dir_world_accessible(tmp_path: Path) -> None:
    (tmp_path / "nfs").mkdir()
    (tmp_path / "local").mkdir()

    result = _put(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "nfs" / "myapp" / "key1.tar").is_file()
    assert _mode(tmp_path / "nfs" / "myapp") == 0o777
    assert _mode(tmp_path / "nfs" / "myapp" / "key1") == 0o777


def test_put_fails_when_cache_type_dir_cannot_be_made_world_accessible(tmp_path: Path) -> None:
    (tmp_path / "nfs" / "myapp").mkdir(parents=True)
    (tmp_path / "nfs" / "myapp").chmod(0o700)
    (tmp_path / "local").mkdir()
    # Stand in for a directory owned by another UID: neither chmod nor sudo chmod succeeds
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("chmod", "sudo"):
        (bin_dir / tool).write_text("#!/bin/sh\nexit 1\n")
        (bin_dir / tool).chmod(0o755)

    result = _put(tmp_path, str(bin_dir))

    assert result.returncode != 0
    assert f"Cache directory {tmp_path / 'nfs' / 'myapp'} is not world-accessible (mode 700" in result.stderr
    assert not (tmp_path / "nfs" / "myapp" / "key1.tar").exists()
