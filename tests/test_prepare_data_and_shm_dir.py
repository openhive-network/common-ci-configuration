"""prepare_data_and_shm_dir.sh ownership step, run as the current (non-root) user."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prepare_data_and_shm_dir.sh"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="exercises the non-root paths")


def _run(tmp_path: Path, hived_uid: int, sudo_script: str) -> "subprocess.CompletedProcess[str]":
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    sudo = bin_dir / "sudo"
    sudo.write_text(sudo_script)
    sudo.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HIVED_UID": str(hived_uid),
        "SUDO_LOG": str(tmp_path / "sudo.log"),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), f"--data-base-dir={tmp_path / 'data'}",
         "--block-log-source-dir=", "--config-ini-source="],
        env=env, capture_output=True, text=True, check=False,
    )


def test_owner_matching_hived_uid_skips_chown(tmp_path: Path) -> None:
    proc = _run(tmp_path, os.getuid(), "#!/bin/sh\necho \"$@\" >> \"$SUDO_LOG\"\nexit 1\n")
    assert proc.returncode == 0, proc.stderr
    for name in ("datadir", "shm_dir"):
        assert (tmp_path / "data" / name).stat().st_uid == os.getuid()
    assert not (tmp_path / "sudo.log").exists()


def test_uid_mismatch_uses_sudo_chown(tmp_path: Path) -> None:
    proc = _run(tmp_path, os.getuid() + 1, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SUDO_LOG\"\n")
    assert proc.returncode == 0, proc.stderr
    data = tmp_path / "data"
    uid = os.getuid() + 1
    assert (tmp_path / "sudo.log").read_text() == (
        f"-n chown -R {uid}:{uid} {data / 'datadir'} {data / 'shm_dir'}\n"
    )


def test_uid_mismatch_without_sudo_names_both_uids(tmp_path: Path) -> None:
    uid = os.getuid() + 1
    proc = _run(tmp_path, uid, "#!/bin/sh\nexit 1\n")
    assert proc.returncode == 1
    assert f"ERROR: running as uid {os.getuid()}, not root and not HIVED_UID {uid}" in proc.stdout
