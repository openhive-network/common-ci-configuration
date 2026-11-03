"""scripts/github-actions/npm-rewrite-package.sh: pointing a GitLab-built npm tarball at npmjs and GitHub."""

import json
import subprocess
import tarfile
from pathlib import Path
from typing import Dict, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "github-actions" / "npm-rewrite-package.sh"

GITLAB_MANIFEST = {
    "name": "@hiveio/workerbee",
    "version": "1.28.4-rc1",
    "description": "kept as is",
    "repository": {"type": "git", "url": "https://gitlab.syncad.com/hive/workerbee.git"},
    "publishConfig": {"registry": "https://gitlab.syncad.com/api/v4/projects/452/packages/npm/", "tag": "latest", "access": "public"},
    "scripts": {"prepack": "exit 1"},
}


def make_tarball(tmp_path: Path, manifest: Optional[dict] = GITLAB_MANIFEST, extra: Optional[Dict[str, str]] = None) -> Path:
    package = tmp_path / "src" / "package"
    package.mkdir(parents=True)
    if manifest is not None:
        (package / "package.json").write_text(json.dumps(manifest, indent=2))
    for name, content in (extra or {"dist/index.js": "module.exports = 1;\n", "README.md": "# pkg\n"}).items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text(content)
    tarball = tmp_path / "hiveio-workerbee-1.28.4-rc1.tgz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(package, arcname="package")
    return tarball


def run(tmp_path: Path, tarball: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), "--tarball", str(tarball), "--output-dir", str(tmp_path / "out"),
         "--output-env", str(tmp_path / "out.env"), *args],
        capture_output=True, text=True, check=False)


def manifest_of(tarball: Path) -> dict:
    with tarfile.open(tarball) as archive:
        return json.load(archive.extractfile("package/package.json"))


def members_of(tarball: Path):
    with tarfile.open(tarball) as archive:
        return sorted(m.name for m in archive.getmembers() if m.isfile())


def test_repository_and_publish_config_point_at_github_and_npmjs(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--repository-url", "https://github.com/openhive-network/workerbee")

    assert result.returncode == 0, result.stderr
    rewritten = tmp_path / "out" / tarball.name
    manifest = manifest_of(rewritten)
    assert manifest["repository"] == {"type": "git", "url": "git+https://github.com/openhive-network/workerbee.git"}
    assert manifest["publishConfig"] == {"registry": "https://registry.npmjs.org/", "tag": "latest", "access": "public"}
    assert manifest["scripts"] == {"prepack": "exit 1"}
    assert manifest["description"] == "kept as is"
    assert members_of(rewritten) == ["package/README.md", "package/dist/index.js", "package/package.json"]
    assert dict(line.split("=", 1) for line in (tmp_path / "out.env").read_text().splitlines()) == {
        "package_name": "@hiveio/workerbee", "package_version": "1.28.4-rc1",
        "package_dist_tag": "latest", "package_tarball": str(rewritten)}


@pytest.mark.parametrize("given", [
    "https://github.com/openhive-network/workerbee.git",
    "https://github.com/openhive-network/workerbee/",
    "git+https://github.com/openhive-network/workerbee.git",
])
def test_repository_url_is_normalised_to_the_form_npm_writes(tmp_path: Path, given: str) -> None:
    tarball = make_tarball(tmp_path)

    assert run(tmp_path, tarball, "--repository-url", given).returncode == 0
    assert manifest_of(tmp_path / "out" / tarball.name)["repository"]["url"] == "git+https://github.com/openhive-network/workerbee.git"


def test_monorepo_package_gets_repository_directory_and_custom_registry_and_tag(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--repository-url", "https://github.com/openhive-network/wax",
                 "--repository-directory", "ts/packages/signers", "--registry", "https://npm.example/", "--dist-tag", "next")

    assert result.returncode == 0, result.stderr
    manifest = manifest_of(tmp_path / "out" / tarball.name)
    assert manifest["repository"] == {"type": "git", "url": "git+https://github.com/openhive-network/wax.git", "directory": "ts/packages/signers"}
    assert manifest["publishConfig"]["registry"] == "https://npm.example/"
    assert manifest["publishConfig"]["tag"] == "next"


def test_npmrc_inside_the_package_is_dropped(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path, extra={".npmrc": "//registry/:_authToken=x\n", "index.js": ""})

    result = run(tmp_path, tarball, "--repository-url", "https://github.com/o/r")

    assert result.returncode == 0, result.stderr
    assert members_of(tmp_path / "out" / tarball.name) == ["package/index.js", "package/package.json"]
    assert "Dropping .npmrc" in result.stdout


@pytest.mark.parametrize("args", [["--expected-version", "1.28.4-rc1"], ["--tag", "1.28.4-rc1"]])
def test_version_check_accepts_the_tag_the_tarball_was_built_from(tmp_path: Path, args) -> None:
    tarball = make_tarball(tmp_path)

    assert run(tmp_path, tarball, "--repository-url", "https://github.com/o/r", *args).returncode == 0


def test_subproject_infix_is_dropped_from_the_tag_like_npm_generate_version_does(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path, manifest={**GITLAB_MANIFEST, "version": "1.28.4rc1"})

    result = run(tmp_path, tarball, "--repository-url", "https://github.com/o/r",
                 "--tag", "1.28.4-workerbee-rc1", "--tag-subproject", "workerbee")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("args", [
    ["--expected-version", "1.28.4"],
    ["--tag", "1.28.5-rc1"],
    ["--tag", "1.28.4-rc1-stable.261030110955"],
])
def test_version_mismatch_fails_without_writing_a_tarball(tmp_path: Path, args) -> None:
    tarball = make_tarball(tmp_path)

    result = run(tmp_path, tarball, "--repository-url", "https://github.com/o/r", *args)

    assert result.returncode == 1
    assert "has version 1.28.4-rc1, expected" in result.stderr
    assert not (tmp_path / "out").exists()


def test_tarball_without_package_json_is_rejected(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path, manifest=None)

    result = run(tmp_path, tarball, "--repository-url", "https://github.com/o/r")

    assert result.returncode == 1
    assert "no package/package.json" in result.stderr


def test_missing_required_options_print_usage(tmp_path: Path) -> None:
    result = subprocess.run(["bash", str(SCRIPT), "--tarball", "x.tgz"], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "Usage:" in result.stderr
    assert "--repository-url are required" in result.stderr
