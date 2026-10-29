"""ccc's pnpm catalogs and scripts/python/pnpm-catalog-manifest.py, the manifest the emsdk image installs to preload its pnpm store."""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "python" / "pnpm-catalog-manifest.py"
PNPM_CATALOG = ROOT / "pnpm-config" / "pnpm-workspace.yaml"
CI = ROOT / ".gitlab-ci.yml"
DOCKERFILE = ROOT / "Dockerfile.emscripten"
HIVE_REGISTRY_LINE = "@hiveio:registry=https://gitlab.syncad.com/api/v4/groups/136/-/packages/npm/"

_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _run(catalogs: Dict[str, Any]) -> Tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], input=json.dumps(catalogs), capture_output=True, text=True, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def _catalogs() -> Dict[str, Any]:
    """The catalogs as `pnpm config get catalogs --json` reports them: the named ones plus `default`."""
    workspace = yaml.safe_load(PNPM_CATALOG.read_text())
    catalogs = dict(workspace.get("catalogs") or {})
    if workspace.get("catalog"):
        catalogs["default"] = workspace["catalog"]
    return catalogs


def test_every_catalog_entry_is_a_dependency() -> None:
    catalogs = _catalogs()
    code, out, err = _run(catalogs)
    assert code == 0, err
    dependencies = json.loads(out)["dependencies"]
    for catalog, packages in catalogs.items():
        for package, version in packages.items():
            assert package in dependencies, f"{catalog} {package} is missing from the manifest"
            chosen = dependencies[package][len("catalog:"):]
            assert str(catalogs[chosen][package]) == str(version), f"{package}: {dependencies[package]}"
    assert len(dependencies) == len({p for packages in catalogs.values() for p in packages})


def test_package_in_two_catalogs_at_one_version_is_written_once() -> None:
    code, out, err = _run({"a": {"x": "1.0.0"}, "b": {"x": "1.0.0", "y": "2.0.0"}})
    assert code == 0, err
    assert json.loads(out)["dependencies"] == {"x": "catalog:a", "y": "catalog:b"}


def test_package_in_two_catalogs_at_different_versions_fails() -> None:
    code, out, err = _run({"a": {"x": "1.0.0"}, "b": {"x": "2.0.0"}})
    assert code == 1
    assert not out
    assert "x is 1.0.0 in catalog a but 2.0.0 in catalog b" in err


def test_no_catalog_entries_fails() -> None:
    code, out, err = _run({})
    assert code == 1
    assert not out
    assert "no catalog entries" in err


def _emsdk_change_lists() -> List[List[str]]:
    job = yaml.load(CI.read_text(), Loader=_Loader)["build_emsdk_image"]
    return [rule["changes"]["paths"] for rule in job["rules"] if isinstance(rule, dict) and "changes" in rule]


@pytest.mark.parametrize(
    "path", ["pnpm-config/pnpm-workspace.yaml", "pnpm-config/.npmrc", "scripts/python/pnpm-catalog-manifest.py"]
)
def test_emsdk_is_rebuilt_when_its_store_inputs_change(path: str) -> None:
    lists = _emsdk_change_lists()
    assert lists
    for paths in lists:
        assert path in paths


def test_preload_takes_the_hive_registry_from_npmrc() -> None:
    """The @hiveio -stable builds exist only in the hive GitLab registry: without its line the preload fails."""
    assert HIVE_REGISTRY_LINE in (ROOT / "pnpm-config" / ".npmrc").read_text().splitlines()
    dockerfile = DOCKERFILE.read_text()
    assert "COPY --chown=emscripten pnpm-config/.npmrc /tmp/pnpm-catalog.npmrc" in dockerfile
    assert 'grep "^@hiveio:registry=" /tmp/pnpm-catalog.npmrc > .npmrc' in dockerfile


def _str_catalogs() -> Dict[str, Dict[str, str]]:
    return {
        catalog: {package: str(version) for package, version in packages.items()}
        for catalog, packages in yaml.safe_load(PNPM_CATALOG.read_text())["catalogs"].items()
    }


def _major_minor(version: str) -> List[int]:
    return [int(part) for part in version.split(".")[:2]]


def test_each_package_is_in_one_ccc_catalog() -> None:
    owners: Dict[str, List[str]] = {}
    for catalog, packages in _str_catalogs().items():
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
    assert sorted(_str_catalogs()[catalog]) == sorted(packages)


def test_framework_catalogs_are_on_their_majors_and_consistent() -> None:
    catalogs = _str_catalogs()
    react, vue, tailwind = catalogs["react-toolset"], catalogs["vue-toolset"], catalogs["tailwind-toolset"]

    assert react["react"] == react["react-dom"] and _major_minor(react["react"])[0] == 19
    assert _major_minor(react["@types/react"])[0] == _major_minor(react["@types/react-dom"])[0] == 19
    assert react["next"] == react["eslint-config-next"] and _major_minor(react["next"])[0] == 16
    assert _major_minor(react["@tanstack/react-query"])[0] == 5
    assert _major_minor(vue["vue"]) == [3, 5] and _major_minor(vue["nuxt"])[0] == 4
    assert len(set(tailwind.values())) == 1 and _major_minor(tailwind["tailwindcss"])[0] == 4
    assert _major_minor(catalogs["typescript-toolset"]["typescript"]) == [6, 0]
    assert _major_minor(catalogs["typedoc-toolset"]["typedoc"]) == [0, 28]


def test_hive_catalog_pins_stable_builds_of_the_current_line() -> None:
    hive = _str_catalogs()["hiveio-toolset"]
    wax = hive["@hiveio/wax"]

    assert re.fullmatch(r"1\.28\.8-stable\.\d{12}", wax), wax
    assert {hive[p] for p in hive if p.startswith("@hiveio/wax-signers-")} == {wax}
    assert all(re.search(r"-stable\.\d{12}$", version) for version in hive.values()), hive


def test_hive_catalog_beekeeper_is_the_large_buffer_fixed_line_and_overrides_the_signers_pin() -> None:
    """The signers depend on exactly a 1.28.8 beekeeper; without the override consumers get both builds."""
    workspace = yaml.safe_load(PNPM_CATALOG.read_text())

    assert re.fullmatch(r"1\.28\.9-rc0-stable\.\d{12}", str(workspace["catalogs"]["hiveio-toolset"]["@hiveio/beekeeper"]))
    assert workspace["overrides"] == {"@hiveio/beekeeper": "catalog:hiveio-toolset"}
