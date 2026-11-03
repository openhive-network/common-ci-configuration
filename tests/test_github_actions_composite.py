"""github-actions/: the composite actions stay thin wrappers of the scripts, and the templates use them.

Each action must call a script that exists, pass every one of its inputs to it through an
environment variable, and hand the script `--output-env "${GITHUB_OUTPUT}"` so its outputs are the
script's. Every option an action passes must be one the script's usage documents. The workflow
templates must only use actions that exist under github-actions/actions.
"""

import re
from pathlib import Path
from typing import Dict, List

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTIONS = sorted((ROOT / "github-actions" / "actions").glob("*/action.yml"))
WORKFLOWS = sorted((ROOT / "github-actions" / "workflows").glob("*.yml"))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _run_step(action: dict) -> dict:
    steps = action["runs"]["steps"]
    assert len(steps) == 1, "a composite action here is one script call"
    return steps[0]


def _script_of(path: Path) -> Path:
    match = re.search(r'"\$\{GITHUB_ACTION_PATH\}/((?:\.\./)+)(scripts/github-actions/[\w-]+\.sh)"', _run_step(_load(path))["run"])
    assert match, f"{path}: run must call a script under scripts/github-actions through GITHUB_ACTION_PATH"
    assert match.group(1) == "../../../", f"{path}: the action sits three levels below the repository root"
    return ROOT / match.group(2)


def _documented_options(script: Path) -> List[str]:
    return sorted(set(re.findall(r"^#\s+(--[a-z-]+)", script.read_text(), flags=re.MULTILINE)))


@pytest.mark.parametrize("path", ACTIONS, ids=[p.parent.name for p in ACTIONS])
def test_action_calls_an_existing_script_and_only_documented_options(path: Path) -> None:
    script = _script_of(path)
    assert script.is_file(), f"{path} calls {script.relative_to(ROOT)}, which does not exist"
    passed = re.findall(r"(--[a-z-]+) ", _run_step(_load(path))["run"])
    unknown = sorted(set(passed) - set(_documented_options(script)))
    assert unknown == [], f"{path} passes options {unknown} that {script.name} does not document"
    assert "--output-env \"${GITHUB_OUTPUT}\"" in _run_step(_load(path))["run"]


@pytest.mark.parametrize("path", ACTIONS, ids=[p.parent.name for p in ACTIONS])
def test_every_input_reaches_the_script_through_the_environment(path: Path) -> None:
    action = _load(path)
    step = _run_step(action)
    assert step["shell"] == "bash"
    env: Dict[str, str] = step["env"]
    for name in action["inputs"]:
        variable = "INPUT_" + name.upper().replace("-", "_")
        assert env.get(variable) == f"${{{{ inputs.{name} }}}}", f"{path}: input {name} is not exported as {variable}"
        assert f'"${{{variable}}}"' in step["run"], f"{path}: {variable} is not passed to the script"
    assert "${{" not in step["run"], f"{path}: expressions belong in env, not in run (injection)"


@pytest.mark.parametrize("path", ACTIONS, ids=[p.parent.name for p in ACTIONS])
def test_outputs_come_from_the_script_step(path: Path) -> None:
    action = _load(path)
    step_id = _run_step(action)["id"]
    for name, output in action["outputs"].items():
        assert re.fullmatch(rf"\$\{{\{{ steps\.{step_id}\.outputs\.[a-z_]+ \}}\}}", output["value"]), f"{path}: output {name}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=[p.name for p in WORKFLOWS])
def test_workflow_template_uses_existing_local_actions_and_pinned_external_ones(path: Path) -> None:
    workflow = _load(path)
    assert set(workflow.get("on") or workflow[True]) == {"push", "workflow_dispatch"}  # PyYAML reads `on` as True
    for job in workflow["jobs"].values():
        assert job["permissions"]["id-token"] == "write"
        for step in job["steps"]:
            uses = step.get("uses")
            if not uses:
                continue
            if uses.startswith("./"):
                name = re.sub(r"^\./.*?/github-actions/actions/", "", uses)
                assert (ROOT / "github-actions" / "actions" / name / "action.yml").is_file(), f"{path}: {uses}"
            else:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), f"{path}: {uses} must be pinned to a commit SHA"
