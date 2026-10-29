#!/usr/bin/env python3
'''
Re-syncs the monorepos listed in pnpm-config/catalog-consumers.txt with this
ccc commit's pnpm catalogs and opens a merge request in each one that is behind.

For every consumer: clone its default branch, point its ccc submodule at this
commit, run that commit's pnpm-catalog-sync.py on its pnpm-workspace.yaml (as
the consumer's CI does), refresh pnpm-lock.yaml with `pnpm install
--lockfile-only` and, when either file changed, force-push the commit to the
`ccc-catalog-resync` branch and open (or update) one merge request to the
default branch, set to merge when its pipeline succeeds. A consumer already in
sync gets no commit, and its open re-sync merge request, if any, is closed.

  python3 scripts/python/pnpm-catalog-resync.py [--commit SHA]

The GitLab token (scopes api and write_repository on every consumer) is read
from CATALOG_RESYNC_TOKEN; without it the script says so and exits 0. The pnpm
on PATH must be the version docker-bake.hcl's PNPM_VERSION gives the images.
Exit 1 names the consumers that could not be re-synced; the others are still
processed.
'''
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
CONSUMERS = ROOT / "pnpm-config" / "catalog-consumers.txt"
BRANCH = "ccc-catalog-resync"
TOKEN_VARIABLE = "CATALOG_RESYNC_TOKEN"
LOCKFILE_COMMAND = ("pnpm", "install", "--lockfile-only", "--no-frozen-lockfile")
_SYNC_SCRIPT = Path("scripts") / "python" / "pnpm-catalog-sync.py"
_IDENTITY = {
    "GIT_AUTHOR_NAME": "common-ci-configuration catalog re-sync",
    "GIT_AUTHOR_EMAIL": "ccc-catalog-resync@noreply.gitlab.syncad.com",
    "GIT_COMMITTER_NAME": "common-ci-configuration catalog re-sync",
    "GIT_COMMITTER_EMAIL": "ccc-catalog-resync@noreply.gitlab.syncad.com",
}
_TIMEOUT = 30
_PIPELINE_WAIT = 300


class ResyncError(Exception):
    """A consumer that cannot be re-synced: no ccc submodule, an API call refused, ..."""


class Consumer(NamedTuple):
    """A monorepo merging ccc's catalogs: its GitLab project path and its ccc submodule's path."""
    project: str
    submodule: str


def read_consumers(path: Path = CONSUMERS) -> List[Consumer]:
    """The consumers listed in `path`: one `<project path> <submodule path>` per line, `#` comments.

    Raises ResyncError for a line that is not two fields.
    """
    consumers = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 2:
            raise ResyncError(f"{path}:{number}: expected `<project path> <ccc submodule path>`, got {line.strip()!r}")
        consumers.append(Consumer(*fields))
    return consumers


def _git(repo: Path, *args: str, stdout: Optional[int] = None) -> str:
    output = subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=stdout, text=True).stdout
    return output or ""


def _ccc_title(ccc_commit: str) -> str:
    return f"Re-sync ccc pnpm catalogs to common-ci-configuration@{ccc_commit[:8]}"


def resync(repo: Path, submodule: str, ccc: Path, ccc_commit: str, lockfile_command: Sequence[str] = LOCKFILE_COMMAND) -> Optional[str]:
    """Commit, in the clone `repo`, its pnpm-workspace.yaml merged with `ccc_commit`'s catalogs
    and its pnpm-lock.yaml refreshed by `lockfile_command`, with its ccc submodule at `submodule`
    moved to that commit; `ccc` is a ccc repository holding `ccc_commit`.

    Returns the new commit's SHA, or None (committing nothing) when neither file changed.
    Raises ResyncError when `submodule` is not a submodule of `repo`, and
    subprocess.CalledProcessError when git, the merge or `lockfile_command` fails.
    """
    if not _git(repo, "ls-tree", "HEAD", "--", submodule, stdout=subprocess.PIPE).startswith("160000 "):
        raise ResyncError(f"no ccc submodule at {submodule}")
    checkout = repo / submodule
    checkout.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(ccc), "archive", "--format=tar", ccc_commit], check=True, stdout=subprocess.PIPE).stdout
    subprocess.run(["tar", "-x", "-C", str(checkout)], input=archive, check=True)
    subprocess.run([sys.executable, str(checkout / _SYNC_SCRIPT), "pnpm-workspace.yaml"], cwd=repo, check=True)
    subprocess.run(list(lockfile_command), cwd=repo, check=True)
    files = ["pnpm-workspace.yaml", "pnpm-lock.yaml"]
    if not _git(repo, "status", "--porcelain", "--", *files, stdout=subprocess.PIPE):
        return None
    _git(repo, "add", "--", *files)
    _git(repo, "update-index", "--cacheinfo", f"160000,{ccc_commit},{submodule}")
    message = (f"{_ccc_title(ccc_commit)}\n\nMerged ccc's catalogs and overrides with {submodule}/{_SYNC_SCRIPT}"
               f" and refreshed pnpm-lock.yaml with `{' '.join(lockfile_command)}`.\n")
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-F", "-"], input=message, text=True, check=True,
                   env={**_IDENTITY, **os.environ})
    return _git(repo, "rev-parse", "HEAD", stdout=subprocess.PIPE).strip()


class _GitLab(NamedTuple):
    """The GitLab API at `api_url` (no trailing slash), called with `token`."""
    api_url: str
    token: str

    def call(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """The decoded JSON response to `method` `path`; raises ResyncError on an HTTP or network error."""
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(f"{self.api_url}{path}", data=body, method=method, headers={
            "PRIVATE-TOKEN": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ResyncError(f"{method} {path}: HTTP {error.code} {error.read().decode(errors='replace')[:500]}") from error
        except urllib.error.URLError as error:
            raise ResyncError(f"{method} {path}: {error.reason}") from error


def _open_merge_request(gitlab: _GitLab, project_id: int, target: str) -> Optional[Dict[str, Any]]:
    query = urllib.parse.urlencode({"state": "opened", "source_branch": BRANCH, "target_branch": target})
    found = gitlab.call("GET", f"/projects/{project_id}/merge_requests?{query}")
    return found[0] if found else None


def _merge_when_pipeline_succeeds(gitlab: _GitLab, project_id: int, iid: int, sha: str) -> None:
    deadline = time.monotonic() + _PIPELINE_WAIT
    while True:
        request = gitlab.call("GET", f"/projects/{project_id}/merge_requests/{iid}")
        if (request.get("head_pipeline") or {}).get("sha") == sha:
            break
        if time.monotonic() > deadline:
            raise ResyncError(f"!{iid}: no pipeline for {sha[:8]} after {_PIPELINE_WAIT}s; auto-merge not set")
        time.sleep(10)
    gitlab.call("PUT", f"/projects/{project_id}/merge_requests/{iid}/merge",
                {"merge_when_pipeline_succeeds": True, "should_remove_source_branch": True, "sha": sha})


def _publish(gitlab: _GitLab, repo: Path, project: Dict[str, Any], sha: str, ccc_commit: str) -> str:
    project_id, target = project["id"], project["default_branch"]
    _git(repo, "push", "--force", "origin", f"{sha}:refs/heads/{BRANCH}")
    server = os.environ.get("CI_PROJECT_URL", "https://gitlab.syncad.com/hive/common-ci-configuration")
    fields = {
        "title": _ccc_title(ccc_commit),
        "description": (f"common-ci-configuration's pnpm catalogs changed in {server}/-/commit/{ccc_commit}. This merge request,"
                        f" opened by its `pnpm_catalog_resync` job, moves the ccc submodule there, merges the catalogs into"
                        f" pnpm-workspace.yaml and refreshes pnpm-lock.yaml. The next catalog change force-updates `{BRANCH}`."),
    }
    existing = _open_merge_request(gitlab, project_id, target)
    if existing:
        request = gitlab.call("PUT", f"/projects/{project_id}/merge_requests/{existing['iid']}", fields)
    else:
        request = gitlab.call("POST", f"/projects/{project_id}/merge_requests",
                              {**fields, "source_branch": BRANCH, "target_branch": target, "remove_source_branch": True})
    _merge_when_pipeline_succeeds(gitlab, project_id, request["iid"], sha)
    return str(request["web_url"])


def _process(gitlab: _GitLab, consumer: Consumer, server_url: str, ccc_commit: str) -> str:
    project = gitlab.call("GET", f"/projects/{urllib.parse.quote(consumer.project, safe='')}")
    target = project["default_branch"]
    with tempfile.TemporaryDirectory(prefix="ccc-resync-") as work:
        repo = Path(work) / "repo"
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", target,
                        f"{server_url}/{consumer.project}.git", str(repo)], check=True)
        sha = resync(repo, consumer.submodule, ROOT, ccc_commit)
        if sha is None:
            stale = _open_merge_request(gitlab, project["id"], target)
            if stale:
                gitlab.call("PUT", f"/projects/{project['id']}/merge_requests/{stale['iid']}", {"state_event": "close"})
                return f"in sync on {target}; closed the stale {stale['web_url']}"
            return f"in sync on {target}"
        return _publish(gitlab, repo, project, sha, ccc_commit)


def _bake_pnpm_version() -> str:
    found = re.search(r'variable "PNPM_VERSION" \{\s*default = "([^"]+)"', (ROOT / "docker-bake.hcl").read_text(encoding="utf-8"))
    if not found:
        raise ResyncError("docker-bake.hcl defines no PNPM_VERSION")
    return found.group(1)


def _git_auth_environment(server_url: str, token: str) -> Dict[str, str]:
    """git config, passed through the environment so the token is in no command line or .git/config."""
    credentials = base64.b64encode(f"oauth2:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{server_url}/.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}"}


def main() -> int:
    """Re-sync every listed consumer; return the exit status."""
    parser = argparse.ArgumentParser(description="Open a catalog re-sync merge request in every listed ccc catalog consumer.")
    parser.add_argument("--commit", default=os.environ.get("CI_COMMIT_SHA", "HEAD"),
                        help="ccc commit to move the consumers to (default: CI_COMMIT_SHA, else HEAD)")
    args = parser.parse_args()
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token:
        print(f"pnpm-catalog-resync: {TOKEN_VARIABLE} is not set, skipping: no consumer is re-synced. It needs a token with the"
              " api and write_repository scopes and the Developer role (one that may merge to their default branches) on"
              f" the projects in {CONSUMERS.relative_to(ROOT)}, as a masked CI/CD variable of this project.")
        return 0
    try:
        consumers = read_consumers()
        expected = _bake_pnpm_version()
        running = subprocess.run(["pnpm", "--version"], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
        if running != expected:
            raise ResyncError(f"pnpm on PATH is {running}, docker-bake.hcl's PNPM_VERSION is {expected}: refusing to write lockfiles")
        ccc_commit = _git(ROOT, "rev-parse", "--verify", f"{args.commit}^{{commit}}", stdout=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError, ResyncError) as error:
        print(f"pnpm-catalog-resync: {error}", file=sys.stderr)
        return 1
    server_url = os.environ.get("CI_SERVER_URL", "https://gitlab.syncad.com").rstrip("/")
    os.environ.update(_git_auth_environment(server_url, token))
    gitlab = _GitLab(os.environ.get("CI_API_V4_URL", f"{server_url}/api/v4").rstrip("/"), token)
    failed = []
    for consumer in consumers:
        print(f"== {consumer.project}", flush=True)
        try:
            print(f"{consumer.project}: {_process(gitlab, consumer, server_url, ccc_commit)}", flush=True)
        except (OSError, subprocess.CalledProcessError, ResyncError) as error:
            print(f"{consumer.project}: FAILED: {error}", file=sys.stderr, flush=True)
            failed.append(consumer.project)
    if failed:
        print(f"pnpm-catalog-resync: not re-synced: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
