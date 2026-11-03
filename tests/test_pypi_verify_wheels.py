"""scripts/github-actions/pypi-verify-wheels.sh: git tag to PEP 440 mapping and distribution file checks."""

import subprocess
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "github-actions" / "pypi-verify-wheels.sh"


def run(tmp_path: Path, files: List[str], *args: str) -> subprocess.CompletedProcess:
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    for name in files:
        (dist / name).parent.mkdir(parents=True, exist_ok=True)
        (dist / name).write_bytes(b"")
    return subprocess.run(["bash", str(SCRIPT), "--dist-dir", str(dist), "--output-env", str(tmp_path / "out.env"), *args],
                          capture_output=True, text=True, check=False)


def outputs(tmp_path: Path) -> dict:
    return dict(line.split("=", 1) for line in (tmp_path / "out.env").read_text().splitlines())


@pytest.mark.parametrize("tag, version", [
    ("1.28.8", "1.28.8"),
    ("v1.2.0", "1.2.0"),
    ("1.28.8-rc3", "1.28.8rc3"),
    ("1.28.8rc3", "1.28.8rc3"),
    ("1.28.8-rc.3", "1.28.8rc3"),
    ("1.28.6-alpha", "1.28.6a0"),
    ("1.28.6-beta.2", "1.28.6b2"),
    ("2.0.0-pre1", "2.0.0rc1"),
])
def test_tag_maps_to_the_pep440_version_poetry_dynamic_versioning_builds(tmp_path: Path, tag: str, version: str) -> None:
    result = run(tmp_path, [f"hiveio_wax-{version}-cp312-cp312-manylinux_2_28_x86_64.whl"], "--tag", tag)

    assert result.returncode == 0, result.stderr
    assert outputs(tmp_path)["pypi_version"] == version


def test_prefixed_tags_are_stripped_before_mapping(tmp_path: Path) -> None:
    result = run(tmp_path, ["hiveio_beekeepy-1.28.5rc1-py3-none-any.whl"], "--tag", "beekeeper-v1.28.5rc1",
                 "--tag-strip-prefix", "beekeeper-v")

    assert result.returncode == 0, result.stderr
    assert outputs(tmp_path)["pypi_version"] == "1.28.5rc1"


def test_all_distributions_of_the_version_are_listed_including_sdists_in_subdirectories(tmp_path: Path) -> None:
    files = ["py312/hiveio_wax-1.28.8rc3-cp312-cp312-manylinux_2_28_x86_64.whl",
             "py314/hiveio_wax-1.28.8rc3-cp314-cp314-manylinux_2_28_x86_64.whl",
             "hiveio_wax-1.28.8rc3.tar.gz"]

    result = run(tmp_path, files, "--tag", "1.28.8-rc3", "--expected-name", "hiveio-wax")

    assert result.returncode == 0, result.stderr
    assert outputs(tmp_path)["pypi_files"].split(" ") == sorted(files)
    assert "3 distribution(s) of version 1.28.8rc3 verified" in result.stdout


def test_collect_dir_receives_the_verified_files_flat(tmp_path: Path) -> None:
    files = ["py312/hiveio_wax-1.28.8rc3-cp312-cp312-manylinux_2_28_x86_64.whl",
             "py314/hiveio_wax-1.28.8rc3-cp314-cp314-manylinux_2_28_x86_64.whl"]

    result = run(tmp_path, files, "--tag", "1.28.8-rc3", "--collect-dir", str(tmp_path / "upload"))

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (tmp_path / "upload").iterdir()) == [Path(f).name for f in files]
    assert outputs(tmp_path)["pypi_dist_dir"] == str(tmp_path / "upload")


def test_dist_dir_is_reported_when_nothing_is_collected(tmp_path: Path) -> None:
    result = run(tmp_path, ["pkg-1.0.0-py3-none-any.whl"], "--tag", "1.0.0")

    assert result.returncode == 0, result.stderr
    assert outputs(tmp_path)["pypi_dist_dir"] == str(tmp_path / "dist")


def test_a_distribution_of_another_version_fails(tmp_path: Path) -> None:
    result = run(tmp_path, ["hiveio_wax-1.28.8rc3-py3-none-any.whl", "hiveio_wax-1.28.8.dev20261103-py3-none-any.whl"],
                 "--tag", "1.28.8-rc3")

    assert result.returncode == 1
    assert "hiveio_wax-1.28.8.dev20261103-py3-none-any.whl: version 1.28.8.dev20261103 does not match tag" in result.stderr
    assert not (tmp_path / "out.env").exists()


def test_a_distribution_of_another_project_fails(tmp_path: Path) -> None:
    result = run(tmp_path, ["hiveio_wax-1.28.8rc3-py3-none-any.whl", "hiveio_api-1.28.8rc3-py3-none-any.whl"],
                 "--tag", "1.28.8-rc3", "--expected-name", "hiveio.wax")

    assert result.returncode == 1
    assert "hiveio_api-1.28.8rc3-py3-none-any.whl: project hiveio_api is not hiveio.wax" in result.stderr


def test_no_distributions_fails(tmp_path: Path) -> None:
    result = run(tmp_path, ["notes.txt"], "--tag", "1.0.0")

    assert result.returncode == 1
    assert "no *.whl or *.tar.gz files found" in result.stderr


@pytest.mark.parametrize("tag", ["1.28.8-foo", "release", "1.2.3-rc1-stable.260101", "beekeeper-v1.0.0"])
def test_tags_that_are_not_release_tags_are_rejected(tmp_path: Path, tag: str) -> None:
    result = run(tmp_path, ["pkg-1.0.0-py3-none-any.whl"], "--tag", tag)

    assert result.returncode == 1
    assert f"tag '{tag}' does not look like a release tag" in result.stderr
