"""scripts/github-actions/npm-publish-package.sh: idempotent publish/stage of a tarball through a stand-in npm.

The stand-in npm answers `--version`, `view` and `stage list` from environment
variables and logs every other invocation (arguments and working directory).
"""

import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "github-actions" / "npm-publish-package.sh"

STUB_NPM = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print(os.environ.get("NPM_STUB_VERSION", "11.16.0"))
elif args[:1] == ["view"]:
    if os.environ.get("NPM_STUB_PUBLISHED") == "1":
        print(args[1].rsplit("@", 1)[1])
        sys.exit(0)
    sys.exit(1)
elif args[:2] == ["stage", "list"]:
    print(os.environ.get("NPM_STUB_STAGED_JSON", "[]"))
else:
    with open(os.environ["NPM_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"args": args, "cwd": os.getcwd(), "userconfig": os.environ.get("NPM_CONFIG_USERCONFIG")}) + "\\n")
"""

MANIFEST = {"name": "@hiveio/workerbee", "version": "1.28.4-rc1",
            "publishConfig": {"registry": "https://registry.npmjs.org/", "tag": "latest"}}


def make_tarball(tmp_path: Path, manifest: Optional[dict] = MANIFEST) -> Path:
    package = tmp_path / "src" / "package"
    package.mkdir(parents=True)
    if manifest is not None:
        (package / "package.json").write_text(json.dumps(manifest))
    (package / "index.js").write_text("")
    tarball = tmp_path / "pkg.tgz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(package, arcname="package")
    return tarball


def run(tmp_path: Path, tarball: Path, *args: str, stub_env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "npm").write_text(STUB_NPM)
    (tools / "npm").chmod(0o755)
    log = tmp_path / "npm.log"
    log.touch()
    env = {k: v for k, v in os.environ.items() if k != "NODE_AUTH_TOKEN"}
    env.update({"PATH": f"{tools}:{os.environ['PATH']}", "NPM_LOG": str(log), **(stub_env or {})})
    return subprocess.run(["bash", str(SCRIPT), "--tarball", str(tarball), "--output-env", str(tmp_path / "out.env"), *args],
                          env=env, capture_output=True, text=True, check=False)


def npm_calls(tmp_path: Path) -> List[dict]:
    return [json.loads(line) for line in (tmp_path / "npm.log").read_text().splitlines()]


def status(tmp_path: Path) -> str:
    return dict(line.split("=", 1) for line in (tmp_path / "out.env").read_text().splitlines())["publish_status"]


def test_publishes_from_the_unpacked_tarball_with_scripts_disabled(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball)

    assert result.returncode == 0, result.stderr
    (call,) = npm_calls(tmp_path)
    assert call["args"] == ["publish", "--ignore-scripts", "--access", "public", "--tag", "latest", "--registry", "https://registry.npmjs.org/"]
    assert Path(call["cwd"]).name == "package"
    assert status(tmp_path) == "published"
    assert (tmp_path / "out.env").read_text().splitlines()[1:] == [
        "package_name=@hiveio/workerbee", "package_version=1.28.4-rc1", "package_dist_tag=latest"]


def test_already_published_version_is_skipped(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, stub_env={"NPM_STUB_PUBLISHED": "1"})

    assert result.returncode == 0, result.stderr
    assert npm_calls(tmp_path) == []
    assert status(tmp_path) == "skipped"
    assert "already published" in result.stdout


def test_stage_mode_runs_npm_stage_publish(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--mode", "stage", "--access", "restricted")

    assert result.returncode == 0, result.stderr
    (call,) = npm_calls(tmp_path)
    assert call["args"][:2] == ["stage", "publish"]
    assert call["args"][2:] == ["--ignore-scripts", "--access", "restricted", "--tag", "latest", "--registry", "https://registry.npmjs.org/"]
    assert status(tmp_path) == "staged"
    assert "npm stage approve" in result.stdout


def test_stage_mode_skips_a_version_that_is_already_staged(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)
    staged = json.dumps([{"id": "st-1", "version": "1.28.4-rc1"}])

    result = run(tmp_path, tarball, "--mode", "stage", stub_env={"NPM_STUB_STAGED_JSON": staged})

    assert result.returncode == 0, result.stderr
    assert npm_calls(tmp_path) == []
    assert status(tmp_path) == "skipped"


@pytest.mark.parametrize("dry_run_args", [["--dry-run"], ["--dry-run", "true"]])
def test_dry_run_never_publishes_or_stages(tmp_path: Path, dry_run_args: List[str]) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--mode", "stage", *dry_run_args)

    assert result.returncode == 0, result.stderr
    (call,) = npm_calls(tmp_path)
    assert call["args"][:2] == ["publish", "--dry-run"]
    assert status(tmp_path) == "dry-run"


def test_dry_run_false_publishes(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--dry-run", "false")

    assert result.returncode == 0, result.stderr
    assert npm_calls(tmp_path)[0]["args"][:2] == ["publish", "--ignore-scripts"]
    assert status(tmp_path) == "published"


@pytest.mark.parametrize("mode, npm_version", [("publish", "11.5.0"), ("stage", "11.14.9"), ("publish", "10.9.2")])
def test_too_old_npm_is_rejected(tmp_path: Path, mode: str, npm_version: str) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--mode", mode, stub_env={"NPM_STUB_VERSION": npm_version})

    assert result.returncode == 1
    assert f"npm {npm_version} is too old" in result.stderr
    assert npm_calls(tmp_path) == []


@pytest.mark.parametrize("mode, npm_version", [("publish", "11.5.1"), ("stage", "11.15.0")])
def test_minimum_npm_versions_are_accepted(tmp_path: Path, mode: str, npm_version: str) -> None:
    tarball = make_tarball(tmp_path)

    assert run(tmp_path, tarball, "--mode", mode, stub_env={"NPM_STUB_VERSION": npm_version}).returncode == 0


def test_dist_tag_and_registry_default_when_publish_config_is_absent(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path, manifest={"name": "pkg", "version": "1.0.0"})

    result = run(tmp_path, tarball)

    assert result.returncode == 0, result.stderr
    (call,) = npm_calls(tmp_path)
    assert call["args"][-4:] == ["--tag", "latest", "--registry", "https://registry.npmjs.org/"]


def test_node_auth_token_in_the_environment_is_reported(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, stub_env={"NODE_AUTH_TOKEN": "x"})

    assert result.returncode == 0, result.stderr
    assert "NODE_AUTH_TOKEN is set" in result.stderr


def test_tarball_without_package_json_is_rejected(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path, manifest=None)

    result = run(tmp_path, tarball)

    assert result.returncode == 1
    assert "no package/package.json" in result.stderr


def test_unknown_mode_prints_an_error(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--mode", "yolo")

    assert result.returncode == 1
    assert "--mode must be" in result.stderr
