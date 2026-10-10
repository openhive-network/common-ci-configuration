"""Jobs that gate a pipeline must not install packages at runtime.

A `.pre`/`detect` job that runs `apk add` depends on a package mirror being
reachable; one DNS blip fails it and skips every downstream job. Such jobs must
use an image that already has their tools.
"""

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
GATING_STAGES = {".pre", "detect"}
PACKAGE_INSTALL = re.compile(r"\b(apk\s+add|apt-get\s+install|apt\s+install|yum\s+install|dnf\s+install)\b")
SCRIPT_KEYS = ("before_script", "script")


class _Reference(List[Any]):
    """A GitLab `!reference [job, key, ...]` path."""


# A SafeLoader subclass, so registering !reference leaves yaml.SafeLoader untouched;
# built with type() because PyYAML ships no type stubs to subclass from.
_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor(
    "!reference", lambda loader, node: _Reference(loader.construct_sequence(node))
)


def _load_jobs(paths: List[Path]) -> Dict[str, Any]:
    jobs: Dict[str, Any] = {}
    for path in paths:
        jobs.update(yaml.load(path.read_text(), Loader=_Loader) or {})
    return jobs


def _resolve(jobs: Dict[str, Any], name: str, key: str) -> Any:
    job = jobs.get(name)
    if not isinstance(job, dict):
        return None
    if key in job:
        return job[key]
    parents = job.get("extends", [])
    for parent in reversed([parents] if isinstance(parents, str) else parents):
        value = _resolve(jobs, parent, key)
        if value is not None:
            return value
    return None


def _commands(jobs: Dict[str, Any], value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, _Reference):
        target: Any = jobs
        for part in value:
            target = target.get(part) if isinstance(target, dict) else None
        return _commands(jobs, target)
    if isinstance(value, list):
        return [cmd for item in value for cmd in _commands(jobs, item)]
    return []


def _gating_install_violations(jobs: Dict[str, Any]) -> Dict[str, List[str]]:
    """Map each gating job to its package-install commands (jobs with none omitted)."""
    violations: Dict[str, List[str]] = {}
    for name in jobs:
        if _resolve(jobs, name, "stage") not in GATING_STAGES:
            continue
        found = [
            cmd
            for key in SCRIPT_KEYS
            for cmd in _commands(jobs, _resolve(jobs, name, key))
            if PACKAGE_INSTALL.search(cmd)
        ]
        if found:
            violations[name] = found
    return violations


def _gating_jobs(jobs: Dict[str, Any]) -> List[str]:
    return [name for name in jobs if _resolve(jobs, name, "stage") in GATING_STAGES]


def test_templates_gating_jobs_install_no_packages() -> None:
    jobs = _load_jobs(sorted(TEMPLATES.glob("*.yml")))

    assert {".detect_source_changes", ".haf_app_detect_changes", ".find_haf_image"} <= set(
        _gating_jobs(jobs)
    )
    assert _gating_install_violations(jobs) == {}, (
        "gating jobs must use an image that already has their tools"
    )


@pytest.mark.parametrize(
    "job",
    [
        {"stage": ".pre", "before_script": ["apk add --no-cache git"]},
        {"stage": "detect", "script": ["set -e\napt-get install -y git\n"]},
        {"extends": ".base", "script": [_Reference([".setup", "script"])]},
    ],
)
def test_flags_package_install_in_gating_job(job: Dict[str, Any]) -> None:
    jobs = {
        ".base": {"stage": ".pre"},
        ".setup": {"script": ["apk add bash"]},
        "detect": job,
    }

    assert list(_gating_install_violations(jobs)) == ["detect"]


def test_ignores_package_install_outside_gating_stages() -> None:
    jobs = {"lint": {"stage": "build", "before_script": ["apk add --no-cache git"]}}

    assert _gating_install_violations(jobs) == {}
