"""scripts/python/pnpm-catalog-resync.py: the per-consumer re-sync commit, against local fixture repositories.

A fixture ccc repository carries the real sync script and a fixture catalog; a
fixture consumer has a ccc submodule, a merged pnpm-workspace.yaml and a
lockfile. A stand-in for `pnpm install --lockfile-only` derives the lockfile
from pnpm-workspace.yaml, so nothing touches the network.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict, List

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "python"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resync_module = _load("pnpm_catalog_resync", SCRIPTS / "pnpm-catalog-resync.py")

OLD_CATALOG = """packages:
  - './src/**/*'

catalogs:
  react-toolset:
    react: 19.3.0
"""
NEW_CATALOG = OLD_CATALOG.replace("19.3.0", "19.3.1")

CONSUMER = """packages:
  - apps/*

catalogs:
  app-local:
    some-lib: ^1.2.0
"""

# Writes a lockfile that changes exactly when pnpm-workspace.yaml does.
LOCKFILE_COMMAND = [sys.executable, "-c",
                    "import hashlib, pathlib; pathlib.Path('pnpm-lock.yaml').write_text("
                    "'workspace: ' + hashlib.sha256(pathlib.Path('pnpm-workspace.yaml').read_bytes()).hexdigest() + '\\n')"]


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _ccc(tmp_path: Path) -> Dict[str, str]:
    """A ccc repository at tmp_path/ccc: catalog name -> commit carrying it."""
    ccc = tmp_path / "ccc"
    (ccc / "scripts" / "python").mkdir(parents=True)
    (ccc / "pnpm-config").mkdir()
    for script in ("pnpm-catalog-sync.py", "pnpm_catalogs.py"):
        shutil.copy(SCRIPTS / script, ccc / "scripts" / "python" / script)
    _git(ccc, "init", "--quiet")
    commits = {}
    for name, catalog in (("old", OLD_CATALOG), ("new", NEW_CATALOG)):
        (ccc / "pnpm-config" / "pnpm-workspace.yaml").write_text(catalog)
        commits[name] = _commit_all(ccc, name)
    (ccc / "README.md").write_text("no catalog change\n")
    commits["new-again"] = _commit_all(ccc, "new-again")
    return commits


def _merged(tmp_path: Path, catalog: str) -> str:
    """CONSUMER merged with `catalog` by the sync script."""
    work = tmp_path / "merge"
    work.mkdir(exist_ok=True)
    (work / "source.yaml").write_text(catalog)
    (work / "pnpm-workspace.yaml").write_text(CONSUMER)
    subprocess.run([sys.executable, str(SCRIPTS / "pnpm-catalog-sync.py"), "--source", "source.yaml"],
                   cwd=work, check=True, capture_output=True)
    return (work / "pnpm-workspace.yaml").read_text()


def _consumer(tmp_path: Path, ccc_commit: str, workspace: str, submodule: bool = True) -> Path:
    """A clone of a consumer whose default branch has `workspace`, its lockfile and ccc at `ccc_commit`."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet", "--initial-branch", "master")
    (origin / "pnpm-workspace.yaml").write_text(workspace)
    subprocess.run(LOCKFILE_COMMAND, cwd=origin, check=True)
    if submodule:
        (origin / ".gitmodules").write_text('[submodule "npm-common-config"]\n'
                                            "\tpath = npm-common-config\n\turl = ../common-ci-configuration.git\n")
    _git(origin, "add", "-A")
    if submodule:
        # After `add -A`, which would stage the missing checkout as a deletion
        _git(origin, "update-index", "--add", "--cacheinfo", f"160000,{ccc_commit},npm-common-config")
    _git(origin, "commit", "--quiet", "-m", "consumer")
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(repo)], check=True)
    return repo


def _resync(tmp_path: Path, repo: Path, ccc_commit: str) -> "str | None":
    result: "str | None" = resync_module.resync(repo, "npm-common-config", tmp_path / "ccc", ccc_commit, LOCKFILE_COMMAND)
    return result


def _changed(repo: Path, commit: str) -> List[str]:
    return sorted(_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())


def test_catalog_change_commits_workspace_lockfile_and_submodule(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    repo = _consumer(tmp_path, ccc["old"], _merged(tmp_path, OLD_CATALOG))
    before = _git(repo, "rev-parse", "HEAD")

    sha = _resync(tmp_path, repo, ccc["new"])

    assert sha == _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", "HEAD^") == before
    assert _changed(repo, "HEAD") == ["npm-common-config", "pnpm-lock.yaml", "pnpm-workspace.yaml"]
    assert _git(repo, "show", "HEAD:pnpm-workspace.yaml") + "\n" == _merged(tmp_path, NEW_CATALOG)
    assert _git(repo, "ls-tree", "HEAD", "npm-common-config").split()[:3] == ["160000", "commit", ccc["new"]]
    assert "common-ci-configuration@" + ccc["new"][:8] in _git(repo, "log", "-1", "--format=%s")
    assert _git(repo, "status", "--porcelain", "--", "pnpm-workspace.yaml", "pnpm-lock.yaml") == ""


def test_consumer_in_sync_gets_no_commit(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    repo = _consumer(tmp_path, ccc["new"], _merged(tmp_path, NEW_CATALOG))
    before = _git(repo, "rev-parse", "HEAD")

    assert _resync(tmp_path, repo, ccc["new-again"]) is None

    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain", "--", "pnpm-workspace.yaml", "pnpm-lock.yaml") == ""


def test_consumer_without_the_submodule_is_refused(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    repo = _consumer(tmp_path, ccc["old"], _merged(tmp_path, OLD_CATALOG), submodule=False)

    with pytest.raises(resync_module.ResyncError, match="no ccc submodule at npm-common-config"):
        _resync(tmp_path, repo, ccc["new"])


def test_unmergeable_workspace_fails_without_a_commit(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    shadowing = CONSUMER.replace("app-local", "react-toolset")
    repo = _consumer(tmp_path, ccc["old"], shadowing)
    before = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(subprocess.CalledProcessError):
        _resync(tmp_path, repo, ccc["new"])

    assert _git(repo, "rev-parse", "HEAD") == before


def test_consumer_list_is_project_and_submodule_per_line(tmp_path: Path) -> None:
    consumers = resync_module.read_consumers()
    assert consumers
    assert all(c.project.startswith("hive/") and c.submodule for c in consumers)

    listed = tmp_path / "consumers.txt"
    listed.write_text("# comment\n\nhive/a npm-common-config  # trailing\n")
    assert resync_module.read_consumers(listed) == [resync_module.Consumer("hive/a", "npm-common-config")]
    listed.write_text("hive/a\n")
    with pytest.raises(resync_module.ResyncError, match=":1: expected"):
        resync_module.read_consumers(listed)


def test_without_token_the_job_skips_and_passes() -> None:
    env = {k: v for k, v in os.environ.items() if k != "CATALOG_RESYNC_TOKEN"}
    proc = subprocess.run([sys.executable, str(SCRIPTS / "pnpm-catalog-resync.py")],
                          env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "CATALOG_RESYNC_TOKEN is not set, skipping" in proc.stdout
    assert "api and write_repository" in proc.stdout
