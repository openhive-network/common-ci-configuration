"""scripts/bash/npm-helpers/npm_build_package.sh: which workspace projects `pnpm run build` covers.

pnpm's `--recursive` leaves the workspace root out, so a single-package project
whose pnpm-workspace.yaml is ccc's (globs matching nothing) built nothing. The
script is run from a copy next to stand-in version and pack scripts, with a
stand-in pnpm answering `pkg get scripts.build` and `list --recursive` from the
fixture and logging the build command.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bash" / "npm-helpers" / "npm_build_package.sh"

STUB_PNPM = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:3] == ["pkg", "get", "scripts.build"]:
    build = json.loads(Path("package.json").read_text()).get("scripts", {}).get("build")
    print("{}" if build is None else json.dumps(build))
elif args[:2] == ["list", "--recursive"]:
    print(os.getcwd())
    for package in sorted(Path("packages").glob("*/package.json")):
        print(package.parent.resolve())
else:
    with open(os.environ["PNPM_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\\n")
"""


def _build(tmp_path: Path, root_scripts: str, packages: List[str]) -> List[str]:
    """The pnpm build commands npm_build_package.sh runs in a fixture workspace."""
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(SCRIPT, tools / SCRIPT.name)
    for sibling in ("npm_generate_version.sh", "npm_pack_package.sh"):
        (tools / sibling).write_text("#!/bin/sh\n")
        (tools / sibling).chmod(0o755)
    (tools / "pnpm").write_text(STUB_PNPM)
    (tools / "pnpm").chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text(f'{{"name": "root", "scripts": {root_scripts}}}')
    for name in packages:
        (project / "packages" / name).mkdir(parents=True)
        (project / "packages" / name / "package.json").write_text(f'{{"name": "{name}", "scripts": {{"build": "tsc"}}}}')
    log = tmp_path / "pnpm.log"
    log.touch()
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "PNPM_LOG": str(log)}
    subprocess.run(["bash", str(tools / SCRIPT.name), str(project), "registry", "@hiveio", "root", str(tmp_path / "out")],
                   env=env, check=True, capture_output=True, text=True)
    return log.read_text().splitlines()


@pytest.mark.parametrize("root_scripts, packages, expected", [
    ('{"build": "tsc"}', [], "--recursive --workspace-concurrency=1 --include-workspace-root run build"),
    ('{"build": "pnpm -r build"}', ["a", "b"], "--recursive --workspace-concurrency=1 run build"),
    ('{"test": "jest"}', [], "--recursive --workspace-concurrency=1 run build"),
    ("{}", ["a"], "--recursive --workspace-concurrency=1 run build"),
], ids=["root-only-with-build", "root-beside-packages", "root-only-without-build", "packages-only"])
def test_workspace_root_is_built_only_when_it_is_the_only_project(
        tmp_path: Path, root_scripts: str, packages: List[str], expected: str) -> None:
    assert _build(tmp_path, root_scripts, packages) == [expected]
