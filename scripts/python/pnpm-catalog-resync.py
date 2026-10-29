#!/usr/bin/env python3
'''
Re-syncs the projects listed in pnpm-config/catalog-consumers.txt with this
ccc commit's pnpm catalogs and opens a merge request in each one that is behind.

For every consumer: clone its default branch and point its ccc submodule at this
commit. A monorepo (mode `merge`) gets that commit's pnpm-catalog-sync.py run on
its pnpm-workspace.yaml (as its CI does) and its pnpm-lock.yaml refreshed; a
project symlinking ccc's pnpm-workspace.yaml (mode `symlink`) gets the
pnpm-lock.yaml of every directory with such a symlink refreshed. Lockfiles are
refreshed with `pnpm install --lockfile-only`. When a file changed, the commit
(which also moves the submodule) is force-pushed to the `ccc-catalog-resync`
branch and one merge request to the default branch is opened (or updated) and
set to merge when its pipeline succeeds, unless the consumer is listed with
`no-auto-merge`. A consumer already in sync gets no commit, and its open re-sync
merge request, if any, is closed.

  python3 scripts/python/pnpm-catalog-resync.py [--commit SHA]

The GitLab token (scopes api and write_repository on every consumer) is read
from CATALOG_RESYNC_TOKEN; without it the script says so and exits 0. The pnpm
on PATH must be the version docker-bake.hcl's PNPM_VERSION gives the images.
Exit 1 names the consumers that could not be re-synced; the others are still
processed.
'''
import argparse
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional, Sequence

from pnpm_catalog_consumers import (
    CONSUMERS, MERGE, ROOT, SYNC_SCRIPT, Consumer, ConsumerError, clone, git, git_auth_environment, point_at, start)

BRANCH = "ccc-catalog-resync"
TOKEN_VARIABLE = "CATALOG_RESYNC_TOKEN"
LOCKFILE_COMMAND = ("pnpm", "install", "--lockfile-only", "--no-frozen-lockfile")
_IDENTITY = {
    "GIT_AUTHOR_NAME": "common-ci-configuration catalog re-sync",
    "GIT_AUTHOR_EMAIL": "ccc-catalog-resync@noreply.gitlab.syncad.com",
    "GIT_COMMITTER_NAME": "common-ci-configuration catalog re-sync",
    "GIT_COMMITTER_EMAIL": "ccc-catalog-resync@noreply.gitlab.syncad.com",
}
_TIMEOUT = 30
_PIPELINE_WAIT = 300


def _ccc_title(ccc_commit: str) -> str:
    return f"Re-sync ccc pnpm catalogs to common-ci-configuration@{ccc_commit[:8]}"


def resync(repo: Path, consumer: Consumer, ccc: Path, ccc_commit: str,
           lockfile_command: Sequence[str] = LOCKFILE_COMMAND) -> Optional[str]:
    """Commit, in the clone `repo` of `consumer`, its ccc submodule moved to `ccc_commit` of the
    ccc repository `ccc`, with (for a MERGE consumer) pnpm-workspace.yaml merged with that commit's
    catalogs and each pnpm-lock.yaml following them refreshed by `lockfile_command`.

    Returns the new commit's SHA, or None (committing nothing) when no file changed.
    Raises ConsumerError when `repo` has no such submodule (or, for a SYMLINK consumer, no
    pnpm-workspace.yaml symlinked into it), and subprocess.CalledProcessError when git, the
    merge or `lockfile_command` fails.
    """
    directories = point_at(repo, consumer, ccc, ccc_commit)
    for directory in directories:
        subprocess.run(list(lockfile_command), cwd=repo / directory, check=True)
    lockfiles = [posixpath.normpath(posixpath.join(directory, "pnpm-lock.yaml")) for directory in directories]
    files = ["pnpm-workspace.yaml", *lockfiles] if consumer.mode == MERGE else lockfiles
    if not git(repo, "status", "--porcelain", "--", *files, stdout=subprocess.PIPE):
        return None
    git(repo, "add", "--", *files)
    git(repo, "update-index", "--cacheinfo", f"160000,{ccc_commit},{consumer.submodule}")
    refreshed = f"refreshed {', '.join(lockfiles)} with `{' '.join(lockfile_command)}`."
    if consumer.mode == MERGE:
        body = f"Merged ccc's catalogs and overrides with {consumer.submodule}/{SYNC_SCRIPT} and {refreshed}"
    else:
        body = f"Moved {consumer.submodule} to the new catalogs and {refreshed}"
    message = f"{_ccc_title(ccc_commit)}\n\n{body}\n"
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-F", "-"], input=message, text=True, check=True,
                   env={**_IDENTITY, **os.environ})
    return git(repo, "rev-parse", "HEAD", stdout=subprocess.PIPE).strip()


class _GitLab(NamedTuple):
    """The GitLab API at `api_url` (no trailing slash), called with `token`."""
    api_url: str
    token: str

    def call(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """The decoded JSON response to `method` `path`; raises ConsumerError on an HTTP or network error."""
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(f"{self.api_url}{path}", data=body, method=method, headers={
            "PRIVATE-TOKEN": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ConsumerError(f"{method} {path}: HTTP {error.code} {error.read().decode(errors='replace')[:500]}") from error
        except urllib.error.URLError as error:
            raise ConsumerError(f"{method} {path}: {error.reason}") from error


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
            raise ConsumerError(f"!{iid}: no pipeline for {sha[:8]} after {_PIPELINE_WAIT}s; auto-merge not set")
        time.sleep(10)
    gitlab.call("PUT", f"/projects/{project_id}/merge_requests/{iid}/merge",
                {"merge_when_pipeline_succeeds": True, "should_remove_source_branch": True, "sha": sha})


def _description(consumer: Consumer, ccc_commit: str) -> str:
    server = os.environ.get("CI_PROJECT_URL", "https://gitlab.syncad.com/hive/common-ci-configuration")
    change = ("merges the catalogs into pnpm-workspace.yaml and refreshes pnpm-lock.yaml" if consumer.mode == MERGE
              else "refreshes each pnpm-lock.yaml next to a pnpm-workspace.yaml symlinked into it")
    merging = ("It is set to merge when its pipeline succeeds." if consumer.auto_merge
               else "This project is listed with `no-auto-merge`: it waits for a person to review and merge it.")
    return (f"common-ci-configuration's pnpm catalogs changed in {server}/-/commit/{ccc_commit}. This merge request, opened by its"
            f" `pnpm_catalog_resync` job, moves the `{consumer.submodule}` submodule there and {change}. {merging}"
            f" The next catalog change force-updates `{BRANCH}`.")


def _publish(gitlab: _GitLab, project: Dict[str, Any], consumer: Consumer, sha: str, ccc_commit: str) -> str:
    """Open or update the re-sync merge request of `sha`, already pushed to BRANCH; its URL."""
    project_id, target = project["id"], project["default_branch"]
    fields = {"title": _ccc_title(ccc_commit), "description": _description(consumer, ccc_commit)}
    existing = _open_merge_request(gitlab, project_id, target)
    if existing:
        request = gitlab.call("PUT", f"/projects/{project_id}/merge_requests/{existing['iid']}", fields)
    else:
        request = gitlab.call("POST", f"/projects/{project_id}/merge_requests",
                              {**fields, "source_branch": BRANCH, "target_branch": target, "remove_source_branch": True})
    if consumer.auto_merge:
        _merge_when_pipeline_succeeds(gitlab, project_id, request["iid"], sha)
    return str(request["web_url"])


def _process(gitlab: _GitLab, consumer: Consumer, server_url: str, ccc_commit: str) -> str:
    project = gitlab.call("GET", f"/projects/{urllib.parse.quote(consumer.project, safe='')}")
    target = project["default_branch"]
    with tempfile.TemporaryDirectory(prefix="ccc-resync-") as work:
        repo = Path(work) / "repo"
        clone(server_url, consumer.project, repo, target)
        sha = resync(repo, consumer, ROOT, ccc_commit)
        if sha is None:
            stale = _open_merge_request(gitlab, project["id"], target)
            if stale:
                gitlab.call("PUT", f"/projects/{project['id']}/merge_requests/{stale['iid']}", {"state_event": "close"})
                return f"in sync on {target}; closed the stale {stale['web_url']}"
            return f"in sync on {target}"
        git(repo, "push", "--force", "origin", f"{sha}:refs/heads/{BRANCH}")
        return _publish(gitlab, project, consumer, sha, ccc_commit)


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
        consumers, ccc_commit = start(args.commit)
    except (OSError, subprocess.CalledProcessError, ConsumerError) as error:
        print(f"pnpm-catalog-resync: {error}: refusing to write lockfiles", file=sys.stderr)
        return 1
    server_url = os.environ.get("CI_SERVER_URL", "https://gitlab.syncad.com").rstrip("/")
    os.environ.update(git_auth_environment(server_url, token))
    gitlab = _GitLab(os.environ.get("CI_API_V4_URL", f"{server_url}/api/v4").rstrip("/"), token)
    failed = []
    for consumer in consumers:
        print(f"== {consumer.project}", flush=True)
        try:
            print(f"{consumer.project}: {_process(gitlab, consumer, server_url, ccc_commit)}", flush=True)
        except (OSError, subprocess.CalledProcessError, ConsumerError) as error:
            print(f"{consumer.project}: FAILED: {error}", file=sys.stderr, flush=True)
            failed.append(consumer.project)
    if failed:
        print(f"pnpm-catalog-resync: not re-synced: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
