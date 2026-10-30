"""ccc's pnpm catalogs and scripts/python/pnpm-catalog-manifest.py, the manifest the emsdk and node-test-tools images install to preload their pnpm store."""

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
NODE_DOCKERFILE = ROOT / "Dockerfile.node-test-tools"
BAKE = ROOT / "docker-bake.hcl"
PRELOAD_INSTALL = "pnpm install --ignore-scripts --config.cache-dir=/tmp/pnpm-catalog-cache"
OFFLINE_CHECK = "pnpm install --offline --frozen-lockfile --ignore-scripts"
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


def _change_lists(job_name: str) -> List[List[str]]:
    job = yaml.load(CI.read_text(), Loader=_Loader)[job_name]
    return [rule["changes"]["paths"] for rule in job["rules"] if isinstance(rule, dict) and "changes" in rule]


@pytest.mark.parametrize("job", ["build_emsdk_image", "build_node_test_tools_image"])
@pytest.mark.parametrize(
    "path", ["pnpm-config/pnpm-workspace.yaml", "pnpm-config/.npmrc", "scripts/python/pnpm-catalog-manifest.py"]
)
def test_preloading_images_are_rebuilt_when_their_store_inputs_change(job: str, path: str) -> None:
    lists = _change_lists(job)
    assert lists
    for paths in lists:
        assert path in paths


@pytest.mark.parametrize(
    "dockerfile,copy_npmrc",
    [
        (DOCKERFILE, "COPY --chown=emscripten pnpm-config/.npmrc /tmp/pnpm-catalog.npmrc"),
        (NODE_DOCKERFILE, "COPY pnpm-config/.npmrc /tmp/pnpm-catalog.npmrc"),
    ],
)
def test_preload_takes_the_hive_registry_from_npmrc(dockerfile: Path, copy_npmrc: str) -> None:
    """The @hiveio -stable builds exist only in the hive GitLab registry: without its line the preload fails."""
    assert HIVE_REGISTRY_LINE in (ROOT / "pnpm-config" / ".npmrc").read_text().splitlines()
    text = dockerfile.read_text()
    assert copy_npmrc in text
    assert 'grep "^@hiveio:registry=" /tmp/pnpm-catalog.npmrc > .npmrc' in text


@pytest.mark.parametrize("dockerfile", [DOCKERFILE, NODE_DOCKERFILE])
def test_preload_installs_the_catalog_manifest(dockerfile: Path) -> None:
    """Both images fill their store from the same catalogs file and the same manifest script."""
    text = dockerfile.read_text()
    assert re.search(r"^COPY .*pnpm-config/pnpm-workspace\.yaml /tmp/pnpm-catalog/$", text, re.MULTILINE)
    assert re.search(r"^COPY .*scripts/python/pnpm-catalog-manifest\.py /tmp/$", text, re.MULTILINE)
    assert "pnpm config get catalogs --json > /tmp/pnpm-catalogs.json" in text
    assert "python3 /tmp/pnpm-catalog-manifest.py < /tmp/pnpm-catalogs.json > package.json" in text
    assert PRELOAD_INSTALL in text


def _stage(text: str, name: str) -> str:
    """The instructions of Dockerfile stage `name`, up to the next FROM."""
    match = re.search(rf"^FROM \S+ AS {re.escape(name)}\n(.*?)(?=^FROM |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"no stage {name}"
    return match.group(1)


def test_node_test_tools_store_build_fails_when_a_catalog_package_is_missing() -> None:
    """After the preload, an offline install of its lockfile with node_modules removed
    needs every catalog package (and dependency) in the store, or the build fails."""
    store = _stage(NODE_DOCKERFILE.read_text(), "pnpm-catalog-store")
    install, check = store.index(PRELOAD_INSTALL), store.index(OFFLINE_CHECK)
    assert install < store.index("rm -rf node_modules") < check
    assert "chmod -R a+rwX /opt/pnpm-store" in store[check:]


@pytest.mark.parametrize("stage", ["node-test-tools", "node-test-tools-pg", "node-test-tools-browser"])
def test_node_test_tools_images_end_with_the_shared_store_layer(stage: str) -> None:
    """The store is the last layer of every image, copied with --link so all carry the
    same layer and a catalog bump re-downloads only it."""
    lines = [line for line in _stage(NODE_DOCKERFILE.read_text(), stage).splitlines() if line and not line.startswith("#")]
    assert lines[-1] == "COPY --link --from=pnpm-catalog-store /opt/pnpm-store /opt/pnpm-store"


def test_node_test_tools_pnpm_uses_the_preloaded_store() -> None:
    base = _stage(NODE_DOCKERFILE.read_text(), "node-test-tools-base")
    assert "ENV npm_config_store_dir=/opt/pnpm-store" in base


def test_node_test_tools_pg_runs_ccc_postgresql_major() -> None:
    pg = _stage(NODE_DOCKERFILE.read_text(), "node-test-tools-pg-base")
    assert '"postgresql-${PSQL_VERSION}"' in pg
    assert 'ENV PATH="/usr/lib/postgresql/${PSQL_VERSION}/bin:${PATH}"' in pg
    bake = BAKE.read_text()
    block = re.search(r'target "node-test-tools-pg" \{(.*?)\n\}', bake, re.DOTALL)
    assert block and "PSQL_VERSION = PSQL_VERSION" in block.group(1)
    assert re.search(r'group "node-test-tools-images" \{\s*targets = \["node-test-tools", "node-test-tools-pg",', bake)


def test_node_test_tools_browser_points_chrome_path_at_full_chromium() -> None:
    browser = _stage(NODE_DOCKERFILE.read_text(), "node-test-tools-browser")
    assert "playwright install --with-deps chromium &&" in browser
    assert '"lighthouse@${LIGHTHOUSE_VERSION}"' in browser
    assert 'ln -s "${chrome}" "${CHROME_PATH}"' in browser
    bake = BAKE.read_text()
    block = re.search(r'target "node-test-tools-browser" \{(.*?)\n\}', bake, re.DOTALL)
    assert block and "LIGHTHOUSE_VERSION = LIGHTHOUSE_VERSION" in block.group(1)
    assert 'inherits = ["node-test-tools"]' in block.group(1)
    assert re.search(r'group "node-test-tools-images" \{\s*targets = \[[^]]*"node-test-tools-browser"\]', bake)


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
