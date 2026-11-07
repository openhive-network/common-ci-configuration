"""cache-manager get: a hive cache extracted with an empty blockchain dir is linked to the
block_log in BLOCK_LOG_SOURCE_DIR, falling back to the former name SHARED_BLOCK_LOG_DIR."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CACHE_MANAGER = ROOT / "scripts" / "cache-manager.sh"


def _run(tmp_path: Path, *args: str, **block_log_env: str) -> "subprocess.CompletedProcess[str]":
    env = {k: v for k, v in os.environ.items() if k not in ("BLOCK_LOG_SOURCE_DIR", "SHARED_BLOCK_LOG_DIR")}
    env.update(block_log_env)
    # Keep the separate local/NFS shared block_log lookup out of the way
    env.update({"CACHE_NFS_PATH": str(tmp_path / "nfs"), "CACHE_LOCAL_PATH": str(tmp_path / "local"),
                "SHARED_BLOCK_LOG_LOCAL": str(tmp_path / "absent"), "SHARED_BLOCK_LOG_NFS": str(tmp_path / "absent")})
    return subprocess.run(["bash", str(CACHE_MANAGER), *args], env=env, capture_output=True, text=True, check=False)


def _block_log_dir(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "block_log").write_text(f"{name}\n")
    (directory / "block_log.artifacts").write_text(f"{name}\n")
    return directory


def _linked_block_log_target(tmp_path: Path, **block_log_env: str) -> Path:
    (tmp_path / "nfs").mkdir()
    (tmp_path / "local").mkdir()
    source = tmp_path / "source"
    (source / "datadir" / "blockchain").mkdir(parents=True)
    (source / "datadir" / "state").write_text("payload\n")
    put = _run(tmp_path, "put", "hive", "key1", str(source))
    assert put.returncode == 0, put.stderr

    dest = tmp_path / "dest"
    get = _run(tmp_path, "get", "hive", "key1", str(dest), **block_log_env)

    assert get.returncode == 0, get.stderr
    linked = dest / "datadir" / "blockchain" / "block_log"
    assert linked.is_symlink(), get.stderr
    assert (dest / "datadir" / "blockchain" / "block_log.artifacts").is_symlink()
    return Path(os.readlink(linked))


@pytest.mark.parametrize("variables", [("BLOCK_LOG_SOURCE_DIR",), ("BLOCK_LOG_SOURCE_DIR", "SHARED_BLOCK_LOG_DIR")])
def test_get_links_block_log_from_block_log_source_dir(tmp_path: Path, variables: "tuple[str, ...]") -> None:
    canonical = _block_log_dir(tmp_path, "canonical")
    former = _block_log_dir(tmp_path, "former")
    env = {"BLOCK_LOG_SOURCE_DIR": str(canonical), "SHARED_BLOCK_LOG_DIR": str(former)}

    target = _linked_block_log_target(tmp_path, **{name: env[name] for name in variables})

    assert target == canonical / "block_log"


@pytest.mark.parametrize("canonical_value", [None, ""])
def test_get_falls_back_to_shared_block_log_dir(tmp_path: Path, canonical_value: "str | None") -> None:
    former = _block_log_dir(tmp_path, "former")
    env = {"SHARED_BLOCK_LOG_DIR": str(former)}
    if canonical_value is not None:
        env["BLOCK_LOG_SOURCE_DIR"] = canonical_value

    target = _linked_block_log_target(tmp_path, **env)

    assert target == former / "block_log"
