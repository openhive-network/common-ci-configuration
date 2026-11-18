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
project depending on `vue-tsc` `vue-tsc --noEmit -p tsconfig.json`, or for one
depending on `typescript` (itself or through the directory's package.json)
`tsc --noEmit -p tsconfig.json`. A project with a tsconfig.json declaring no
compiler (its TypeScript runs through tsx, a test runner or a bundler) gets the
same tsc check only when a tsc resolves in its install (its own or the
directory's node_modules/.bin, else PATH), and only advisorily: it is reported
as passed when tsc passes, otherwise as `not checked: no declared compiler; tsc
found <first error>`, never failing the consumer.
A workspace project without such a script whose entry is its TypeScript sources
(denser's packages) compiles only as part of the projects depending on it, under
their compiler options: when one of those is type-checked it is reported as
checked through them rather than type-checked alone.
A type check failing only because tsc cannot find modules inside the package's own
tree that its checkout lacks (its own build output, such as wax's wasm build) is
reported as not checked, naming the missing paths, and one failing only on
TS18003 (its tsconfig matches no inputs) as having no type check; neither fails
the consumer, and a failure names its first other error.
A dependency on a package of the clone that ships its type declarations only
once built (wax's examples and signers on `@hiveio/wax`, which needs the wasm
toolchain) is pointed at the catalog entry pinning its published version
before the install, which then also takes ccc's scoped registries
(pnpm-config/.npmrc). A dependency on such a project of the workspace that no
catalog pins (hive-io's @hiveio/hive-lib) is built with its own `build` script
after the install, before the type check of the project depending on it; a
dependent of one without a build script, or whose build fails or does not
produce its entry, is reported as not checked, naming that package's build.

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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Set, Tuple

from pnpm_catalog_consumers import (
    ROOT, Consumer, ConsumerError, clone, git_auth_environment, point_at, start)
from pnpm_catalogs import Catalogs, CatalogError, read_catalogs

TOKEN_VARIABLE = "CATALOG_RESYNC_TOKEN"
INSTALL_COMMAND = ("pnpm", "install", "--no-frozen-lockfile")
TYPE_CHECK_SCRIPTS = ("typecheck", "type-check", "tsc")
NUXT_PREPARE_COMMAND = ("pnpm", "exec", "nuxi", "prepare")
NUXT_TYPE_CHECK_COMMAND = ("pnpm", "exec", "nuxi", "typecheck")
_TSC_COMMAND = ("pnpm", "exec", "tsc", "--noEmit", "-p", "tsconfig.json")
_NUXT_CONFIGS = ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs", "nuxt.config.mts", "nuxt.config.cjs", "nuxt.config.cts")
_NPMRC = ROOT / "pnpm-config" / ".npmrc"
_SCOPED_REGISTRY = re.compile(r"@[^\s=]+:registry=\S+")
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
# Tried in order: a TypeScript diagnostic, then a pnpm error code, then any line naming an error.
_ERROR_PATTERNS = (re.compile(r"error TS\d+"), re.compile(r"ERR_PNPM_\w+"), re.compile(r"\berror\b", re.IGNORECASE))
# A whole tsc diagnostic line: plain (`file(line,col): error TSn: ...`), pretty (`file:line:col - error TSn: ...`) or without a file.
_DIAGNOSTIC = re.compile(r"(?:(?P<file>[^\s(]+)(?:\(\d+,\d+\):|:\d+:\d+ -) )?error (?P<code>TS\d+): (?P<message>.*)")
_MISSING_MODULE = re.compile(r"Cannot find module '(?P<module>\.{1,2}/[^']*)'")
_MISSING_MODULE_CODE = "TS2307"
_NO_INPUTS = "TS18003"
_RELATIVE_IMPORT = re.compile(r"""(?:\bfrom|\bimport|\brequire\(|\bimport\()\s*["'](\.{1,2}/[^"']+)["']""")
_SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".d.ts")
_SCRIPT_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")
_BUILD_SCRIPT = "build"
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MAX_ERROR_LENGTH = 300
_CREDENTIAL_VARIABLES = frozenset((
    TOKEN_VARIABLE, "CI_JOB_TOKEN", "CI_BUILD_TOKEN", "CI_JOB_JWT", "CI_JOB_JWT_V1", "CI_JOB_JWT_V2",
    "CI_REGISTRY_PASSWORD", "CI_DEPENDENCY_PROXY_PASSWORD", "CI_DEPLOY_PASSWORD", "CI_REPOSITORY_URL"))


class _PackageCheck(NamedTuple):
    """One package's type check: its exit status, the first error of a failure, and the paths
    of its own build output a failure needed (sorted, repo-relative; empty for any other
    failure) or whether it only reported TS18003; or, when it did not run, the builds of the
    workspace packages it needs that are missing (`<package>'s build (<reason>)`). `advisory`
    marks a tsc check of a package declaring no compiler, whose failure fails nothing."""
    status: int
    first: Optional[str] = None
    missing_build: Tuple[str, ...] = ()
    no_inputs: bool = False
    unbuilt: Tuple[str, ...] = ()
    advisory: bool = False


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


def type_check_command(project: Path, workspace: Optional[Path] = None) -> Optional[List[str]]:
    """The command type-checking the package at `project` (a project of the workspace at
    `workspace`, if given): its first TYPE_CHECK_SCRIPTS script; else for a Nuxt project
    `nuxi typecheck` when it depends on vue-tsc (nuxi's type checker), None otherwise; else on
    its tsconfig.json `vue-tsc --noEmit` (for a project depending on vue-tsc) or `tsc --noEmit`
    (for one whose package.json or the workspace's depends on typescript); else None."""
    script = _type_check_script(project)
    if script:
        return ["pnpm", "run", script]
    vue_tsc = _depends_on(_manifest(project), "vue-tsc")
    if _is_nuxt(project):
        return list(NUXT_TYPE_CHECK_COMMAND) if vue_tsc else None
    if not (project / "tsconfig.json").is_file():
        return None
    if vue_tsc:
        return ["pnpm", "exec", "vue-tsc", "--noEmit", "-p", "tsconfig.json"]
    if any(_depends_on(_manifest(package), "typescript") for package in (project, workspace or project)):
        return list(_TSC_COMMAND)
    return None


def _undeclared_tsc_command(project: Path, workspace: Path, environment: Mapping[str, str]) -> Optional[List[str]]:
    """`tsc --noEmit` on the tsconfig.json of a project of the installed workspace at `workspace`
    that type_check_command gives no type check, when it is not a Nuxt project and a tsc resolves
    where `pnpm exec` looks (the project's and the workspace's node_modules/.bin, then PATH in
    `environment`); else None."""
    if _is_nuxt(project) or not (project / "tsconfig.json").is_file():
        return None
    path = [str(package / "node_modules" / ".bin") for package in (project, workspace)] + [environment.get("PATH", "")]
    return list(_TSC_COMMAND) if shutil.which("tsc", path=os.pathsep.join(path)) else None


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


def _export_entry(exports: Any) -> Optional[str]:
    """The path a package.json `exports` value gives its main entry point (`.`), type
    declarations first; None when it gives none."""
    if isinstance(exports, str):
        return exports
    if not isinstance(exports, dict):
        return None
    if "." in exports:
        return _export_entry(exports["."])
    conditions = (exports[key] for key in ("types", "import", "default", "require") if key in exports)
    return next((entry for entry in map(_export_entry, conditions) if entry), None)


def _entry(manifest: Dict[str, Any]) -> Optional[str]:
    """The file a package's dependents take its types (else its code) from: its `types`,
    `typings` or `main`, else its `exports` main entry; None without one."""
    entry = manifest.get("types") or manifest.get("typings") or manifest.get("main") or _export_entry(manifest.get("exports"))
    return entry if isinstance(entry, str) else None


def _is_source(entry: str) -> bool:
    return entry.endswith(_SOURCE_SUFFIXES) and not entry.endswith(".d.ts")


def _needs_build(package: Path) -> bool:
    """True when the package at `package` declares an entry (_entry) its checkout does not
    have that a build would produce (not a TypeScript source), i.e. it only exists once built."""
    entry = _entry(_manifest(package))
    return entry is not None and "*" not in entry and not _is_source(entry) and not (package / entry).is_file()


def _is_source_package(package: Path) -> bool:
    """True when the package at `package` has no type check script and its entry names one of
    its own TypeScript sources (present or not: denser's smart-signer names a removed one),
    which its dependents compile under their compiler options."""
    entry = _entry(_manifest(package))
    return _type_check_script(package) is None and entry is not None and _is_source(entry)


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


def _members(projects: List[Path]) -> Dict[str, Path]:
    """The workspace `projects` by package name."""
    names = ((_manifest(project).get("name"), project) for project in projects)
    return {name: project for name, project in names if isinstance(name, str)}


def _workspace_dependencies(project: Path, projects: List[Path]) -> List[Path]:
    """The projects of `projects` (one workspace) `project` depends on, in declaration order."""
    members = _members(projects)
    found: List[Path] = []
    for mapping, key, name in _local_dependencies(_manifest(project)):
        package = _local_package(project, name, mapping[key], members)
        if package in projects and package != project and package not in found:
            found.append(package)
    return found


def _use_published_packages(projects: List[Path], catalogs: Catalogs) -> List[str]:
    """Point each dependency (or pnpm override) of `projects` on a package of the clone that needs
    a build (_needs_build) at the first catalog pinning that package, rewriting the projects'
    package.json; returns the names of the packages it repointed."""
    members = _members(projects)
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


def _missing_path(file: Path, module: str, package: Path) -> Optional[Path]:
    """The outermost path inside `package` the checkout lacks on the way to `module`, a module
    `file` imports; None when it resolves outside `package` or exists."""
    target = Path(os.path.normpath(file.parent / module))
    if package not in target.parents:
        return None
    for part in [*reversed(target.relative_to(package).parents), target]:
        if not (package / part).exists():
            return package / part
    return None


def _resolved_import(importer: Path, module: str) -> Optional[Path]:
    """The source file the relative import `module` of `importer` names, None when there is none."""
    target = Path(os.path.normpath(importer.parent / module))
    stem = target.with_suffix("") if target.suffix in _SCRIPT_SUFFIXES else target
    candidates = [target, *(stem.with_name(stem.name + suffix) for suffix in _SOURCE_SUFFIXES),
                  *(target / f"index{suffix}" for suffix in _SOURCE_SUFFIXES)]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _imports_any(file: Path, importers: Set[Path], package: Path) -> bool:
    """True when `file` is one of `importers` or reaches one through relative imports inside `package`."""
    seen: Set[Path] = set()
    pending = [file]
    while pending:
        current = pending.pop()
        if current in importers:
            return True
        if current in seen or package not in current.parents or not current.is_file():
            continue
        seen.add(current)
        modules = _RELATIVE_IMPORT.findall(current.read_text(encoding="utf-8", errors="replace"))
        pending += [resolved for resolved in (_resolved_import(current, module) for module in modules) if resolved]
    return False


def _diagnosed(status: int, output: str, package: Path, repo: Path) -> Optional[_PackageCheck]:
    """The type check of `package` (in the clone at `repo`) that failed with `status` and `output`,
    when that names a tsc diagnostic. A TS2307 for a relative module of the package's own tree its
    checkout lacks (its build output) names the missing path, and every other diagnostic in a file
    reaching such an import (types tsc could not know) is taken as its consequence: when that
    accounts for every diagnostic the package is not checked, naming the missing paths; when every
    one is TS18003 it has no inputs; else it failed on its first other diagnostic. None without a
    tsc diagnostic."""
    lines = [_ANSI_ESCAPE.sub("", line).strip() for line in output.splitlines()]
    diagnostics = [(line, _DIAGNOSTIC.fullmatch(line)) for line in lines if _ERROR_PATTERNS[0].search(line)]
    if not diagnostics:
        return None
    sources = {line: Path(os.path.normpath(package / found["file"])) for line, found in diagnostics if found and found["file"]}
    missing: Dict[str, Path] = {}
    for line, found in diagnostics:
        module = _MISSING_MODULE.match(found["message"]) if found and found["code"] == _MISSING_MODULE_CODE else None
        path = _missing_path(sources[line], module["module"], package) if module and line in sources else None
        if path:
            missing[line] = path
    importers = {sources[line] for line in missing}
    real = [line for line, found in diagnostics
            if line not in missing and not (found and found["code"] == _NO_INPUTS and not found["file"])
            and not (line in sources and missing and _imports_any(sources[line], importers, package))]
    if real:
        return _PackageCheck(status, real[0][:_MAX_ERROR_LENGTH])
    paths = sorted({path.relative_to(repo).as_posix() for path in missing.values()})
    return _PackageCheck(status, None, tuple(paths), not paths)


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


def _unbuilt_dependencies(package: Path, projects: List[Path], environment: Mapping[str, str],
                          tried: Dict[Path, Optional[str]]) -> Tuple[str, ...]:
    """Build each project of the workspace `projects` that `package` depends on and that needs a
    build (_needs_build), with its own `build` script (each project at most once: `tried` maps
    one already tried to why it is not built, None when it is). Returns `<package>'s build
    (<reason>)` for each one not built; empty when each one is."""
    unbuilt = []
    for dependency in _workspace_dependencies(package, projects):
        if _needs_build(dependency) and dependency not in tried:
            tried[dependency] = "dependency cycle"
            tried[dependency] = _build(dependency, projects, environment, tried)
        reason = tried.get(dependency)
        if reason:
            unbuilt.append(f"{_manifest(dependency).get('name') or dependency.name}'s build ({reason})")
    return tuple(unbuilt)


def _build(package: Path, projects: List[Path], environment: Mapping[str, str], tried: Dict[Path, Optional[str]]) -> Optional[str]:
    """Build `package`, its own unbuilt workspace dependencies first; None when its entry exists
    afterwards, else why not."""
    if _BUILD_SCRIPT not in _manifest(package).get("scripts", {}):
        return "no build script"
    needed = _unbuilt_dependencies(package, projects, environment, tried)
    if needed:
        return f"needs {', '.join(needed)}"
    command = ["pnpm", "run", _BUILD_SCRIPT]
    print(f"-- {package.name}: {' '.join(command)}", flush=True)
    status, output = _run(command, package, environment)
    if status:
        first = _first_error(output)
        return f"{' '.join(command)} failed (exit {status})" + (f": {first}" if first else "")
    if _needs_build(package):
        return f"{' '.join(command)} did not produce {_entry(_manifest(package))}"
    return None


def _type_check(label: str, package: Path, repo: Path, command: List[str], environment: Mapping[str, str]) -> _PackageCheck:
    """Run _prepare_command(package), if any, then `command` in `package` (inside the clone at
    `repo`); the result of the one that ran last, a failure classified by _diagnosed (a failed
    prepare named as such, and always a failure)."""
    prepare = _prepare_command(package)
    if prepare:
        print(f"-- {label}: {' '.join(prepare)}", flush=True)
        status, output = _run(prepare, package, environment)
        if status:
            first = _first_error(output)
            return _PackageCheck(status, f"{' '.join(prepare)} failed (exit {status})" + (f": {first}" if first else ""))
    print(f"-- {label}: {' '.join(command)}", flush=True)
    status, output = _run(command, package, environment)
    if not status:
        return _PackageCheck(0)
    return _diagnosed(status, output, package, repo) or _PackageCheck(status, _first_error(output))


def _type_check_outcome(checks: List[Tuple[str, _PackageCheck]]) -> Tuple[str, bool]:
    """The outcome of a directory whose packages' type checks (`where`, result) are `checks`, and
    whether it passed: a package not checked for its own build output or a workspace package's,
    or without inputs, or an advisory check that failed, fails nothing."""
    missing_build = [path for _, check in checks for path in check.missing_build]
    unbuilt = sorted({build for _, check in checks for build in check.unbuilt})
    ran = [(where, check) for where, check in checks if not check.missing_build and not check.no_inputs and not check.unbuilt]
    advisory = [check for _, check in ran if check.advisory and check.status]
    checked = [(where, check) for where, check in ran if not (check.advisory and check.status)]
    failed = [(where, check.first) for where, check in checked if check.status]
    own = [f"needs its own build ({', '.join(missing_build)})"] if missing_build else []
    reasons = own + [f"needs {build}" for build in unbuilt]
    if advisory:
        first = next((check.first for check in advisory if check.first), None)
        reasons.append(f"no declared compiler; tsc found {first}" if first else f"no declared compiler; tsc failed (exit {advisory[0].status})")
    needs_build = f"not checked: {'; '.join(reasons)}"
    unchecked = f"; {needs_build}" if reasons else ""
    if failed:
        places = ", ".join(where for where, _ in failed)
        where, first = next(((where, first) for where, first in failed if first), failed[0])
        detail = f"; first error ({where}): {first}" if first else ""
        return f"type check failed in {places}{detail}{unchecked}", False
    if checked:
        return f"passed{unchecked}", True
    if reasons:
        return needs_build, True
    if checks:  # each one without inputs
        return "installed; no type check found (tsconfig has no inputs)", True
    return "installed; no type check found", True


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
    checks, through_dependents = _type_checks(project, repo, workspace, projects, environment)
    outcome, passed = _type_check_outcome(checks)
    if through_dependents:
        sources = ", ".join(package.relative_to(repo).as_posix() for package in through_dependents)
        outcome += f"; {sources} checked as TypeScript sources of their dependents"
    return Result(project, directory, outcome + note, passed)


def _type_checks(project: str, repo: Path, workspace: Path, projects: List[Path],
                 environment: Mapping[str, str]) -> Tuple[List[Tuple[str, _PackageCheck]], List[Path]]:
    """Type-check the installed workspace at `workspace` (its projects `projects`, in the clone of
    `project` at `repo`): its root when it has a type check script, else each project with a type
    check except the source packages checked through their dependents, after building the
    workspace packages each one needs built. Returns each check (`where`, result) and those
    source packages."""
    packages = [workspace] if _type_check_script(workspace) else projects
    declared = {package: type_check_command(package, workspace) for package in packages}
    commands = {package: command or _undeclared_tsc_command(package, workspace, environment) for package, command in declared.items()}
    through_dependents = _checked_through_dependents(commands, projects)
    tried: Dict[Path, Optional[str]] = {}
    checks: List[Tuple[str, _PackageCheck]] = []
    for package, command in commands.items():
        if command is None or package in through_dependents:
            continue
        where = package.relative_to(repo).as_posix() or "."
        unbuilt = _unbuilt_dependencies(package, projects, environment, tried)
        if unbuilt:
            print(f"-- {project} {where}: not type-checked: needs {', '.join(unbuilt)}", flush=True)
            checks.append((where, _PackageCheck(0, unbuilt=unbuilt)))
        else:
            check = _type_check(f"{project} {where}", package, repo, command, environment)
            checks.append((where, check._replace(advisory=declared[package] is None)))
    return checks, through_dependents


def _checked_through_dependents(commands: Mapping[Path, Optional[List[str]]], projects: List[Path]) -> List[Path]:
    """The source packages (_is_source_package) among `commands` (package: its type check
    command) that a package with a type check, not itself a source package, depends on directly
    or through other source packages; in `commands` order."""
    sources = {package for package, command in commands.items() if command and _is_source_package(package)}
    pending = [package for package, command in commands.items() if command and package not in sources]
    reached: Set[Path] = set()
    while pending:
        for dependency in _workspace_dependencies(pending.pop(), projects):
            if dependency in sources and dependency not in reached:
                reached.add(dependency)
                pending.append(dependency)
    return [package for package in commands if package in reached]


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
