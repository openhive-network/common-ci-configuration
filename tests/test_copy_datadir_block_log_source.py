"""copy_datadir.sh links block_log from the first of BLOCK_LOG_SOURCE_DIR, SHARED_BLOCK_LOG_DIR and
/blockchain/block_log_5m that holds block_log* files, run with stubbed sudo and ln."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "haf-app-tools" / "scripts" / "copy_datadir.sh"
DEFAULT_DIR = "/blockchain/block_log_5m"

# Drops sudo's options (and -u's user) and runs the command as the current user
SUDO_STUB = """#!/bin/bash
while [[ "$1" == -* ]]; do
    [[ "$1" == *u* ]] && shift
    shift
done
exec "$@"
"""
LN_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$LN_LOG"
exec "$REAL_LN" "$@"
"""


def _block_log_dir(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "block_log_part.0001").write_text(f"{name}\n")
    (directory / "block_log_part.0001.artifacts").write_text(f"{name}\n")
    return directory


def _run(tmp_path: Path, default_dir: Path, **block_log_env: str) -> "subprocess.CompletedProcess[str]":
    # The default is a fixed path on the builders; the copy run here points it at default_dir
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "copy_datadir.sh"
    script.write_text(SCRIPT.read_text().replace(f'"{DEFAULT_DIR}"', f'"{default_dir}"'))
    cache_manager = scripts / "cache-manager.sh"
    cache_manager.write_text("#!/bin/sh\nexit 1\n")
    cache_manager.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("sudo", SUDO_STUB), ("ln", LN_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)

    data_source = tmp_path / "data_source"
    (data_source / "datadir").mkdir(parents=True)
    (data_source / "datadir" / "state").write_text("payload\n")
    (data_source / ".extraction_complete").touch()

    env = {k: v for k, v in os.environ.items() if k not in ("BLOCK_LOG_SOURCE_DIR", "SHARED_BLOCK_LOG_DIR")}
    env.update(block_log_env)
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_LN": shutil.which("ln") or "/bin/ln",
        "LN_LOG": str(tmp_path / "ln.log"),
        "DATA_SOURCE": str(data_source),
        "DATADIR": str(tmp_path / "datadir"),
        "SHM_DIR": str(tmp_path / "shm_dir"),
    })
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)


def _linked_from(tmp_path: Path, proc: "subprocess.CompletedProcess[str]") -> Path:
    assert proc.returncode == 0, proc.stderr[-4000:]
    blockchain = tmp_path / "datadir" / "blockchain"
    links = sorted(blockchain.iterdir())
    assert [link.name for link in links] == ["block_log_part.0001", "block_log_part.0001.artifacts"]
    assert all(link.is_symlink() for link in links)
    sources = {Path(os.readlink(link)).parent for link in links}
    assert len(sources) == 1
    assert len((tmp_path / "ln.log").read_text().splitlines()) == 2
    return sources.pop()


def test_block_log_source_dir_without_block_log_falls_through_to_default(tmp_path: Path) -> None:
    default = _block_log_dir(tmp_path, "default")
    empty = tmp_path / "cache_blockchain"
    (empty / "subdir").mkdir(parents=True)

    proc = _run(tmp_path, default, BLOCK_LOG_SOURCE_DIR=str(empty))

    assert _linked_from(tmp_path, proc) == default
    assert f"BLOCK_LOG_SOURCE_DIR={empty} has no block_log* files, skipping" in proc.stdout
    assert f"Using block_log source default={default}" in proc.stdout


def test_block_log_source_dir_alone(tmp_path: Path) -> None:
    canonical = _block_log_dir(tmp_path, "canonical")
    default = _block_log_dir(tmp_path, "default")

    proc = _run(tmp_path, default, BLOCK_LOG_SOURCE_DIR=str(canonical))

    assert _linked_from(tmp_path, proc) == canonical
    assert f"Using block_log source BLOCK_LOG_SOURCE_DIR={canonical}" in proc.stdout


def test_shared_block_log_dir_alone(tmp_path: Path) -> None:
    former = _block_log_dir(tmp_path, "former")
    default = _block_log_dir(tmp_path, "default")

    proc = _run(tmp_path, default, SHARED_BLOCK_LOG_DIR=str(former))

    assert _linked_from(tmp_path, proc) == former


def test_block_log_source_dir_wins_over_shared_block_log_dir(tmp_path: Path) -> None:
    canonical = _block_log_dir(tmp_path, "canonical")
    former = _block_log_dir(tmp_path, "former")
    default = _block_log_dir(tmp_path, "default")

    proc = _run(tmp_path, default, BLOCK_LOG_SOURCE_DIR=str(canonical), SHARED_BLOCK_LOG_DIR=str(former))

    assert _linked_from(tmp_path, proc) == canonical


def test_no_candidate_with_block_log_warns_naming_each(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    empty.mkdir()
    default = tmp_path / "default_empty"
    default.mkdir()

    proc = _run(tmp_path, default, BLOCK_LOG_SOURCE_DIR=str(missing), SHARED_BLOCK_LOG_DIR=str(empty))

    assert proc.returncode == 0, proc.stderr[-4000:]
    assert ("WARNING: No blockchain in cache and no shared block_log* files in any of: "
            f"BLOCK_LOG_SOURCE_DIR={missing}, SHARED_BLOCK_LOG_DIR={empty}, default={default}") in proc.stdout
    assert not (tmp_path / "ln.log").exists()
