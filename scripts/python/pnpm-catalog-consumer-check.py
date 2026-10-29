#!/usr/bin/env python3
'''
Type-checks every project listed in pnpm-config/catalog-consumers.txt against
this ccc commit's pnpm catalogs, so a catalog change that breaks one shows up
on its merge request rather than after it lands.

For every consumer: clone its default branch, point its ccc submodule at this
commit (a `merge` consumer also gets the catalogs merged into its
pnpm-workspace.yaml), and in each directory whose lockfile follows the catalogs
run `pnpm install --no-frozen-lockfile` and the type check: the directory's
`typecheck`, `type-check` or `tsc` script when its package.json has one, else
that script or `tsc --noEmit -p tsconfig.json` in each workspace project.

  python3 scripts/python/pnpm-catalog-consumer-check.py [--commit SHA] [--summary FILE]

Each consumer's result is printed and written to FILE. Exit 1 when a consumer
could not be cloned, installed or type-checked. The consumers are cloned with
CATALOG_RESYNC_TOKEN when it is set (read only), anonymously otherwise. The pnpm
on PATH must be the version docker-bake.hcl's PNPM_VERSION gives the images.
'''
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, NamedTuple, Optional

from pnpm_catalog_consumers import (
    ROOT, Consumer, ConsumerError, clone, git_auth_environment, point_at, start)

TOKEN_VARIABLE = "CATALOG_RESYNC_TOKEN"
INSTALL_COMMAND = ("pnpm", "install", "--no-frozen-lockfile")
TYPE_CHECK_SCRIPTS = ("typecheck", "type-check", "tsc")


class Result(NamedTuple):
    """One consumer directory's outcome; `passed` is False for anything that should fail the check."""
    project: str
    directory: str
    outcome: str
    passed: bool


def _type_check_script(project: Path) -> Optional[str]:
    manifest = project / "package.json"
    scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {}) if manifest.is_file() else {}
    return next((name for name in TYPE_CHECK_SCRIPTS if name in scripts), None)


def type_check_command(project: Path) -> Optional[List[str]]:
    """The command type-checking the package at `project`: its first TYPE_CHECK_SCRIPTS script,
    else `tsc --noEmit` on its tsconfig.json, else None."""
    script = _type_check_script(project)
    if script:
        return ["pnpm", "run", script]
    if (project / "tsconfig.json").is_file():
        return ["pnpm", "exec", "tsc", "--noEmit", "-p", "tsconfig.json"]
    return None


def _workspace_projects(directory: Path) -> List[Path]:
    """The workspace root `directory` when its package.json has a type-check script (it checks the
    whole workspace), else every project of the workspace, root included."""
    if _type_check_script(directory):
        return [directory]
    listed = subprocess.run(["pnpm", "list", "--recursive", "--depth", "-1", "--parseable"], cwd=directory, check=True,
                            stdout=subprocess.PIPE, text=True).stdout
    return [Path(line) for line in listed.splitlines() if line.strip()] or [directory]


def _check_directory(project: str, repo: Path, directory: str) -> Result:
    workspace = repo / directory
    print(f"-- {project} {directory}: {' '.join(INSTALL_COMMAND)}", flush=True)
    installed = subprocess.run(list(INSTALL_COMMAND), cwd=workspace, check=False)
    if installed.returncode:
        return Result(project, directory, f"install failed (exit {installed.returncode})", False)
    failed, checked = [], 0
    for package in _workspace_projects(workspace):
        command = type_check_command(package)
        if command is None:
            continue
        where = package.relative_to(repo).as_posix() or "."
        print(f"-- {project} {where}: {' '.join(command)}", flush=True)
        checked += 1
        if subprocess.run(command, cwd=package, check=False).returncode:
            failed.append(where)
    if failed:
        return Result(project, directory, f"type check failed in {', '.join(failed)}", False)
    if not checked:
        return Result(project, directory, "installed; no type check found", True)
    return Result(project, directory, "passed", True)


def _check(consumer: Consumer, server_url: str, ccc_commit: str) -> List[Result]:
    with tempfile.TemporaryDirectory(prefix="ccc-catalog-check-") as work:
        repo = Path(work) / "repo"
        try:
            clone(server_url, consumer.project, repo)
            directories = point_at(repo, consumer, ROOT, ccc_commit)
        except (OSError, subprocess.CalledProcessError, ConsumerError) as error:
            return [Result(consumer.project, ".", f"not checked: {error}", False)]
        return [_check_directory(consumer.project, repo, directory) for directory in directories]


def main() -> int:
    """Check every listed consumer; return the exit status."""
    parser = argparse.ArgumentParser(description="Type-check every listed ccc catalog consumer against a ccc commit's catalogs.")
    parser.add_argument("--commit", default=os.environ.get("CI_COMMIT_SHA", "HEAD"),
                        help="ccc commit whose catalogs the consumers are checked against (default: CI_COMMIT_SHA, else HEAD)")
    parser.add_argument("--summary", type=Path, default=Path("pnpm-catalog-consumer-check.txt"),
                        help="file the per-consumer results are written to")
    args = parser.parse_args()
    try:
        consumers, ccc_commit = start(args.commit)
    except (OSError, subprocess.CalledProcessError, ConsumerError) as error:
        print(f"pnpm-catalog-consumer-check: {error}", file=sys.stderr)
        return 1
    server_url = os.environ.get("CI_SERVER_URL", "https://gitlab.syncad.com").rstrip("/")
    token = os.environ.get(TOKEN_VARIABLE, "")
    if token:
        os.environ.update(git_auth_environment(server_url, token))
    else:
        print(f"pnpm-catalog-consumer-check: {TOKEN_VARIABLE} is not available to this pipeline: cloning the consumers anonymously", flush=True)
    results = []
    for consumer in consumers:
        print(f"== {consumer.project}", flush=True)
        for result in _check(consumer, server_url, ccc_commit):
            print(f"{result.project} {result.directory}: {result.outcome}", flush=True)
            results.append(result)
    failed = [result for result in results if not result.passed]
    lines = [f"pnpm catalog consumer check against common-ci-configuration@{ccc_commit[:8]}", ""]
    lines += [f"{'ok    ' if result.passed else 'FAILED'} {result.project} {result.directory}: {result.outcome}" for result in results]
    lines += ["", f"{len(results) - len(failed)} passed, {len(failed)} failed"]
    args.summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
