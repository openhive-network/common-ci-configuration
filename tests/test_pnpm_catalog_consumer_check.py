"""scripts/python/pnpm-catalog-consumer-check.py: which type check a consumer gets, and how its result is reported.

A stand-in pnpm logs every command (and, with PNPM_ENV_LOG, its environment),
lists the fixture's workspace projects and fails the commands named in
PNPM_FAIL (printing PNPM_FAIL_OUTPUT first), so nothing touches the network.
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "python"

STUB_PNPM = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
command = " ".join(sys.argv[1:])
with open(os.environ["PNPM_LOG"], "a", encoding="utf-8") as log:
    log.write(f"{Path.cwd().name}: {command}\\n")
if os.environ.get("PNPM_ENV_LOG"):
    with open(os.environ["PNPM_ENV_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(dict(os.environ)) + "\\n")
if sys.argv[1:3] == ["list", "--recursive"]:
    print(Path.cwd())
    for package in sorted(Path("packages").glob("*/package.json")):
        print(package.parent.resolve())
if command in os.environ.get("PNPM_FAIL", "").split(";"):
    print(os.environ.get("PNPM_FAIL_OUTPUT", ""))
    sys.exit(1)
"""


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPTS))
check_module = _load("pnpm_catalog_consumer_check", SCRIPTS / "pnpm-catalog-consumer-check.py")


def _package(directory: Path, scripts: str, tsconfig: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(f'{{"name": "{directory.name}", "scripts": {scripts}}}')
    if tsconfig:
        (directory / "tsconfig.json").write_text("{}")
    return directory


@pytest.mark.parametrize("scripts, tsconfig, expected", [
    ('{"tsc": "tsc", "type-check": "vue-tsc", "typecheck": "tsc -b"}', True, ["pnpm", "run", "typecheck"]),
    ('{"tsc": "tsc", "type-check": "vue-tsc"}', False, ["pnpm", "run", "type-check"]),
    ('{"tsc": "tsc --noEmit"}', False, ["pnpm", "run", "tsc"]),
    ('{"build": "tsc"}', True, ["pnpm", "exec", "tsc", "--noEmit", "-p", "tsconfig.json"]),
    ('{"build": "vite build"}', False, None),
])
def test_type_check_prefers_a_script_then_the_tsconfig(tmp_path: Path, scripts: str, tsconfig: bool, expected: List[str]) -> None:
    assert check_module.type_check_command(_package(tmp_path / "app", scripts, tsconfig)) == expected


def _vue_package(directory: Path, manifest: Dict[str, Any], tsconfig: bool = True, nuxt_config: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(json.dumps({"name": directory.name, **manifest}))
    if tsconfig:
        (directory / "tsconfig.json").write_text('{"extends": "./.nuxt/tsconfig.json"}')
    if nuxt_config:
        (directory / "nuxt.config.ts").write_text("export default defineNuxtConfig({})\n")
    return directory


@pytest.mark.parametrize("manifest, tsconfig, nuxt_config, expected", [
    ({"dependencies": {"nuxt": "catalog:vue-toolset"}, "devDependencies": {"vue-tsc": "catalog:vue-toolset"}}, True, True,
     ["pnpm", "exec", "nuxi", "typecheck"]),
    ({"devDependencies": {"nuxt": "catalog:vue-toolset", "vue-tsc": "catalog:vue-toolset"}}, True, False,
     ["pnpm", "exec", "nuxi", "typecheck"]),
    ({"devDependencies": {"vue-tsc": "catalog:vue-toolset"}}, False, True, ["pnpm", "exec", "nuxi", "typecheck"]),
    ({"dependencies": {"nuxt": "catalog:vue-toolset", "vue": "catalog:vue-toolset"}}, True, True, None),
    ({"devDependencies": {"vue-tsc": "catalog:vue-toolset"}}, True, False,
     ["pnpm", "exec", "vue-tsc", "--noEmit", "-p", "tsconfig.json"]),
    ({"devDependencies": {"vue-tsc": "catalog:vue-toolset"}}, False, False, None),
    ({"devDependencies": {"typescript": "catalog:typescript-toolset"}}, True, False,
     ["pnpm", "exec", "tsc", "--noEmit", "-p", "tsconfig.json"]),
    ({"scripts": {"typecheck": "vue-tsc -b"}, "dependencies": {"nuxt": "catalog:vue-toolset"}}, True, True,
     ["pnpm", "run", "typecheck"]),
])
def test_vue_and_nuxt_projects_get_their_own_type_checker(
        tmp_path: Path, manifest: Dict[str, Any], tsconfig: bool, nuxt_config: bool, expected: List[str]) -> None:
    assert check_module.type_check_command(_vue_package(tmp_path / "app", manifest, tsconfig, nuxt_config)) == expected


@pytest.fixture(name="pnpm_log")
def _pnpm_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "pnpm").write_text(STUB_PNPM)
    (tools / "pnpm").chmod(0o755)
    log = tmp_path / "pnpm.log"
    log.touch()
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    monkeypatch.setenv("PNPM_LOG", str(log))
    return log


def test_workspace_without_a_root_script_checks_each_project(tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _package(repo, '{"build": "pnpm -r build"}')
    _package(repo / "packages" / "lib", '{"build": "tsc"}', tsconfig=True)
    _package(repo / "packages" / "ui", '{"type-check": "vue-tsc --noEmit"}')
    _package(repo / "packages" / "docs", '{"build": "typedoc"}')
    monkeypatch.setenv("PNPM_FAIL", "run type-check")

    result = check_module._check_directory("hive/app", repo, ".")

    assert result == check_module.Result("hive/app", ".", "type check failed in packages/ui", False)
    assert pnpm_log.read_text().splitlines() == [
        "repo: list --recursive --depth -1 --parseable",
        "repo: install --no-frozen-lockfile",
        "lib: exec tsc --noEmit -p tsconfig.json",
        "ui: run type-check",
    ]


def test_root_type_check_script_covers_the_workspace(tmp_path: Path, pnpm_log: Path) -> None:
    repo = tmp_path / "repo"
    _package(repo / "ts", '{"typecheck": "tsc -b"}', tsconfig=True)
    _package(repo / "ts" / "packages" / "lib", '{"typecheck": "tsc"}')

    assert check_module._check_directory("hive/wax", repo, "ts") == check_module.Result("hive/wax", "ts", "passed", True)
    assert pnpm_log.read_text().splitlines() == [
        "ts: list --recursive --depth -1 --parseable", "ts: install --no-frozen-lockfile", "ts: run typecheck"]


def test_failure_reports_the_first_type_error_of_the_first_failing_project(
        tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _package(repo, '{"build": "pnpm -r build"}')
    _package(repo / "packages" / "lib", '{"typecheck": "tsc"}')
    _package(repo / "packages" / "ui", '{"typecheck": "vue-tsc"}')
    monkeypatch.setenv("PNPM_FAIL", "run typecheck")
    monkeypatch.setenv("PNPM_FAIL_OUTPUT", "> tsc\nsrc/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
                                           "src/b.ts(1,1): error TS2304: Cannot find name 'x'.\n ELIFECYCLE  Command failed with exit code 2.")

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "type check failed in packages/lib, packages/ui; first error (packages/lib): "
        "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.", False)


def test_failed_install_skips_the_type_check(tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _package(tmp_path / "repo", '{"typecheck": "tsc"}')
    monkeypatch.setenv("PNPM_FAIL", "install --no-frozen-lockfile")
    monkeypatch.setenv("PNPM_FAIL_OUTPUT", "Progress: resolved 1\n\x1b[31m ERR_PNPM_FETCH_404\x1b[39m GET https://registry/x: Not Found")

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "install failed (exit 1): ERR_PNPM_FETCH_404 GET https://registry/x: Not Found", False)
    assert pnpm_log.read_text().splitlines() == ["repo: list --recursive --depth -1 --parseable", "repo: install --no-frozen-lockfile"]


def test_project_without_a_type_check_passes_on_its_install(tmp_path: Path, pnpm_log: Path) -> None:
    repo = _package(tmp_path / "repo", '{"build": "vite build"}')

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "installed; no type check found", True)
    assert pnpm_log.read_text().splitlines() == ["repo: list --recursive --depth -1 --parseable", "repo: install --no-frozen-lockfile"]


def test_install_and_type_check_never_see_the_token_or_ci_credentials(
        tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _package(repo, '{"build": "pnpm -r build"}')
    _package(repo / "packages" / "lib", '{"typecheck": "tsc"}')
    environments = tmp_path / "environments.jsonl"
    monkeypatch.setenv("PNPM_ENV_LOG", str(environments))
    credentials = {
        "CATALOG_RESYNC_TOKEN": "glpat-secret",
        "CI_JOB_TOKEN": "job-token",
        "CI_REGISTRY_PASSWORD": "registry-password",
        "CI_REPOSITORY_URL": "https://gitlab-ci-token:job-token@gitlab.example/hive/ccc.git",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://gitlab.example/.extraHeader",
        "GIT_CONFIG_VALUE_0": "Authorization: Basic b2F1dGgyOmdscGF0LXNlY3JldA==",
        "COPIED_TOKEN": "prefix-glpat-secret",
    }
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CI_PROJECT_PATH", "hive/common-ci-configuration")

    assert check_module._check_directory("hive/app", repo, ".").passed

    seen = [json.loads(line) for line in environments.read_text().splitlines()]
    assert len(seen) == 3  # workspace listing, install, the package's type check
    for environment in seen:
        assert not set(credentials) & set(environment)
        assert not any("glpat-secret" in value or "job-token" in value for value in environment.values())
        assert environment["CI_PROJECT_PATH"] == "hive/common-ci-configuration"
        assert environment["PATH"] == os.environ["PATH"]


def _catalog_workspace(directory: Path) -> None:
    (directory / "pnpm-workspace.yaml").write_text(
        'packages:\n  - ./packages/**/*\n\ncatalogs:\n  hiveio-toolset:\n    "@hiveio/wax": 1.28.8-stable.1\n'
        '    "@hiveio/hb-auth": 1.28.4-stable.1\n  other:\n    "@hiveio/built": 1.0.0\n')


def _unbuilt(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(json.dumps({"name": name, "types": "dist/index.d.ts", "main": "dist/index.js"}))
    return directory


def _dependencies(project: Path) -> Dict[str, Any]:
    manifest = json.loads((project / "package.json").read_text())
    return {field: manifest[field] for field in ("dependencies", "devDependencies", "peerDependencies", "pnpm") if field in manifest}


def test_local_packages_that_need_a_build_come_from_the_catalogs(tmp_path: Path, pnpm_log: Path) -> None:
    repo = tmp_path / "repo"
    _unbuilt(repo / "ts", "@hiveio/wax")
    _unbuilt(repo / "unpinned", "@hiveio/unpinned")
    built = _unbuilt(repo / "built", "@hiveio/built")
    (built / "dist").mkdir()
    (built / "dist" / "index.d.ts").write_text("export {};\n")
    app = repo / "examples" / "app"
    app.mkdir(parents=True)
    _catalog_workspace(app)
    (app / "package.json").write_text(json.dumps({
        "name": "app",
        "dependencies": {"@hiveio/wax": "file:../../ts", "@hiveio/built": "link:../../built", "@hiveio/unpinned": "file:../../unpinned"},
        "devDependencies": {"typescript": "catalog:typescript-toolset"},
        "pnpm": {"overrides": {"@hiveio/wax@*": "file:../../ts", "axios": "^1.8.4"}},
    }))

    result = check_module._check_directory("hive/wax", repo, "examples/app")

    assert result == check_module.Result(
        "hive/wax", "examples/app", "installed; no type check found; @hiveio/wax from the catalogs' published versions", True)
    assert _dependencies(app) == {
        "dependencies": {"@hiveio/wax": "catalog:hiveio-toolset", "@hiveio/built": "link:../../built", "@hiveio/unpinned": "file:../../unpinned"},
        "devDependencies": {"typescript": "catalog:typescript-toolset"},
        "pnpm": {"overrides": {"@hiveio/wax@*": "catalog:hiveio-toolset", "axios": "^1.8.4"}},
    }
    listing, install, *rest = pnpm_log.read_text().splitlines()
    assert (listing, rest) == ("app: list --recursive --depth -1 --parseable", [])
    assert re.fullmatch(r"app: install --no-frozen-lockfile --config\.@hiveio:registry=https://\S+", install), install


def test_workspace_members_take_the_published_root_package(tmp_path: Path, pnpm_log: Path) -> None:
    repo = tmp_path / "repo"
    workspace = _unbuilt(repo / "ts", "@hiveio/wax")
    _catalog_workspace(workspace)
    signer = _package(workspace / "packages" / "signer", '{"typecheck": "tsc"}')
    manifest = json.loads((signer / "package.json").read_text())
    manifest.update(devDependencies={"@hiveio/wax": "workspace:../.."}, peerDependencies={"@hiveio/hb-auth": "workspace:*"})
    (signer / "package.json").write_text(json.dumps(manifest))
    _unbuilt(workspace / "packages" / "hb-auth", "@hiveio/hb-auth")

    assert check_module._check_directory("hive/wax", repo, "ts") == check_module.Result(
        "hive/wax", "ts", "passed; @hiveio/hb-auth, @hiveio/wax from the catalogs' published versions", True)
    assert _dependencies(signer) == {
        "devDependencies": {"@hiveio/wax": "catalog:hiveio-toolset"}, "peerDependencies": {"@hiveio/hb-auth": "catalog:hiveio-toolset"}}
    assert pnpm_log.read_text().splitlines()[-1] == "signer: run typecheck"


def test_nuxt_project_is_prepared_before_its_type_check(tmp_path: Path, pnpm_log: Path) -> None:
    repo = tmp_path / "repo"
    _package(repo, '{"build": "pnpm -r build"}')
    _vue_package(repo / "packages" / "app", {"dependencies": {"nuxt": "catalog:vue-toolset"},
                                             "devDependencies": {"vue-tsc": "catalog:vue-toolset"}}, nuxt_config=True)
    _vue_package(repo / "packages" / "example", {"dependencies": {"nuxt": "catalog:vue-toolset"}}, nuxt_config=True)
    _vue_package(repo / "packages" / "ui", {"devDependencies": {"vue-tsc": "catalog:vue-toolset"}})
    _vue_package(repo / "packages" / "site", {"scripts": {"type-check": "nuxt typecheck"}, "dependencies": {"nuxt": "4"}})

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result("hive/app", ".", "passed", True)
    assert pnpm_log.read_text().splitlines() == [
        "repo: list --recursive --depth -1 --parseable",
        "repo: install --no-frozen-lockfile",
        "app: exec nuxi prepare",
        "app: exec nuxi typecheck",
        "site: run type-check",
        "ui: exec vue-tsc --noEmit -p tsconfig.json",
    ]


def test_failed_nuxt_prepare_fails_the_project_without_type_checking_it(
        tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _vue_package(tmp_path / "repo", {"devDependencies": {"nuxt": "catalog:vue-toolset", "vue-tsc": "catalog:vue-toolset"}},
                        nuxt_config=True)
    monkeypatch.setenv("PNPM_FAIL", "exec nuxi prepare")
    monkeypatch.setenv("PNPM_FAIL_OUTPUT", "ERROR  Cannot find module 'shadcn-nuxt'")

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "type check failed in .; first error (.): pnpm exec nuxi prepare failed (exit 1): "
        "ERROR  Cannot find module 'shadcn-nuxt'", False)
    assert pnpm_log.read_text().splitlines() == [
        "repo: list --recursive --depth -1 --parseable", "repo: install --no-frozen-lockfile", "repo: exec nuxi prepare"]


# Captured from `tsc --noEmit -p tsconfig.json` in hive/wax ts, whose sources import its unbuilt wasm output:
# visitor.ts reaches it only through protocol.ts.
WAX_TS_OUTPUT = """wasm/lib/detailed/base.ts(5,28): error TS2307: Cannot find module '../build_wasm/wax.common.js' or its corresponding type declarations.
wasm/lib/detailed/protocol.ts(4,29): error TS2307: Cannot find module '../proto/transaction.js' or its corresponding type declarations.
wasm/lib/detailed/visitor.ts(22,14): error TS2420: Class 'OperationVisitor<R>' incorrectly implements interface 'TOperationVisitor<R>'.
  Index signature for type 'string' is missing in type 'OperationVisitor<R>'.
wasm/lib/index.ts(8,32): error TS2307: Cannot find module './build_wasm/wax.common.js' or its corresponding type declarations.
"""


def _wax_ts(repo: Path) -> Path:
    package = _package(repo / "ts", '{"build": "rollup -c"}', tsconfig=True)
    sources = package / "wasm" / "lib"
    (sources / "detailed").mkdir(parents=True)
    (sources / "index.ts").write_text('export * from "./detailed/base.js";\nimport Wax from "./build_wasm/wax.common.js";\n')
    (sources / "detailed" / "base.ts").write_text('import type { MainModule } from "../build_wasm/wax.common.js";\n')
    (sources / "detailed" / "protocol.ts").write_text('export { transaction } from "../proto/transaction.js";\n')
    (sources / "detailed" / "visitor.ts").write_text('import {\n  operation\n} from "./protocol.js";\n')
    (sources / "detailed" / "chain.ts").write_text('import axios from "axios";\n')
    return package


def test_type_check_needing_only_its_own_build_output_is_not_checked(
        tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _wax_ts(repo)
    monkeypatch.setenv("PNPM_FAIL", "exec tsc --noEmit -p tsconfig.json")
    monkeypatch.setenv("PNPM_FAIL_OUTPUT", WAX_TS_OUTPUT)

    assert check_module._check_directory("hive/wax", repo, "ts") == check_module.Result(
        "hive/wax", "ts", "not checked: needs its own build (ts/wasm/lib/build_wasm, ts/wasm/lib/proto)", True)


@pytest.mark.parametrize("real_error", [
    "wasm/lib/detailed/chain.ts(1,19): error TS2307: Cannot find module 'axios' or its corresponding type declarations.",
    "wasm/lib/detailed/chain.ts:3:7 - error TS2322: Type 'string' is not assignable to type 'number'.",
    "wasm/lib/detailed/chain.ts(1,19): error TS2307: Cannot find module '../../../../outside.js' or its corresponding type declarations.",
])
def test_real_type_error_beside_missing_build_output_fails_naming_it(
        tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch, real_error: str) -> None:
    repo = tmp_path / "repo"
    _wax_ts(repo)
    monkeypatch.setenv("PNPM_FAIL", "exec tsc --noEmit -p tsconfig.json")
    monkeypatch.setenv("PNPM_FAIL_OUTPUT", WAX_TS_OUTPUT + f"\x1b[96m{real_error}\x1b[0m\n")

    assert check_module._check_directory("hive/wax", repo, "ts") == check_module.Result(
        "hive/wax", "ts", f"type check failed in ts; first error (ts): {real_error}", False)


def test_missing_build_output_of_one_project_does_not_hide_the_others(
        tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _package(repo, '{"build": "pnpm -r build"}')
    lib = _package(repo / "packages" / "lib", '{"typecheck": "tsc"}')
    (lib / "src").mkdir()
    (lib / "src" / "index.ts").write_text('export * from "../build/lib.js";\n')
    _package(repo / "packages" / "ui", '{"tsc": "tsc"}')
    monkeypatch.setenv("PNPM_FAIL", "run typecheck")
    monkeypatch.setenv("PNPM_FAIL_OUTPUT",
                       "src/index.ts(1,15): error TS2307: Cannot find module '../build/lib.js' or its corresponding type declarations.")

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "passed; not checked: needs its own build (packages/lib/build)", True)


def test_tsconfig_without_inputs_is_no_type_check(tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _package(tmp_path / "repo", '{"build": "vite build"}', tsconfig=True)
    monkeypatch.setenv("PNPM_FAIL", "exec tsc --noEmit -p tsconfig.json")
    # Captured from tsc 7.0.2 on a tsconfig.json whose include matches no file.
    monkeypatch.setenv("PNPM_FAIL_OUTPUT", f"error TS18003: No inputs were found in config file '{repo}/tsconfig.json'. "
                                           "Specified 'include' paths were '[\"src/**/*.ts\"]' and 'exclude' paths were '[]'.")

    assert check_module._check_directory("hive/wax", repo, ".") == check_module.Result(
        "hive/wax", ".", "installed; no type check found (tsconfig has no inputs)", True)
