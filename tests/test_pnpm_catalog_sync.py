"""scripts/python/pnpm-catalog-sync.py: ccc's catalogs and overrides merged into a monorepo's pnpm-workspace.yaml.

The script is run as a consumer runs it, from its directory through the path
of the ccc submodule, with `--source` standing in for the submodule's catalog.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SYNC = ROOT / "scripts" / "python" / "pnpm-catalog-sync.py"

CCC = """packages:
  - './src/**/*'

catalogs:
  react-toolset:
    react: 19.3.1
    "@tanstack/react-query": 5.104.2
  hiveio-toolset:
    "@hiveio/wax": 1.28.8-stable.261028191145
"""

CCC_WITH_OVERRIDES = CCC + """
overrides:
  "@hiveio/beekeeper": "catalog:hiveio-toolset"
  lodash: 4.17.21
"""

CONSUMER = """# denser-style monorepo
packages:
  - apps/*
  - packages/*

catalogs:
  # repo-only pins
  app-local:
    "some-lib": ^1.2.0  # why it is pinned
    other: '2.0'

overrides:
  "@hiveio/beekeeper": "catalog:hiveio-toolset"
  "nanoid@<3.3.8": 3.3.8

onlyBuiltDependencies:
  - esbuild
"""


def _sync(tmp_path: Path, *args: str, ccc: str = CCC) -> "subprocess.CompletedProcess[str]":
    source = tmp_path / "ccc-pnpm-workspace.yaml"
    source.write_text(ccc)
    return subprocess.run(
        [sys.executable, str(SYNC), "--source", str(source), *args],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )


def _workspace(tmp_path: Path, text: str = CONSUMER) -> Path:
    path = tmp_path / "pnpm-workspace.yaml"
    path.write_text(text)
    return path


def _load(path: Path) -> Dict[str, Any]:
    loaded: Dict[str, Any] = yaml.safe_load(path.read_text())
    return loaded


def test_merge_adds_ccc_catalogs_and_keeps_the_rest(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    proc = _sync(tmp_path)

    assert proc.returncode == 0, proc.stderr
    merged = _load(workspace)
    assert merged["catalogs"] == {
        "react-toolset": {"react": "19.3.1", "@tanstack/react-query": "5.104.2"},
        "hiveio-toolset": {"@hiveio/wax": "1.28.8-stable.261028191145"},
        "app-local": {"some-lib": "^1.2.0", "other": "2.0"},
    }
    assert merged["packages"] == ["apps/*", "packages/*"]
    assert merged["overrides"] == {"@hiveio/beekeeper": "catalog:hiveio-toolset", "nanoid@<3.3.8": "3.3.8"}
    assert merged["onlyBuiltDependencies"] == ["esbuild"]


def test_merge_changes_no_line_outside_the_ccc_region(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    assert _sync(tmp_path).returncode == 0
    lines = workspace.read_text().splitlines(keepends=True)
    begin = next(i for i, line in enumerate(lines) if "# BEGIN common-ci-configuration catalogs" in line)
    end = next(i for i, line in enumerate(lines) if line.strip() == "# END common-ci-configuration catalogs")

    assert "".join(lines[:begin] + lines[end + 1:]) == CONSUMER


def test_check_passes_when_in_sync_and_merge_is_idempotent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert _sync(tmp_path).returncode == 0
    merged = workspace.read_text()

    check = _sync(tmp_path, "--check")
    again = _sync(tmp_path)

    assert check.returncode == 0, check.stdout + check.stderr
    assert "up to date" in check.stdout
    assert again.returncode == 0
    assert workspace.read_text() == merged


def test_check_fails_on_drift_and_writes_nothing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert _sync(tmp_path).returncode == 0
    before = workspace.read_text()
    bumped = CCC.replace("react: 19.3.1", "react: 19.3.2")

    check = _sync(tmp_path, "--check", ccc=bumped)

    assert check.returncode == 1
    assert '-    "react": "19.3.1"' in check.stdout
    assert '+    "react": "19.3.2"' in check.stdout
    assert "refresh pnpm-lock.yaml" in check.stderr
    assert workspace.read_text() == before

    assert _sync(tmp_path, ccc=bumped).returncode == 0
    assert _load(workspace)["catalogs"]["react-toolset"]["react"] == "19.3.2"
    assert _load(workspace)["catalogs"]["app-local"] == {"some-lib": "^1.2.0", "other": "2.0"}
    assert _sync(tmp_path, "--check", ccc=bumped).returncode == 0


def test_check_fails_before_the_first_merge(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    check = _sync(tmp_path, "--check")

    assert check.returncode == 1
    assert workspace.read_text() == CONSUMER


def test_catalog_removed_from_ccc_is_removed_from_the_region(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert _sync(tmp_path).returncode == 0

    assert _sync(tmp_path, ccc=CCC.split("  hiveio-toolset:")[0]).returncode == 0

    assert sorted(_load(workspace)["catalogs"]) == ["app-local", "react-toolset"]


def test_workspace_without_catalogs_gets_a_catalogs_block(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "packages:\n  - apps/*\noverrides:\n  a: 1.0.0")

    assert _sync(tmp_path).returncode == 0

    merged = _load(workspace)
    assert sorted(merged["catalogs"]) == ["hiveio-toolset", "react-toolset"]
    assert merged["packages"] == ["apps/*"] and merged["overrides"] == {"a": "1.0.0"}
    assert _sync(tmp_path, "--check").returncode == 0


def test_region_follows_the_consumers_indentation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "catalogs:\n    local:\n        a: 1.0.0\n")

    assert _sync(tmp_path).returncode == 0

    assert '    "react-toolset":\n        "react": "19.3.1"\n' in workspace.read_text()
    assert sorted(_load(workspace)["catalogs"]) == ["hiveio-toolset", "local", "react-toolset"]


CONSUMER_OVERRIDES = """packages:
  - apps/*

overrides:
  # own pins
  "@hiveio/beekeeper": 1.28.8-stable.261010174216
  "nanoid@<3.3.8": 3.3.8

onlyBuiltDependencies:
  - esbuild
"""


def test_merge_carries_ccc_overrides_over_the_consumers_own(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, CONSUMER_OVERRIDES)

    proc = _sync(tmp_path, ccc=CCC_WITH_OVERRIDES)

    assert proc.returncode == 0, proc.stderr
    merged = _load(workspace)
    assert merged["overrides"] == {
        "@hiveio/beekeeper": "catalog:hiveio-toolset", "lodash": "4.17.21", "nanoid@<3.3.8": "3.3.8",
    }
    assert merged["packages"] == ["apps/*"] and merged["onlyBuiltDependencies"] == ["esbuild"]
    text = workspace.read_text()
    assert "1.28.8-stable.261010174216" not in text
    assert "  # own pins\n" in text and '  "nanoid@<3.3.8": 3.3.8\n' in text
    assert _sync(tmp_path, "--check", ccc=CCC_WITH_OVERRIDES).returncode == 0
    assert _sync(tmp_path, ccc=CCC_WITH_OVERRIDES).returncode == 0
    assert workspace.read_text() == text


def test_override_dropped_by_ccc_is_removed_and_local_ones_stay(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, CONSUMER_OVERRIDES)
    assert _sync(tmp_path, ccc=CCC_WITH_OVERRIDES).returncode == 0

    assert _sync(tmp_path, ccc=CCC_WITH_OVERRIDES.replace("  lodash: 4.17.21\n", "")).returncode == 0
    assert _load(workspace)["overrides"] == {"@hiveio/beekeeper": "catalog:hiveio-toolset", "nanoid@<3.3.8": "3.3.8"}

    assert _sync(tmp_path).returncode == 0
    assert _load(workspace)["overrides"] == {"nanoid@<3.3.8": "3.3.8"}
    assert "common-ci-configuration overrides" not in workspace.read_text()


def test_workspace_without_overrides_gets_ccc_overrides(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "packages:\n  - apps/*\n")

    assert _sync(tmp_path, ccc=CCC_WITH_OVERRIDES).returncode == 0

    merged = _load(workspace)
    assert merged["overrides"] == {"@hiveio/beekeeper": "catalog:hiveio-toolset", "lodash": "4.17.21"}
    assert sorted(merged["catalogs"]) == ["hiveio-toolset", "react-toolset"]
    assert _sync(tmp_path, "--check", ccc=CCC_WITH_OVERRIDES).returncode == 0


@pytest.mark.parametrize(
    "text,message",
    [
        ("catalogs:\n  react-toolset:\n    react: 18.3.1\n", "local catalogs react-toolset are named like ccc catalogs"),
        ("# BEGIN common-ci-configuration catalogs\ncatalogs:\n  a:\n    b: 1.0.0\n", "inside the block"),
        ("catalogs:\n  # BEGIN common-ci-configuration catalogs\n  a:\n    b: 1.0.0\n", "followed by one"),
        ("catalogs: {a: {b: 1.0.0}}\n", "must be a block mapping"),
        ("overrides:\n  a:\n    b: 1.0.0\n", "expected `selector: version`"),
        ("overrides:\n  # BEGIN common-ci-configuration overrides\n  a: 1.0.0\n", "followed by one"),
    ],
)
def test_unmergeable_workspace_fails_and_is_left_alone(tmp_path: Path, text: str, message: str) -> None:
    workspace = _workspace(tmp_path, text)

    proc = _sync(tmp_path)

    assert proc.returncode == 2
    assert message in proc.stderr
    assert workspace.read_text() == text


def test_default_source_is_the_scripts_own_ccc_catalog(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    proc = subprocess.run([sys.executable, str(SYNC)], cwd=tmp_path, capture_output=True, text=True, check=False)

    assert proc.returncode == 0, proc.stderr
    ccc = yaml.safe_load((ROOT / "pnpm-config" / "pnpm-workspace.yaml").read_text())["catalogs"]
    merged = _load(workspace)["catalogs"]
    assert {name: merged[name] for name in ccc} == {
        name: {package: str(version) for package, version in packages.items()} for name, packages in ccc.items()
    }
    ccc_overrides = yaml.safe_load((ROOT / "pnpm-config" / "pnpm-workspace.yaml").read_text())["overrides"]
    assert _load(workspace)["overrides"] == {**ccc_overrides, "nanoid@<3.3.8": "3.3.8"}
