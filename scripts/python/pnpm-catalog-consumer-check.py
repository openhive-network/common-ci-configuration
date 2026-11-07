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
in each workspace project that script, or for a Nuxt project (a nuxt.config.* or
a `nuxt` dependency) depending on `vue-tsc` `nuxi prepare` and then
`nuxi typecheck` (a Nuxt project without vue-tsc has no type check), or for a
project depending on `vue-tsc` `vue-tsc --noEmit -p tsconfig.json`, or
`tsc --noEmit -p tsconfig.json`.
A dependency on a package of the clone that ships its type declarations only
once built (wax's examples and signers on `@hiveio/wax`, which needs the wasm
toolchain) is pointed at the catalog entry pinning its published version
before the install, which then also takes ccc's scoped registries
(pnpm-config/.npmrc); a dependency no catalog pins is left as it is.

  python3 scripts/python/pnpm-catalog-consumer-check.py [--commit SHA] [--summary FILE]

Each consumer's result, with the first error of a failure, is printed and written to FILE. Exit 1 when a consumer
could not be cloned, installed or type-checked. The consumers are cloned with
CATALOG_RESYNC_TOKEN when it is set (read only), anonymously otherwise; their
install and type check, which run the consumer's own code, get an environment
without that token, the CI job's credentials or the clone's git auth. The pnpm
on PATH must be the version docker-bake.hcl's PNPM_VERSION gives the images.
'''
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from pnpm_catalog_consumers import (
    ROOT, Consumer, ConsumerError, clone, git_auth_environment, point_at, start)
from pnpm_catalogs import Catalogs, CatalogError, read_catalogs

TOKEN_VARIABLE = "CATALOG_RESYNC_TOKEN"
INSTALL_COMMAND = ("pnpm", "install", "--no-frozen-lockfile")
TYPE_CHECK_SCRIPTS = ("typecheck", "type-check", "tsc")
NUXT_PREPARE_COMMAND = ("pnpm", "exec", "nuxi", "prepare")
NUXT_TYPE_CHECK_COMMAND = ("pnpm", "exec", "nuxi", "typecheck")
_NUXT_CONFIGS = ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs", "nuxt.config.mts", "nuxt.config.cjs", "nuxt.config.cts")
_NPMRC = ROOT / "pnpm-config" / ".npmrc"
_SCOPED_REGISTRY = re.compile(r"@[^\s=]+:registry=\S+")
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
# Tried in order: a TypeScript diagnostic, then a pnpm error code, then any line naming an error.
_ERROR_PATTERNS = (re.compile(r"error TS\d+"), re.compile(r"ERR_PNPM_\w+"), re.compile(r"\berror\b", re.IGNORECASE))
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MAX_ERROR_LENGTH = 300
_CREDENTIAL_VARIABLES = frozenset((
    TOKEN_VARIABLE, "CI_JOB_TOKEN", "CI_BUILD_TOKEN", "CI_JOB_JWT", "CI_JOB_JWT_V1", "CI_JOB_JWT_V2",
    "CI_REGISTRY_PASSWORD", "CI_DEPENDENCY_PROXY_PASSWORD", "CI_DEPLOY_PASSWORD", "CI_REPOSITORY_URL"))


class Result(NamedTuple):
    """One consumer directory's outcome; `passed` is False for anything that should fail the check."""
    project: str
    directory: str
    outcome: str
    passed: bool


def _consumer_environment(environment: Mapping[str, str]) -> Dict[str, str]:
    """`environment` without what consumer code must not see: CATALOG_RESYNC_TOKEN, the CI job's
    credentials (CI_REPOSITORY_URL embeds the job token), git config passed through the environment
    (the clone's auth header) and any other variable whose value holds the token."""
    token = environment.get(TOKEN_VARIABLE, "")
    return {name: value for name, value in environment.items()
            if name not in _CREDENTIAL_VARIABLES and not name.startswith("GIT_CONFIG_") and not (token and token in value)}


def _manifest(package: Path) -> Dict[str, Any]:
    path = package / "package.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _type_check_script(project: Path) -> Optional[str]:
    scripts = _manifest(project).get("scripts", {})
    return next((name for name in TYPE_CHECK_SCRIPTS if name in scripts), None)


def _depends_on(manifest: Dict[str, Any], name: str) -> bool:
    return any(name in manifest.get(field, {}) for field in _DEPENDENCY_FIELDS)


def _is_nuxt(project: Path) -> bool:
    return any((project / config).is_file() for config in _NUXT_CONFIGS) or _depends_on(_manifest(project), "nuxt")


def _prepare_command(project: Path) -> Optional[List[str]]:
    """The command generating what type_check_command(project) needs: `nuxi prepare` (the .nuxt
    directory, whose tsconfigs a Nuxt project's tsconfig.json extends or references) before
    `nuxi typecheck`, else None."""
    return list(NUXT_PREPARE_COMMAND) if type_check_command(project) == list(NUXT_TYPE_CHECK_COMMAND) else None


def type_check_command(project: Path) -> Optional[List[str]]:
    """The command type-checking the package at `project`: its first TYPE_CHECK_SCRIPTS script;
    else for a Nuxt project `nuxi typecheck` when it depends on vue-tsc (nuxi's type checker),
    None otherwise; else `vue-tsc --noEmit` (for a project depending on vue-tsc) or `tsc --noEmit`
    on its tsconfig.json; else None."""
    script = _type_check_script(project)
    if script:
        return ["pnpm", "run", script]
    vue_tsc = _depends_on(_manifest(project), "vue-tsc")
    if _is_nuxt(project):
        return list(NUXT_TYPE_CHECK_COMMAND) if vue_tsc else None
    if (project / "tsconfig.json").is_file():
        return ["pnpm", "exec", "vue-tsc" if vue_tsc else "tsc", "--noEmit", "-p", "tsconfig.json"]
    return None


def _listed_projects(directory: Path, environment: Mapping[str, str]) -> List[Path]:
    """Every project of the workspace at `directory`, root included."""
    listed = subprocess.run(["pnpm", "list", "--recursive", "--depth", "-1", "--parseable"], cwd=directory, check=True,
                            stdout=subprocess.PIPE, text=True, env=environment).stdout
    return [Path(line) for line in listed.splitlines() if line.strip()] or [directory]


def _local_package(project: Path, name: str, spec: Any, members: Mapping[str, Path]) -> Optional[Path]:
    """The directory in the clone `project`'s dependency `name`: `spec` takes its package from
    (a `file:`, `link:` or `workspace:` spec), None for any other spec."""
    if not isinstance(spec, str):
        return None
    protocol, _, target = spec.partition(":")
    if protocol in ("file", "link") or (protocol == "workspace" and target.startswith(".")):
        return (project / target).resolve()
    if protocol == "workspace":
        return members.get(name)
    return None


def _needs_build(package: Path) -> bool:
    """True when the package at `package` declares type declarations (else a main entry) its
    checkout does not have, i.e. they only exist once it is built."""
    manifest = _manifest(package)
    entry = manifest.get("types") or manifest.get("typings") or manifest.get("main")
    return isinstance(entry, str) and not (package / entry).is_file()


def _override_package(selector: str) -> str:
    """The package a pnpm `overrides` selector (`pkg`, `pkg@range`, `parent>pkg`) overrides."""
    package = selector.rpartition(">")[2]
    at = package.rfind("@")
    return package[:at] if at > 0 else package


def _local_dependencies(manifest: Dict[str, Any]) -> List[Tuple[Dict[str, str], str, str]]:
    """(mapping, key, package name) of every dependency and pnpm override `manifest` declares."""
    entries = [(dependencies, name, name) for field in _DEPENDENCY_FIELDS
               for dependencies in [manifest.get(field, {})] for name in dependencies]
    overrides = manifest.get("pnpm", {}).get("overrides", {})
    return entries + [(overrides, selector, _override_package(selector)) for selector in overrides]


def _use_published_packages(projects: List[Path], catalogs: Catalogs) -> List[str]:
    """Point each dependency (or pnpm override) of `projects` on a package of the clone that needs
    a build (_needs_build) at the first catalog pinning that package, rewriting the projects'
    package.json; returns the names of the packages it repointed."""
    names = ((_manifest(project).get("name"), project) for project in projects)
    members: Dict[str, Path] = {name: project for name, project in names if isinstance(name, str)}
    repointed = set()
    for project in projects:
        manifest = _manifest(project)
        changed = False
        for mapping, key, name in _local_dependencies(manifest):
            catalog = next((catalog for catalog, packages in catalogs.items() if name in packages), None)
            package = _local_package(project, name, mapping[key], members)
            if catalog and package and _needs_build(package):
                mapping[key] = f"catalog:{catalog}"
                repointed.add(name)
                changed = True
        if changed:
            (project / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return sorted(repointed)


def _catalogs(workspace: Path) -> Catalogs:
    manifest = workspace / "pnpm-workspace.yaml"
    if not manifest.is_file():
        return {}
    try:
        return read_catalogs(manifest.read_text(encoding="utf-8"))
    except CatalogError as error:
        print(f"-- {manifest}: catalogs not readable, local packages are not repointed: {error}", flush=True)
        return {}


def _registry_options() -> List[str]:
    """pnpm options giving an install ccc's scoped registries, which hold the published versions the catalogs pin."""
    lines = (line.strip() for line in _NPMRC.read_text(encoding="utf-8").splitlines())
    return [f"--config.{line}" for line in lines if _SCOPED_REGISTRY.fullmatch(line)]


def _first_error(output: str) -> Optional[str]:
    """The first line of `output` naming an error, by _ERROR_PATTERNS precedence; None without one."""
    lines = [_ANSI_ESCAPE.sub("", line).strip() for line in output.splitlines()]
    for pattern in _ERROR_PATTERNS:
        found = next((line for line in lines if pattern.search(line)), None)
        if found:
            return found[:_MAX_ERROR_LENGTH]
    return None


def _run(command: List[str], cwd: Path, environment: Mapping[str, str]) -> Tuple[int, str]:
    """Run `command`, copying its output to stdout as it comes; returns its exit status and output."""
    output = []
    with subprocess.Popen(command, cwd=cwd, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace") as process:
        for line in process.stdout or ():
            sys.stdout.write(line)
            output.append(line)
    sys.stdout.flush()
    return process.returncode, "".join(output)


def _type_check(label: str, package: Path, command: List[str], environment: Mapping[str, str]) -> Tuple[int, Optional[str]]:
    """Run _prepare_command(package), if any, then `command` in `package`; returns the exit status
    of the one that ran last and, when it failed, its first error (a failed prepare named as such)."""
    prepare = _prepare_command(package)
    if prepare:
        print(f"-- {label}: {' '.join(prepare)}", flush=True)
        status, output = _run(prepare, package, environment)
        if status:
            first = _first_error(output)
            return status, f"{' '.join(prepare)} failed (exit {status})" + (f": {first}" if first else "")
    print(f"-- {label}: {' '.join(command)}", flush=True)
    status, output = _run(command, package, environment)
    return status, _first_error(output) if status else None


def _check_directory(project: str, repo: Path, directory: str) -> Result:
    workspace = repo / directory
    environment = _consumer_environment(os.environ)
    try:
        projects = _listed_projects(workspace, environment)
    except subprocess.CalledProcessError as listing:
        return Result(project, directory, f"workspace projects not listed (exit {listing.returncode})", False)
    install = list(INSTALL_COMMAND)
    published = _use_published_packages(projects, _catalogs(workspace))
    note = f"; {', '.join(published)} from the catalogs' published versions" if published else ""
    if published:
        print(f"-- {project} {directory}: {', '.join(published)} need a build: using the catalogs' published versions", flush=True)
        install += _registry_options()
    print(f"-- {project} {directory}: {' '.join(install)}", flush=True)
    status, output = _run(install, workspace, environment)
    if status:
        first = _first_error(output)
        return Result(project, directory, f"install failed (exit {status})" + (f": {first}" if first else "") + note, False)
    failed: List[Tuple[str, Optional[str]]] = []
    checked = 0
    for package in [workspace] if _type_check_script(workspace) else projects:
        command = type_check_command(package)
        if command is None:
            continue
        where = package.relative_to(repo).as_posix() or "."
        checked += 1
        status, first = _type_check(f"{project} {where}", package, command, environment)
        if status:
            failed.append((where, first))
    if failed:
        places = ", ".join(where for where, _ in failed)
        where, first = next(((where, first) for where, first in failed if first), failed[0])
        detail = f"; first error ({where}): {first}" if first else ""
        return Result(project, directory, f"type check failed in {places}{detail}{note}", False)
    if not checked:
        return Result(project, directory, f"installed; no type check found{note}", True)
    return Result(project, directory, f"passed{note}", True)


def _check(consumer: Consumer, server_url: str, ccc_commit: str, clone_environment: Mapping[str, str]) -> List[Result]:
    with tempfile.TemporaryDirectory(prefix="ccc-catalog-check-") as work:
        repo = Path(work) / "repo"
        try:
            clone(server_url, consumer.project, repo, environment=clone_environment)
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
    clone_environment = dict(os.environ)
    if token:
        clone_environment.update(git_auth_environment(server_url, token))
    else:
        print(f"pnpm-catalog-consumer-check: {TOKEN_VARIABLE} is not available to this pipeline: cloning the consumers anonymously", flush=True)
    results = []
    for consumer in consumers:
        print(f"== {consumer.project}", flush=True)
        for result in _check(consumer, server_url, ccc_commit, clone_environment):
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
