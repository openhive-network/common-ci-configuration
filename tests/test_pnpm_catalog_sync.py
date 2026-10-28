"""scripts/python/pnpm-catalog-sync.py: a monorepo's pnpm-workspace.yaml gets ccc's
catalogs merged in, keeping everything else, and --check reports drift."""

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SYNC = ROOT / "scripts" / "python" / "pnpm-catalog-sync.py"
CCC = """packages:
  - './packages/**/*'

catalogs:
  # Can be referenced through "catalog:hiveio-toolset"
  hiveio-toolset:
    "@hiveio/wax": 2.0.2
  react-toolset:
    react: 19.3.1
    next: 16.4.1
"""
CONSUMER = """packages:
  - 'apps/*'
  - 'packages/*'

catalogs:
  # the app's own pins
  local-toolset:
    '@local/thing': 1.2.3

# the signers pin an older beekeeper
overrides:
  '@hiveio/beekeeper': 'catalog:hiveio-toolset'
onlyBuiltDependencies:
  - esbuild
"""


def _run(tmp_path: Path, consumer: str, *args: str, ccc: str = CCC) -> "subprocess.CompletedProcess[str]":
    (tmp_path / "ccc.yaml").write_text(ccc)
    workspace = tmp_path / "pnpm-workspace.yaml"
    if not workspace.exists():
        workspace.write_text(consumer)
    return subprocess.run(
        [sys.executable, str(SYNC), "--source", str(tmp_path / "ccc.yaml"), *args, str(workspace)],
        capture_output=True, text=True, check=False,
    )


def _loaded(tmp_path: Path) -> Dict[str, Any]:
    loaded: Dict[str, Any] = yaml.safe_load((tmp_path / "pnpm-workspace.yaml").read_text())
    return loaded


def _outside_region(text: str) -> str:
    lines = text.splitlines(keepends=True)
    begin = next(i for i, line in enumerate(lines) if "# BEGIN common-ci-configuration catalogs" in line)
    end = next(i for i, line in enumerate(lines) if "# END common-ci-configuration catalogs" in line)
    return "".join(lines[:begin] + lines[end + 1:])


def test_merge_keeps_packages_overrides_and_local_catalogs(tmp_path: Path) -> None:
    proc = _run(tmp_path, CONSUMER)
    merged = (tmp_path / "pnpm-workspace.yaml").read_text()
    loaded = _loaded(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert _outside_region(merged) == CONSUMER
    assert loaded["packages"] == ["apps/*", "packages/*"]
    assert loaded["overrides"] == {"@hiveio/beekeeper": "catalog:hiveio-toolset"}
    assert loaded["onlyBuiltDependencies"] == ["esbuild"]
    assert loaded["catalogs"] == {
        "hiveio-toolset": {"@hiveio/wax": "2.0.2"},
        "react-toolset": {"react": "19.3.1", "next": "16.4.1"},
        "local-toolset": {"@local/thing": "1.2.3"},
    }


def test_merge_replaces_the_region_on_a_ccc_bump(tmp_path: Path) -> None:
    _run(tmp_path, CONSUMER)
    bumped = CCC.replace("react: 19.3.1", "react: 19.3.2") + "  vue-toolset:\n    vue: 3.5.44\n"

    proc = _run(tmp_path, CONSUMER, ccc=bumped)

    assert proc.returncode == 0, proc.stderr
    assert _outside_region((tmp_path / "pnpm-workspace.yaml").read_text()) == CONSUMER
    assert _loaded(tmp_path)["catalogs"] == {
        "hiveio-toolset": {"@hiveio/wax": "2.0.2"},
        "react-toolset": {"react": "19.3.2", "next": "16.4.1"},
        "vue-toolset": {"vue": "3.5.44"},
        "local-toolset": {"@local/thing": "1.2.3"},
    }


def test_merge_is_idempotent(tmp_path: Path) -> None:
    _run(tmp_path, CONSUMER)
    once = (tmp_path / "pnpm-workspace.yaml").read_text()

    proc = _run(tmp_path, CONSUMER)

    assert proc.returncode == 0, proc.stderr
    assert "up to date" in proc.stdout
    assert (tmp_path / "pnpm-workspace.yaml").read_text() == once


def test_merge_adds_catalogs_to_a_file_without_them(tmp_path: Path) -> None:
    consumer = "packages:\n  - 'apps/*'\noverrides:\n  foo: 1.0.0"

    proc = _run(tmp_path, consumer)

    assert proc.returncode == 0, proc.stderr
    assert _loaded(tmp_path) == {
        "packages": ["apps/*"],
        "overrides": {"foo": "1.0.0"},
        "catalogs": {
            "hiveio-toolset": {"@hiveio/wax": "2.0.2"},
            "react-toolset": {"react": "19.3.1", "next": "16.4.1"},
        },
    }


def test_merge_follows_the_consumer_indentation(tmp_path: Path) -> None:
    consumer = "catalogs:\n    local:\n        x: 1.0.0\n"

    proc = _run(tmp_path, consumer)

    assert proc.returncode == 0, proc.stderr
    assert "\n    react-toolset:\n        react: '19.3.1'\n" in (tmp_path / "pnpm-workspace.yaml").read_text()
    assert _loaded(tmp_path)["catalogs"]["local"] == {"x": "1.0.0"}


def test_merge_keeps_the_region_where_the_consumer_moved_it(tmp_path: Path) -> None:
    _run(tmp_path, CONSUMER)
    lines = (tmp_path / "pnpm-workspace.yaml").read_text().splitlines(keepends=True)
    begin = next(i for i, line in enumerate(lines) if "# BEGIN common-ci" in line)
    end = next(i for i, line in enumerate(lines) if "# END common-ci" in line)
    local = lines.index("  local-toolset:\n")
    moved = lines[:begin] + lines[end + 1:local + 2] + lines[begin:end + 1] + lines[local + 2:]
    (tmp_path / "pnpm-workspace.yaml").write_text("".join(moved))

    proc = _run(tmp_path, CONSUMER, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_passes_when_in_sync_and_writes_nothing(tmp_path: Path) -> None:
    _run(tmp_path, CONSUMER)
    merged = (tmp_path / "pnpm-workspace.yaml").read_text()

    proc = _run(tmp_path, CONSUMER, "--check")

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "pnpm-workspace.yaml").read_text() == merged


def test_check_fails_on_drift_with_a_diff(tmp_path: Path) -> None:
    _run(tmp_path, CONSUMER)
    merged = (tmp_path / "pnpm-workspace.yaml").read_text()

    proc = _run(tmp_path, CONSUMER, "--check", ccc=CCC.replace("next: 16.4.1", "next: 16.4.3"))

    assert proc.returncode == 1
    assert "-    next: '16.4.1'\n+    next: '16.4.3'\n" in proc.stdout
    assert "differs from ccc's catalogs" in proc.stderr
    assert (tmp_path / "pnpm-workspace.yaml").read_text() == merged


def test_check_fails_before_the_first_merge(tmp_path: Path) -> None:
    proc = _run(tmp_path, CONSUMER, "--check")

    assert proc.returncode == 1
    assert (tmp_path / "pnpm-workspace.yaml").read_text() == CONSUMER


def test_check_fails_on_a_hand_edited_region(tmp_path: Path) -> None:
    _run(tmp_path, CONSUMER)
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text(workspace.read_text().replace("react: '19.3.1'", "react: '18.3.1'"))

    proc = _run(tmp_path, CONSUMER, "--check")

    assert proc.returncode == 1
    assert "+    react: '19.3.1'" in proc.stdout


@pytest.mark.parametrize(
    "consumer,error",
    [
        ("catalogs:\n  react-toolset:\n    react: 18.3.1\n", "react-toolset have the names of ccc catalogs"),
        ("catalogs:\n  # BEGIN common-ci-configuration catalogs\n  a:\n    x: 1.0.0\n", "exactly one"),
        ("# END common-ci-configuration catalogs\ncatalogs:\n  a:\n    x: 1.0.0\n", "inside the `catalogs:` block"),
        ("catalogs: {}\n", "block mapping"),
    ],
)
def test_unmergeable_files_are_left_alone(tmp_path: Path, consumer: str, error: str) -> None:
    proc = _run(tmp_path, consumer)

    assert proc.returncode == 2
    assert error in proc.stderr
    assert (tmp_path / "pnpm-workspace.yaml").read_text() == consumer


def test_missing_workspace_file_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "ccc.yaml").write_text(CCC)

    proc = subprocess.run(
        [sys.executable, str(SYNC), "--source", str(tmp_path / "ccc.yaml"), str(tmp_path / "pnpm-workspace.yaml")],
        capture_output=True, text=True, check=False,
    )

    assert proc.returncode == 2
    assert not (tmp_path / "pnpm-workspace.yaml").exists()


def test_default_source_is_ccc_catalog(tmp_path: Path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(CONSUMER)

    proc = subprocess.run(
        [sys.executable, str(SYNC), "pnpm-workspace.yaml"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    catalogs = _loaded(tmp_path)["catalogs"]
    ccc = yaml.safe_load((ROOT / "pnpm-config" / "pnpm-workspace.yaml").read_text())["catalogs"]

    assert proc.returncode == 0, proc.stderr
    assert catalogs == {**{name: {k: str(v) for k, v in pins.items()} for name, pins in ccc.items()},
                        "local-toolset": {"@local/thing": "1.2.3"}}
