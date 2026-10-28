"""ccc's pnpm catalogs and scripts/python/pnpm-catalog-manifest.py, the emsdk preload list.

The script is run as the emsdk Dockerfile runs it; PyYAML reads the same files
as the reference for what pnpm sees.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "python" / "pnpm-catalog-manifest.py"
CCC_WORKSPACE = ROOT / "pnpm-config" / "pnpm-workspace.yaml"
HIVE_REGISTRY_LINE = "@hiveio:registry=https://gitlab.syncad.com/api/v4/groups/136/-/packages/npm/"


def _run(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run([sys.executable, str(MANIFEST), *args], capture_output=True, text=True, check=False)


def _manifest_of(tmp_path: Path, text: str) -> "subprocess.CompletedProcess[str]":
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text(text)
    return _run(str(workspace))


def _ccc_catalogs() -> Dict[str, Dict[str, str]]:
    return {
        catalog: {package: str(version) for package, version in packages.items()}
        for catalog, packages in yaml.safe_load(CCC_WORKSPACE.read_text())["catalogs"].items()
    }


def _major_minor(version: str) -> List[int]:
    return [int(part) for part in version.split(".")[:2]]


def test_ccc_manifest_lists_every_catalog_package_once() -> None:
    proc = _run()

    assert proc.returncode == 0, proc.stderr
    expected = [f"{package}@{version}" for packages in _ccc_catalogs().values() for package, version in packages.items()]
    assert proc.stdout.splitlines() == expected


def test_each_package_is_in_one_ccc_catalog() -> None:
    owners: Dict[str, List[str]] = {}
    for catalog, packages in _ccc_catalogs().items():
        for package in packages:
            owners.setdefault(package, []).append(catalog)

    assert not {package: catalogs for package, catalogs in owners.items() if len(catalogs) > 1}


@pytest.mark.parametrize(
    "catalog,packages",
    [
        ("hiveio-toolset", [
            "@hiveio/wax", "@hiveio/wax-signers-external", "@hiveio/wax-signers-keychain",
            "@hiveio/wax-signers-metamask", "@hiveio/wax-signers-peakvault", "@hiveio/wax-signers-beekeeper",
            "@hiveio/beekeeper", "@hiveio/workerbee", "@hiveio/hb-auth", "@hiveio/healthchecker-component",
        ]),
        ("react-toolset", ["react", "react-dom", "@types/react", "@types/react-dom", "next", "eslint-config-next", "@tanstack/react-query"]),
        ("vue-toolset", ["vue", "nuxt", "pinia", "vuetify", "vue-tsc"]),
        ("tailwind-toolset", ["tailwindcss", "@tailwindcss/postcss", "@tailwindcss/vite", "@tailwindcss/cli"]),
    ],
)
def test_framework_catalogs_hold_their_packages(catalog: str, packages: List[str]) -> None:
    assert sorted(_ccc_catalogs()[catalog]) == sorted(packages)


def test_framework_catalogs_are_on_their_majors_and_consistent() -> None:
    catalogs = _ccc_catalogs()
    react, vue, tailwind = catalogs["react-toolset"], catalogs["vue-toolset"], catalogs["tailwind-toolset"]

    assert react["react"] == react["react-dom"] and _major_minor(react["react"])[0] == 19
    assert _major_minor(react["@types/react"])[0] == _major_minor(react["@types/react-dom"])[0] == 19
    assert react["next"] == react["eslint-config-next"] and _major_minor(react["next"])[0] == 16
    assert _major_minor(react["@tanstack/react-query"])[0] == 5
    assert _major_minor(vue["vue"]) == [3, 5] and _major_minor(vue["nuxt"])[0] == 4
    assert len(set(tailwind.values())) == 1 and _major_minor(tailwind["tailwindcss"])[0] == 4
    assert _major_minor(catalogs["typescript-toolset"]["typescript"]) == [5, 9]


def test_hive_catalog_pins_stable_builds_of_the_current_line() -> None:
    hive = _ccc_catalogs()["hiveio-toolset"]
    wax = hive["@hiveio/wax"]

    assert re.fullmatch(r"1\.28\.8-stable\.\d{12}", wax), wax
    assert {hive[p] for p in hive if p.startswith("@hiveio/wax-signers-")} == {wax}
    assert all(re.search(r"-stable\.\d{12}$", version) for version in hive.values()), hive


def test_hive_registry_is_configured_for_the_preload() -> None:
    assert HIVE_REGISTRY_LINE in (ROOT / "pnpm-config" / ".npmrc").read_text().splitlines()


def test_conflicting_versions_fail_naming_both(tmp_path: Path) -> None:
    proc = _manifest_of(tmp_path, "catalogs:\n  a:\n    react: 19.3.1\n  b:\n    react: 18.3.1\n")

    assert proc.returncode == 1
    assert "react: 19.3.1 in a, 18.3.1 in b" in proc.stderr
    assert proc.stdout == ""


def test_same_version_in_two_catalogs_is_listed_once(tmp_path: Path) -> None:
    proc = _manifest_of(tmp_path, "catalogs:\n  a:\n    react: 19.3.1\n  b:\n    react: 19.3.1\n    vue: 3.5.44\n")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["react@19.3.1", "vue@3.5.44"]


def test_reads_quoted_scalars_and_comments_as_yaml_does(tmp_path: Path) -> None:
    text = (
        "packages:\n  - './src/**/*'\n\n"
        "catalogs:\n"
        "  # comment\n"
        "  'quoted-catalog':\n"
        "    \"@scope/a\": '1.0.0'   # trailing\n"
        "    b: \"^2.0.0\"\n"
        "    c: 3.0.0-rc1-stable.261028191145\n"
        "# column-0 comment\n"
        "overrides:\n  b: 2.0.0\n"
    )
    proc = _manifest_of(tmp_path, text)

    assert proc.returncode == 0, proc.stderr
    expected = [f"{p}@{v}" for p, v in yaml.safe_load(text)["catalogs"]["quoted-catalog"].items()]
    assert proc.stdout.splitlines() == expected == ["@scope/a@1.0.0", "b@^2.0.0", "c@3.0.0-rc1-stable.261028191145"]


@pytest.mark.parametrize(
    "text,message",
    [
        ("catalogs: {a: {react: 1.0.0}}\n", "must be a block mapping"),
        ("catalogs:\n  a: {react: 1.0.0}\n", "catalog 'a' must be a block mapping"),
        ("catalogs:\n  a:\n    react: 1.0.0\n      vue: 3.5.0\n", "line 4"),
        ("catalogs:\n  a:\n    react: 1.0.0\n    react: 1.0.1\n", "listed twice"),
        ("catalogs:\n  a:\n    react: 1.0.0\ncatalogs:\n  b:\n    vue: 3.5.0\n", "more than once"),
    ],
)
def test_unreadable_catalogs_block_fails_naming_the_problem(tmp_path: Path, text: str, message: str) -> None:
    proc = _manifest_of(tmp_path, text)

    assert proc.returncode == 2
    assert message in proc.stderr


def test_emsdk_image_preloads_the_manifest() -> None:
    dockerfile = (ROOT / "Dockerfile.emscripten").read_text()

    assert "pnpm-catalog-manifest.py /tmp/ccc-catalog/pnpm-workspace.yaml" in dockerfile
    assert re.search(r"pnpm add --ignore-scripts \$\{packages\}", dockerfile)
    loader: Any = type("_Loader", (yaml.SafeLoader,), {})
    loader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))
    rules = yaml.load((ROOT / ".gitlab-ci.yml").read_text(), Loader=loader)["build_emsdk_image"]["rules"]
    for rule in rules:
        if "changes" in rule:
            assert {"pnpm-config/pnpm-workspace.yaml", "scripts/python/pnpm-catalog-manifest.py"} <= set(rule["changes"]["paths"])
