# common-ci-configuration under AIDEV

AIDEV verifies changes to this repository through the slots in `project.yaml`,
integrates them into `aidev/integration`, and people merge that into `develop`
through merge requests (as in hive/hivemind and hive/haf). GitLab CI doesn't run for
AIDEV branches (`ai/*`, `session/*`, pushes to `aidev/integration`); see
`.gitlab-ci.yml` `workflow:`.

## Suites

`.aidev/run-checks.sh <suite> <step>...` runs the named steps and writes
`test-results/<suite>/junit.xml`, one test case per step, with the step's log tail as
the failure body. `tests` also writes one case per test (`tests.xml`).

| Step | What |
|---|---|
| `shellcheck` | ShellCheck on `scripts/**/*.sh`, expanded by `sh` as `.gitlab-ci.yml`'s `lint_bash_scripts` does (so `scripts/*/*.sh`), plus `.aidev/`'s own scripts |
| `yamllint` | `yamllint` on `templates/` with the repository's `.yamllint`, as `lint_ci_templates` runs it; warnings don't fail |
| `pylint` | pylint 4.1.2 on `scripts/python/*.py` with the repository's `.pylintrc`, as `lint_python_scripts` runs it, then `misc/pylint2junit.py` on its json2 output (`pylint.xml`, one case per module) |
| `tests` | `pytest tests/`; a recorded skip when the repository has no `tests/` |

| Slot | Steps |
|---|---|
| quick, full, canary | shellcheck, yamllint, pylint, tests |
| baseline, coverage | tests |

`static` and `system` are unbound: AIDEV runs neither for a project. Not covered: the
`Dockerfile.*` images (CI builds them) and how the templates behave in the projects that
include them. A change to a template's shell is best covered by a test under `tests/`
that extracts and runs that shell with stubbed commands.

To run the checks by hand:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/w" -w /w \
    "$(grep -o 'registry[^"]*aidev-tests@sha256:[0-9a-f]*' .aidev/project.yaml)" \
    .aidev/run-checks.sh full shellcheck yamllint pylint tests
```

## The test runtime image (`runtime/`)

`python:3.14-slim` (the CI's `PYTHON_IMAGE_TAG`) with `git` and `bash`, the `shellcheck`
binary of `koalaman/shellcheck:v0.11.0` (the CI's `SHELLCHECK_ALPINE_TAG`), and
`scripts/python/requirements.txt` plus pylint, yamllint and pytest. Every base is pinned by digest.

When `runtime/Dockerfile` or `scripts/python/requirements.txt` changes, rebuild and
re-pin **in the same commit**:

```bash
.aidev/runtime/build.sh --push   # registry digest if aidev-<input hash> exists, else build + push
# put the printed repo@sha256:<digest> into project.yaml environment.image
```
