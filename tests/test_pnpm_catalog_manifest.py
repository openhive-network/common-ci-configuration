"""scripts/python/pnpm-catalog-manifest.py: the manifest the emsdk image installs to preload its pnpm store."""

import json
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


@pytest.mark.parametrize("path", ["pnpm-config/pnpm-workspace.yaml", "scripts/python/pnpm-catalog-manifest.py"])
def test_emsdk_is_rebuilt_when_its_store_inputs_change(path: str) -> None:
    lists = _emsdk_change_lists()
    assert lists
    for paths in lists:
        assert path in paths
