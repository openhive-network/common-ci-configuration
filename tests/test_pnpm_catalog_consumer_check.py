"""scripts/python/pnpm-catalog-consumer-check.py: which type check a consumer gets, and how its result is reported.

A stand-in pnpm logs every command, lists the fixture's workspace projects and
fails the commands named in PNPM_FAIL, so nothing touches the network.
"""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "python"

STUB_PNPM = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
command = " ".join(sys.argv[1:])
with open(os.environ["PNPM_LOG"], "a", encoding="utf-8") as log:
    log.write(f"{Path.cwd().name}: {command}\\n")
if sys.argv[1:3] == ["list", "--recursive"]:
    print(Path.cwd())
    for package in sorted(Path("packages").glob("*/package.json")):
        print(package.parent.resolve())
sys.exit(1 if command in os.environ.get("PNPM_FAIL", "").split(";") else 0)
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
        "repo: install --no-frozen-lockfile",
        "repo: list --recursive --depth -1 --parseable",
        "lib: exec tsc --noEmit -p tsconfig.json",
        "ui: run type-check",
    ]


def test_root_type_check_script_covers_the_workspace(tmp_path: Path, pnpm_log: Path) -> None:
    repo = tmp_path / "repo"
    _package(repo / "ts", '{"typecheck": "tsc -b"}', tsconfig=True)
    _package(repo / "ts" / "packages" / "lib", '{"typecheck": "tsc"}')

    assert check_module._check_directory("hive/wax", repo, "ts") == check_module.Result("hive/wax", "ts", "passed", True)
    assert pnpm_log.read_text().splitlines() == ["ts: install --no-frozen-lockfile", "ts: run typecheck"]


def test_failed_install_skips_the_type_check(tmp_path: Path, pnpm_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _package(tmp_path / "repo", '{"typecheck": "tsc"}')
    monkeypatch.setenv("PNPM_FAIL", "install --no-frozen-lockfile")

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "install failed (exit 1)", False)
    assert pnpm_log.read_text().splitlines() == ["repo: install --no-frozen-lockfile"]


def test_project_without_a_type_check_passes_on_its_install(tmp_path: Path, pnpm_log: Path) -> None:
    repo = _package(tmp_path / "repo", '{"build": "vite build"}')

    assert check_module._check_directory("hive/app", repo, ".") == check_module.Result(
        "hive/app", ".", "installed; no type check found", True)
    assert pnpm_log.read_text().splitlines() == ["repo: install --no-frozen-lockfile", "repo: list --recursive --depth -1 --parseable"]
