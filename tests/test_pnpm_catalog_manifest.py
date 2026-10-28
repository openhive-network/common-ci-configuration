"""ccc's pnpm catalogs and scripts/python/pnpm-catalog-manifest.py, the package
list the emsdk image preloads into its pnpm store."""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "pnpm-config" / "pnpm-workspace.yaml"
MANIFEST = ROOT / "scripts" / "python" / "pnpm-catalog-manifest.py"
SIGNERS = ("external", "keychain", "metamask", "peakvault", "beekeeper")
# catalog -> package -> version line it must be on
VERSION_LINES = {
    "hiveio-toolset": {"@hiveio/wax": "2."},
    "react-toolset": {
        "react": "19.",
        "react-dom": "19.",
        "@types/react": "19.",
        "@types/react-dom": "19.",
        "next": "16.",
        "@tanstack/react-query": "5.",
    },
    "vue-toolset": {"vue": "3.5.", "nuxt": "4."},
    "tailwind-toolset": {"tailwindcss": "4."},
    "typescript-toolset": {"typescript": "5.9."},
}
REQUIRED_PACKAGES = {
    "hiveio-toolset": [
        "@hiveio/wax",
        *(f"@hiveio/wax-signers-{signer}" for signer in SIGNERS),
        "@hiveio/beekeeper",
        "@hiveio/workerbee",
        "@hiveio/hb-auth",
        "@hiveio/healthchecker-component",
    ],
    "react-toolset": [
        "react", "react-dom", "@types/react", "@types/react-dom", "next", "eslint-config-next", "@tanstack/react-query",
    ],
    "vue-toolset": ["vue", "nuxt", "pinia", "vuetify", "vue-tsc"],
    "tailwind-toolset": ["tailwindcss", "@tailwindcss/postcss", "@tailwindcss/vite", "@tailwindcss/cli"],
}


def _catalogs() -> Dict[str, Dict[str, Any]]:
    catalogs: Dict[str, Dict[str, Any]] = yaml.safe_load(CATALOG.read_text())["catalogs"]
    return catalogs


def _manifest(path: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(MANIFEST), str(path)], capture_output=True, text=True, check=False
    )


def test_every_package_is_in_one_catalog() -> None:
    owners: Dict[str, List[str]] = {}
    for catalog, packages in _catalogs().items():
        for package in packages:
            owners.setdefault(package, []).append(catalog)

    assert {package: names for package, names in owners.items() if len(names) > 1} == {}


@pytest.mark.parametrize("catalog", sorted(REQUIRED_PACKAGES))
def test_catalog_lists_its_packages(catalog: str) -> None:
    assert sorted(_catalogs()[catalog]) == sorted(REQUIRED_PACKAGES[catalog])


@pytest.mark.parametrize(
    "catalog,package,line",
    [(catalog, package, line) for catalog, lines in VERSION_LINES.items() for package, line in lines.items()],
)
def test_package_is_on_its_version_line(catalog: str, package: str, line: str) -> None:
    assert str(_catalogs()[catalog][package]).startswith(line)


def test_versions_that_ship_together_are_equal() -> None:
    catalogs = _catalogs()
    hive, react, tailwind = catalogs["hiveio-toolset"], catalogs["react-toolset"], catalogs["tailwind-toolset"]

    assert {hive[f"@hiveio/wax-signers-{signer}"] for signer in SIGNERS} == {hive["@hiveio/wax"]}
    assert react["eslint-config-next"] == react["next"]
    assert react["react-dom"] == react["react"]
    assert len(set(tailwind.values())) == 1


def test_beekeeper_is_a_csp_safe_stable_build() -> None:
    # 1.28.8 is the first beekeeper whose embind glue runs under a nonce CSP
    version = _catalogs()["hiveio-toolset"]["@hiveio/beekeeper"]

    assert re.fullmatch(r"1\.28\.(8|9|\d\d)-stable\.\d{12}", version), version


def test_manifest_lists_every_catalog_package_once() -> None:
    proc = _manifest(CATALOG)
    expected = [f"{package}@{version}" for packages in _catalogs().values() for package, version in packages.items()]

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == expected


def test_manifest_defaults_to_ccc_catalog() -> None:
    proc = subprocess.run([sys.executable, str(MANIFEST)], capture_output=True, text=True, check=False)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == _manifest(CATALOG).stdout


def test_manifest_lists_a_package_repeated_at_one_version_once(tmp_path: Path) -> None:
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text(
        "catalogs:\n  a:\n    x: 1.0.0\n    '@s/y': \"2.0.0\"  # comment\n  # comment\n  b:\n    x: 1.0.0\n"
    )

    proc = _manifest(workspace)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["x@1.0.0", "@s/y@2.0.0"]


def test_manifest_fails_on_conflicting_versions(tmp_path: Path) -> None:
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text("catalogs:\n  a:\n    x: 1.0.0\n  b:\n    x: 1.1.0\n")

    proc = _manifest(workspace)

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "x: a has 1.0.0, b has 1.1.0" in proc.stderr


@pytest.mark.parametrize(
    "text,error",
    [
        ("catalogs: {a: {x: 1.0.0}}\n", "block mapping"),
        ("catalogs:\n  a:\n    x: 1.0.0\n    x: 1.0.1\n", "listed twice"),
        ("catalogs:\n  a:\n    x: 1.0.0\n  a:\n    y: 1.0.0\n", "defined twice"),
        ("catalogs:\n  a:\n    x:\n      y: 1.0.0\n", "package: version"),
    ],
)
def test_manifest_rejects_what_it_cannot_read(tmp_path: Path, text: str, error: str) -> None:
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text(text)

    proc = _manifest(workspace)

    assert proc.returncode == 2
    assert error in proc.stderr


def test_emsdk_image_preloads_the_manifest() -> None:
    dockerfile = (ROOT / "Dockerfile.emscripten").read_text()
    copied = re.search(r"^COPY .*pnpm-config/pnpm-workspace\.yaml.* (\S+)$", dockerfile, re.MULTILINE)

    assert copied, "Dockerfile.emscripten does not copy ccc's catalog"
    assert "pnpm-catalog-manifest.py" in copied.group(0) and "pnpm_catalogs.py" in copied.group(0)
    assert re.search(
        rf'packages="\$\(python3 {re.escape(copied.group(1))}pnpm-catalog-manifest\.py[^)]*\)"(?:.|\n)*pnpm add [^\n]*\$\{{packages\}}',
        dockerfile,
    )


def test_emsdk_image_is_rebuilt_on_a_catalog_change() -> None:
    loader: Any = type("_Loader", (yaml.SafeLoader,), {})
    loader.add_constructor("!reference", lambda ldr, node: ldr.construct_sequence(node))
    job = yaml.load((ROOT / ".gitlab-ci.yml").read_text(), Loader=loader)["build_emsdk_image"]
    rule_paths = [rule["changes"]["paths"] for rule in job["rules"] if "changes" in rule]

    assert rule_paths
    for paths in rule_paths:
        assert {
            "pnpm-config/pnpm-workspace.yaml",
            "scripts/python/pnpm_catalogs.py",
            "scripts/python/pnpm-catalog-manifest.py",
        } <= set(paths)
