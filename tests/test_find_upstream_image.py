"""find-upstream-image.sh against a local upstream repo and a stub docker registry."""

import os
import subprocess
from pathlib import Path

from typing import Dict, List, Tuple

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bash" / "find-upstream-image.sh"
REGISTRY = "registry.example.com/hive/haf"
RELEASE = "1.28.8-rc3"

STUB_DOCKER = """#!/bin/sh
if [ "$1" = manifest ] && [ "$2" = inspect ]; then
    grep -qxF "$3" "$DOCKER_STUB_ALLOWLIST"
    exit $?
fi
echo "unexpected docker call: $*" >&2
exit 2
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(name="upstream")
def _upstream(tmp_path: Path) -> Tuple[Path, str]:
    """A repo with one source commit on develop and a release branch pointing at it."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "main.c").write_text("int main;\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "source")
    _git(repo, "branch", RELEASE)
    return repo, _git(repo, "rev-parse", "HEAD")


def _run(
    tmp_path: Path, repo: Path, branch: str, available: List[str], *extra: str
) -> Tuple["subprocess.CompletedProcess[str]", Dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(STUB_DOCKER)
    docker.chmod(0o755)
    allowlist = tmp_path / "allowlist"
    allowlist.write_text("".join(f"{ref}\n" for ref in available))
    output = tmp_path / "out.env"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_STUB_ALLOWLIST": str(allowlist),
    }
    proc = subprocess.run(
        [
            "bash", str(SCRIPT),
            f"--repo-url=file://{repo}",
            f"--registry={REGISTRY}",
            "--patterns=src/",
            f"--branch={branch}",
            f"--output={output}",
            f"--work-dir={tmp_path / 'work'}",
            *extra,
        ],
        env=env, capture_output=True, text=True, check=False,
    )
    values: Dict[str, str] = {}
    if output.exists():
        values = dict(line.split("=", 1) for line in output.read_text().splitlines() if line)
    return proc, values


def test_commit_tag_preferred_over_release_tag(tmp_path: Path, upstream: Tuple[Path, str]) -> None:
    repo, sha = upstream
    proc, out = _run(tmp_path, repo, RELEASE,
                     [f"{REGISTRY}:{sha[:8]}", f"{REGISTRY}:{RELEASE}"], "--require-hit")
    assert proc.returncode == 0, proc.stderr
    assert out["UPSTREAM_CACHE_HIT"] == "true"
    assert out["UPSTREAM_TAG"] == sha[:8]
    assert out["UPSTREAM_IMAGE"] == f"{REGISTRY}:{sha[:8]}"
    assert out["UPSTREAM_FALLBACK"] == "false"


def test_release_tag_used_when_commit_tag_missing(tmp_path: Path, upstream: Tuple[Path, str]) -> None:
    repo, sha = upstream
    proc, out = _run(tmp_path, repo, RELEASE, [f"{REGISTRY}:{RELEASE}"], "--require-hit")
    assert proc.returncode == 0, proc.stderr
    assert out == {
        "UPSTREAM_BRANCH": RELEASE,
        "UPSTREAM_FALLBACK": "release-tag",
        "UPSTREAM_CACHE_HIT": "true",
        "UPSTREAM_COMMIT": sha,
        "UPSTREAM_TAG": RELEASE,
        "UPSTREAM_IMAGE": f"{REGISTRY}:{RELEASE}",
        "UPSTREAM_REGISTRY": REGISTRY,
    }
    assert "using release tag" in proc.stderr


def test_no_image_with_require_hit_fails(tmp_path: Path, upstream: Tuple[Path, str]) -> None:
    repo, sha = upstream
    proc, out = _run(tmp_path, repo, RELEASE, [], "--require-hit")
    assert proc.returncode == 1
    assert "No upstream image available" in proc.stderr
    assert out["UPSTREAM_CACHE_HIT"] == "false"
    assert out["UPSTREAM_TAG"] == sha[:8]
    assert out["UPSTREAM_FALLBACK"] == "false"


def test_develop_without_commit_tag_fails(tmp_path: Path, upstream: Tuple[Path, str]) -> None:
    repo, _ = upstream
    proc, out = _run(tmp_path, repo, "develop", [f"{REGISTRY}:{RELEASE}"], "--require-hit")
    assert proc.returncode == 1
    assert out["UPSTREAM_CACHE_HIT"] == "false"
    assert out["UPSTREAM_BRANCH"] == "develop"


def test_release_tag_honours_image_type_sub_path(tmp_path: Path, upstream: Tuple[Path, str]) -> None:
    repo, sha = upstream
    proc, out = _run(tmp_path, repo, RELEASE,
                     [f"{REGISTRY}:{RELEASE}", f"{REGISTRY}/testnet:{RELEASE}"],
                     "--image=testnet", "--require-hit")
    assert proc.returncode == 0, proc.stderr
    assert out["UPSTREAM_FALLBACK"] == "release-tag"
    assert out["UPSTREAM_IMAGE"] == f"{REGISTRY}/testnet:{RELEASE}"
    assert out["UPSTREAM_REGISTRY"] == f"{REGISTRY}/testnet"
    assert out["UPSTREAM_COMMIT"] == sha


def test_image_type_not_satisfied_by_mainnet_release_tag(tmp_path: Path, upstream: Tuple[Path, str]) -> None:
    repo, _ = upstream
    proc, out = _run(tmp_path, repo, RELEASE, [f"{REGISTRY}:{RELEASE}"],
                     "--image=testnet", "--require-hit")
    assert proc.returncode == 1
    assert out["UPSTREAM_CACHE_HIT"] == "false"
