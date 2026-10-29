"""scripts/python/pnpm-catalog-resync.py: the per-consumer re-sync commit, against local fixture repositories.

A fixture ccc repository carries the real sync script and a fixture catalog; a
fixture consumer has a ccc submodule, a lockfile and either a merged
pnpm-workspace.yaml (a monorepo) or pnpm-workspace.yaml symlinks into the
submodule. A stand-in for `pnpm install --lockfile-only` derives the lockfile
from pnpm-workspace.yaml, so nothing touches the network.
"""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Self

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "python"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPTS))
resync_module = _load("pnpm_catalog_resync", SCRIPTS / "pnpm-catalog-resync.py")
consumers_module = importlib.import_module("pnpm_catalog_consumers")
MONOREPO = consumers_module.Consumer("hive/monorepo", "npm-common-config", "merge")
SYMLINKING = consumers_module.Consumer("hive/app", "npm-common-config", "symlink")

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


def _resync(tmp_path: Path, repo: Path, ccc_commit: str, consumer: Any = MONOREPO) -> "str | None":
    result: "str | None" = resync_module.resync(repo, consumer, tmp_path / "ccc", ccc_commit, LOCKFILE_COMMAND)
    return result


def _lockfile(workspace: str) -> str:
    """What LOCKFILE_COMMAND writes for `workspace`."""
    return "workspace: " + hashlib.sha256(workspace.encode()).hexdigest() + "\n"


def _symlinking_consumer(tmp_path: Path, ccc_commit: str, catalog: str, symlinks: Dict[str, Optional[str]]) -> Path:
    """A clone of a project with ccc at `ccc_commit` whose `symlinks` (directory -> pnpm-workspace.yaml
    symlink target) were locked against `catalog`; a None target is a regular file holding CONSUMER."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet", "--initial-branch", "main")
    for directory, target in symlinks.items():
        (origin / directory).mkdir(parents=True, exist_ok=True)
        if target is None:
            (origin / directory / "pnpm-workspace.yaml").write_text(CONSUMER)
        else:
            (origin / directory / "pnpm-workspace.yaml").symlink_to(target)
        (origin / directory / "pnpm-lock.yaml").write_text(_lockfile(catalog))
    (origin / "unlocked").mkdir()
    (origin / "unlocked" / "pnpm-workspace.yaml").symlink_to("../npm-common-config/pnpm-config/pnpm-workspace.yaml")
    _git(origin, "add", "-A")
    _git(origin, "update-index", "--add", "--cacheinfo", f"160000,{ccc_commit},npm-common-config")
    _git(origin, "commit", "--quiet", "-m", "consumer")
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(repo)], check=True)
    return repo


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

    with pytest.raises(consumers_module.ConsumerError, match="no ccc submodule at npm-common-config"):
        _resync(tmp_path, repo, ccc["new"])


def test_unmergeable_workspace_fails_without_a_commit(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    shadowing = CONSUMER.replace("app-local", "react-toolset")
    repo = _consumer(tmp_path, ccc["old"], shadowing)
    before = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(subprocess.CalledProcessError):
        _resync(tmp_path, repo, ccc["new"])

    assert _git(repo, "rev-parse", "HEAD") == before


SYMLINKS: Dict[str, Optional[str]] = {".": "npm-common-config/pnpm-config/pnpm-workspace.yaml",
                                     "examples/app": "../../npm-common-config/pnpm-config/pnpm-workspace.yaml"}


def test_symlinking_consumer_gets_every_symlinked_lockfile_refreshed(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    repo = _symlinking_consumer(tmp_path, ccc["old"], OLD_CATALOG, {**SYMLINKS, "own": None})

    sha = _resync(tmp_path, repo, ccc["new"], SYMLINKING)

    assert sha == _git(repo, "rev-parse", "HEAD")
    assert _changed(repo, "HEAD") == ["examples/app/pnpm-lock.yaml", "npm-common-config", "pnpm-lock.yaml"]
    for directory in SYMLINKS:
        assert _git(repo, "show", f"HEAD:{directory}/pnpm-lock.yaml") + "\n" == _lockfile(NEW_CATALOG)
    assert _git(repo, "ls-tree", "HEAD", "npm-common-config").split()[:3] == ["160000", "commit", ccc["new"]]
    assert _git(repo, "ls-files", "--stage", "pnpm-workspace.yaml").startswith("120000 ")
    message = _git(repo, "log", "-1", "--format=%B")
    assert "common-ci-configuration@" + ccc["new"][:8] in message
    assert "refreshed examples/app/pnpm-lock.yaml, pnpm-lock.yaml with" in message
    assert not (repo / "unlocked" / "pnpm-lock.yaml").exists()


def test_symlinking_consumer_in_sync_gets_no_commit(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    repo = _symlinking_consumer(tmp_path, ccc["new"], NEW_CATALOG, SYMLINKS)
    before = _git(repo, "rev-parse", "HEAD")

    assert _resync(tmp_path, repo, ccc["new-again"], SYMLINKING) is None

    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain", "--", "pnpm-lock.yaml", "examples/app/pnpm-lock.yaml") == ""


def test_symlinking_consumer_without_a_symlink_into_the_submodule_is_refused(tmp_path: Path) -> None:
    ccc = _ccc(tmp_path)
    repo = _symlinking_consumer(tmp_path, ccc["old"], OLD_CATALOG, {".": None, "other": "../elsewhere/pnpm-workspace.yaml"})
    before = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(consumers_module.ConsumerError, match="no pnpm-workspace.yaml symlink to npm-common-config/"):
        _resync(tmp_path, repo, ccc["new"], SYMLINKING)

    assert _git(repo, "rev-parse", "HEAD") == before


def test_consumer_list_is_project_submodule_mode_and_flag_per_line(tmp_path: Path) -> None:
    consumers = {c.project: c for c in consumers_module.read_consumers()}
    assert all(p.startswith("hive/") and c.submodule and c.mode in ("merge", "symlink") for p, c in consumers.items())
    assert consumers["hive/denser"].mode == "merge" and consumers["hive/denser"].auto_merge
    assert consumers["hive/block_explorer_ui"] == consumers_module.Consumer("hive/block_explorer_ui", "npm-common-config", "symlink")
    assert consumers["hive/vessel"].mode == "merge" and not consumers["hive/vessel"].auto_merge

    listed = tmp_path / "consumers.txt"
    listed.write_text("# comment\n\nhive/a npm-common-config merge  # trailing\nhive/b ts/ccc symlink no-auto-merge\n")
    assert consumers_module.read_consumers(listed) == [
        consumers_module.Consumer("hive/a", "npm-common-config", "merge", auto_merge=True),
        consumers_module.Consumer("hive/b", "ts/ccc", "symlink", auto_merge=False)]
    for line in ("hive/a npm-common-config", "hive/a npm-common-config copy", "hive/a ccc symlink auto-merge",
                 "hive/a ccc symlink no-auto-merge extra"):
        listed.write_text(line + "\n")
        with pytest.raises(consumers_module.ConsumerError, match=":1: expected"):
            consumers_module.read_consumers(listed)


class _FakeGitLab:
    """Records the API calls of one merge request's publication; the MR's pipeline is already at `sha`."""

    def __init__(self: Self, sha: str) -> None:
        self.sha = sha
        self.calls: List[Any] = []

    def call(self: Self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        self.calls.append((method, path, data))
        if method == "GET" and "?" in path:
            return []
        return {"iid": 7, "web_url": "https://gitlab.example/hive/app/-/merge_requests/7", "head_pipeline": {"sha": self.sha}}


@pytest.mark.parametrize("auto_merge", [True, False])
def test_merge_request_is_set_to_merge_unless_the_consumer_is_no_auto_merge(auto_merge: bool) -> None:
    gitlab = _FakeGitLab("c0ffee")
    consumer = SYMLINKING._replace(auto_merge=auto_merge)

    url = resync_module._publish(gitlab, {"id": 3, "default_branch": "main"}, consumer, "c0ffee", "ab" * 20)

    assert url.endswith("/merge_requests/7")
    created = [data for method, path, data in gitlab.calls if method == "POST"]
    assert len(created) == 1 and created[0]["source_branch"] == "ccc-catalog-resync" and created[0]["target_branch"] == "main"
    merges = [data for method, path, data in gitlab.calls if path.endswith("/merge_requests/7/merge")]
    assert merges == ([{"merge_when_pipeline_succeeds": True, "should_remove_source_branch": True, "sha": "c0ffee"}]
                      if auto_merge else [])
    assert ("no-auto-merge" in created[0]["description"]) is not auto_merge


class _FakeProjectGitLab(_FakeGitLab):
    """_FakeGitLab that also answers the project lookup, for a consumer at `origin` with default branch
    master, and whose MR pipeline is at whatever ccc-catalog-resync holds there."""

    def __init__(self: Self, origin: Path) -> None:
        super().__init__("")
        self.origin = origin

    def call(self: Self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        self.sha = subprocess.run(["git", "-C", str(self.origin), "rev-parse", "--verify", "--quiet", "refs/heads/ccc-catalog-resync"],
                                  capture_output=True, text=True, check=False).stdout.strip()
        if method == "GET" and path.startswith("/projects/") and path.count("/") == 2:
            self.calls.append((method, path, data))
            return {"id": 3, "default_branch": "master"}
        return super().call(method, path, data)


def test_resync_branch_already_carrying_the_tree_is_not_pushed_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ccc = _ccc(tmp_path)
    _consumer(tmp_path, ccc["old"], _merged(tmp_path, OLD_CATALOG))
    origin = tmp_path / "origin"
    monkeypatch.setattr(resync_module, "clone", lambda _server, _project, destination, branch: subprocess.run(
        ["git", "clone", "--quiet", "--branch", branch, str(origin), str(destination)], check=True))
    resync = resync_module.resync
    monkeypatch.setattr(resync_module, "resync", lambda repo, consumer, _ccc, commit: resync(
        repo, consumer, tmp_path / "ccc", commit, LOCKFILE_COMMAND))

    def run(gitlab: _FakeGitLab) -> str:
        result: str = resync_module._process(gitlab, MONOREPO, "unused", ccc["new"])
        return result

    first = run(_FakeProjectGitLab(origin))
    pushed = _git(origin, "rev-parse", "refs/heads/ccc-catalog-resync")
    assert first.endswith("/merge_requests/7")
    assert _git(origin, "rev-parse", "ccc-catalog-resync:npm-common-config") == ccc["new"]

    again = _FakeProjectGitLab(origin)
    second = run(again)

    assert second.startswith("ccc-catalog-resync already carries this re-sync, not pushed; ")
    assert _git(origin, "rev-parse", "refs/heads/ccc-catalog-resync") == pushed
    merges = [data for method, path, data in again.calls if path.endswith("/merge_requests/7/merge")]
    assert merges == [{"merge_when_pipeline_succeeds": True, "should_remove_source_branch": True, "sha": pushed}]

    (origin / "README.md").write_text("default branch moved on\n")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "--quiet", "-m", "consumer change")
    run(_FakeProjectGitLab(origin))
    moved = _git(origin, "rev-parse", "refs/heads/ccc-catalog-resync")
    assert moved != pushed
    assert _git(origin, "rev-parse", "ccc-catalog-resync:README.md") == _git(origin, "rev-parse", "master:README.md")


def test_without_token_the_job_skips_and_passes() -> None:
    env = {k: v for k, v in os.environ.items() if k != "CATALOG_RESYNC_TOKEN"}
    proc = subprocess.run([sys.executable, str(SCRIPTS / "pnpm-catalog-resync.py")],
                          env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "CATALOG_RESYNC_TOKEN is not set, skipping" in proc.stdout
    assert "api and write_repository" in proc.stdout
