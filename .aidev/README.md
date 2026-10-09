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
| `py-compile` | parses every `scripts/python/*.py`. `lint_python_scripts` pins pylint 2.17.7, which crashes on python 3.14 (the CI image), so syntax is what can be checked today |
| `tests` | `pytest tests/`. The repository has no `tests/` yet; until it does, the step is a recorded skip |

| Slot | Steps |
|---|---|
| quick, full, canary | shellcheck, yamllint, py-compile, tests |
| baseline, coverage | tests |

`static` and `system` are unbound: AIDEV runs neither for a project. Not covered: the
`Dockerfile.*` images (CI builds them) and how the templates behave in the projects that
include them. A change to a template's shell is best covered by a test under `tests/`
that extracts and runs that shell with stubbed commands.

To run the checks by hand:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/w" -w /w \
    "$(grep -o 'registry[^"]*aidev-tests@sha256:[0-9a-f]*' .aidev/project.yaml)" \
    .aidev/run-checks.sh full shellcheck yamllint py-compile tests
```

## The test runtime image (`runtime/`)

`python:3.14-slim` (the CI's `PYTHON_IMAGE_TAG`) with `git` and `bash`, the `shellcheck`
binary of `koalaman/shellcheck:v0.11.0` (the CI's `SHELLCHECK_ALPINE_TAG`), and
`scripts/python/requirements.txt` plus yamllint and pytest. Every base is pinned by digest.

When `runtime/Dockerfile` or `scripts/python/requirements.txt` changes, rebuild and
re-pin **in the same commit**:

```bash
.aidev/runtime/build.sh --push   # registry digest if aidev-<input hash> exists, else build + push
# put the printed repo@sha256:<digest> into project.yaml environment.image
```
