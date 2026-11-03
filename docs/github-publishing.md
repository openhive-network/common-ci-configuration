# Publishing release packages from GitHub Actions with GitLab-built artifacts

Hive projects build and test their npm and Python packages on GitLab. This page describes how the
**release** of such a package is published to npmjs.org or pypi.org from the project's GitHub mirror,
with provenance, without rebuilding it and without any registry token stored anywhere.

Tracking issue: hive/common-ci-configuration#70.

## Distribution model

1. A protected tag (e.g. `1.28.8`, `1.28.8-rc3`) is pushed on GitLab. The tag pipeline builds the
   package as before: `.npm_build_template` produces `*.tgz`, `.build_wheel_template` produces
   `*.whl`, both kept as job artifacts.
2. GitLab push mirroring copies the tag to `github.com/openhive-network/<project>`. The tag push
   starts the project's `publish-npm.yml` / `publish-pypi.yml` workflow.
3. The workflow polls the GitLab API until a pipeline for that tag **and for the same commit as the
   checked-out tag** has finished successfully, then downloads the package out of the job artifacts.
4. npm only: `package.json` inside the tarball is pointed at npmjs and at the GitHub repository
   (npm provenance requires `repository.url` to match the repository the workflow runs in). Wheels
   are uploaded unchanged.
5. The package is published through OIDC **trusted publishing**: the workflow's identity (repository,
   workflow file, environment) is what the registry trusts. npm generates SLSA provenance, PyPI
   generates PEP 740 attestations. Both name the GitHub repository and the tagged commit; the
   commit check in step 3 ties them to the GitLab build.

Only the release channel takes this path. The channel policy itself does not change:

| Source | npm dist-tag | PyPI (PEP 440) | Published by |
|---|---|---|---|
| protected tag | `latest` | release `X.Y.Z` / pre-release `X.Y.ZrcN` | **GitHub workflow** to npmjs.org / pypi.org |
| protected branch, no tag | `stable` | post-release `X.Y.Z.post<ts>` | GitLab jobs to the GitLab registries |
| other branches | `dev` | dev release `X.Y.Z.dev<ts>` | GitLab jobs to the GitLab registries |

Two consequences of PEP 440 are worth knowing: on npm an rc tag becomes `latest` and `npm install`
picks it, while on PyPI `1.28.8rc3` is a pre-release that `pip install` skips unless asked with `--pre`
or an explicit version; and PyPI has no mutable pointer like a dist-tag, the nearest thing to moving
`latest` away from a bad release is yanking it.

## What lives where

| Path | Role |
|---|---|
| `scripts/github-actions/gitlab-wait-for-artifact.sh` | Polls GitLab, checks the commit, downloads and extracts the artifacts |
| `scripts/github-actions/npm-rewrite-package.sh` | Rewrites `repository.url` and `publishConfig` in a tarball, checks its version against the tag |
| `scripts/github-actions/npm-publish-package.sh` | `npm publish` (or `npm stage publish`) of a tarball, skipping published versions |
| `scripts/github-actions/pypi-verify-wheels.sh` | Checks wheels and sdists carry the tag's PEP 440 version, collects them for upload |
| `github-actions/actions/<name>/action.yml` | Composite actions wrapping the scripts, one per script |
| `github-actions/workflows/publish-npm.yml` | Workflow template for an npm package |
| `github-actions/workflows/publish-pypi.yml` | Workflow template for a Python package |

The logic is in the scripts; the actions only map their inputs to script options and expose the
script's `--output-env` lines as step outputs. Every script prints its usage with `--help` and can be
run locally, e.g. against a finished pipeline:

```bash
scripts/github-actions/gitlab-wait-for-artifact.sh --project 452 --ref 1.28.4-rc1 --job build \
  --artifact 'dist/*.tgz' --output-dir /tmp/wb --wait-for job
scripts/github-actions/npm-rewrite-package.sh --tarball /tmp/wb/dist/hiveio-workerbee-1.28.4-rc1.tgz \
  --output-dir /tmp/wb/npmjs --repository-url https://github.com/openhive-network/workerbee --tag 1.28.4-rc1
scripts/github-actions/npm-publish-package.sh --tarball /tmp/wb/npmjs/hiveio-workerbee-1.28.4-rc1.tgz --dry-run
```

Projects use the scripts through their **common-ci-configuration submodule** (`npm-common-config`
in workerbee, `ts/npm-common-config` in wax, ...), so a project pins the version of the scripts the
way it pins everything else from this repository: by the submodule commit. GitHub resolves
`uses: ./<submodule path>/github-actions/actions/<name>` once the submodule is checked out.

## Adding the workflow to a project

1. **Submodule.** The project must have common-ci-configuration as a submodule at a commit that
   contains `github-actions/`. The submodule URL is relative (`../common-ci-configuration.git`), so on
   GitHub it resolves to the `openhive-network/common-ci-configuration` mirror.
2. **Workflow file.** Copy `github-actions/workflows/publish-npm.yml` or `publish-pypi.yml` to
   `.github/workflows/` in the project, fill in the `env:` block (GitLab project, build job, artifact
   path, tag pattern) and put the submodule path into every `uses: ./...` line. A `uses:` value cannot
   come from a variable, so this is the one place the path is repeated. Keep the file name: the
   trusted publisher is bound to it.
   - `GITLAB_ARTIFACT` is a glob (`*` also matches `/`) relative to the project directory, as in the GitLab job's
     `artifacts:paths`. Make it specific: wax's `build_wheel` artifacts also carry the `hiveio-api`
     wheel the job depends on, so `python/wax/dist/*.whl`, not `*.whl`. For npm it must match
     exactly one tarball.
   - A `parallel:matrix` job (wax `build_wheel: [3.12]`, `[3.14]`) is named by its base name; the
     artifacts of all its instances are collected.
3. **Tags on GitHub.** Check the release tags reach the mirror
   (`git ls-remote --tags https://github.com/openhive-network/<project>`) and protect them: a
   repository ruleset for `refs/tags/<pattern>` that restricts creation and deletion to the account
   the GitLab mirror pushes with, so nobody else can trigger a release.
4. **Environment.** Create the environment the template names (`npm-release` / `pypi-release`) in
   the repository settings, with a deployment tag policy limited to the release tag pattern. The
   trusted publisher below is bound to this environment, which means that even a workflow of the same
   file name on a branch cannot publish.
5. **Trusted publisher.** See the registry sections below.
6. **First run.** Trigger the workflow manually (`workflow_dispatch`) for an existing release tag with
   `dry-run` on. It waits for (or finds) the GitLab pipeline, downloads and rewrites the package and
   stops at `npm publish --dry-run` / before the PyPI upload. Then push the next rc tag.

## How the workflow behaves

- **Trigger.** `push` of a tag matching the pattern, or `workflow_dispatch` with a `tag` input for a
  re-run (a push run that timed out, or a GitLab job that had to be retried). The tag is checked out
  in both cases, so `HEAD` is the tagged commit.
- **Waiting.** The wait action polls every 60 s for up to 4 h by default (`timeout`, `poll-interval`),
  within the job's `timeout-minutes` (360, the GitHub-hosted maximum). By default it needs the whole
  GitLab pipeline to succeed, not just the build job, so a package whose tests failed is never
  published; `wait-for: job` relaxes that. A pipeline blocked on a manual job counts as finished.
  Pipelines for other commits of the same ref are ignored.
- **Failure modes.** The wait script's exit code says what happened: 2 the job or pipeline failed on
  GitLab (fix it there, re-run the workflow), 3 timed out (re-run once the pipeline is done), 4 the
  artifacts expired or do not contain the file (GitLab keeps job artifacts for `expire_in`, 1 week in
  the templates: retry the GitLab job, then re-run the workflow).
- **Idempotence.** A version that is already on the registry is skipped (`npm view` /
  `skip-existing`), so re-running a workflow for a published tag is a no-op.
- **No secrets.** GitLab is read anonymously (public projects; `token` input for private ones), the
  registries authenticate the OIDC token. The checkout uses `persist-credentials: false`.

## npm

### What is rewritten

The GitLab build job writes `publishConfig.registry` (the GitLab project registry) and leaves
`repository.url` at the GitLab project. `npm-rewrite-package.sh` sets:

| Field | Value |
|---|---|
| `repository.url` | `git+https://github.com/<owner>/<repo>.git`, the repository the workflow runs in |
| `repository.directory` | the package directory, for monorepo packages (`repository-directory` input) |
| `publishConfig.registry` | `https://registry.npmjs.org/` |
| `publishConfig.tag` | `latest` |

Nothing else changes, no lifecycle script runs (`prepack`, `prepublishOnly` are skipped with
`--ignore-scripts` at publish time too), and the script refuses a tarball whose version is not the
tag's. `npm publish` re-packs the unpacked directory, so the integrity hash differs from the GitLab
tarball's; the contents are the same. To compare: `npm pack @hiveio/<pkg>@<version>` from both
registries and `diff -r` the unpacked trees, as `docs/npmjs-staged-publishing.md` describes.

### Trusted publisher on npmjs.com

Requirements, from the npm documentation: npm CLI 11.5.1 or newer (Node 24 on the runner; the
template uses `actions/setup-node` with `node-version: 24` and **no** `registry-url`, because the
`NODE_AUTH_TOKEN` placeholder it writes to `.npmrc` makes npm skip OIDC), and the package must
already exist on npmjs.org. A brand-new package needs one publish with a token first.

On the package page, *Settings → Trusted publishing*: publisher GitHub Actions, organization
`openhive-network`, the repository name, workflow filename `publish-npm.yml`, environment
`npm-release`. New trusted publisher configurations may default to "stage only": either allow
`npm publish` there, or run the publish action with `mode: stage` so the version is staged and a
maintainer approves it with 2FA (`npm stage list`, `npm stage approve`), as the GitLab job does today.

Verify after the first publish: `npm view @hiveio/<pkg>@<version> dist.attestations`, or the
"Provenance" box on the npmjs.com version page, must name the GitHub repository, this workflow and the
tagged commit.

## PyPI

Nothing in a wheel is rewritten: PyPI trusts the workflow's OIDC identity, not the file's metadata.
`pypi-verify-wheels.sh` maps the tag to the version poetry-dynamic-versioning gives a tag build
(`1.28.8` → `1.28.8`, `1.28.8-rc3` → `1.28.8rc3`, `v1.2.0` → `1.2.0`, `beekeeper-v1.28.5rc1` →
`1.28.5rc1` with `tag-strip-prefix: beekeeper-v`) and refuses any wheel or sdist of another version or,
with `expected-name`, of another project. The verified files are copied into one flat directory,
which `pypa/gh-action-pypi-publish` uploads with `skip-existing` and attestations.

On pypi.org, *Manage project → Publishing*: owner `openhive-network`, repository name, workflow name
`publish-pypi.yml`, environment `pypi-release`. A project that does not exist yet can be registered
as a *pending* publisher from the account page; the first upload then creates it. Reusable
(`workflow_call`) workflows are not supported by PyPI's trusted publishing, which is why the shared
parts are composite actions and the workflow file itself lives in each project.

Verify after the first upload: the release files on pypi.org show "Provenance" with the repository
and workflow; `pip download hiveio-<pkg>==<version>` then `python -m pypi_attestations verify ...`
checks it offline.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No pipeline for ref '<tag>' at commit <sha>` until timeout | The GitLab pipeline ran for another commit than the tag on GitHub (tag moved or mirror lag), or the mirror has not pushed the tag yet. Compare `git ls-remote` of both sides. |
| exit 4, `artifacts ... expired` | Older than the GitLab retention. Retry the GitLab job, then re-run the workflow with `workflow_dispatch`. |
| exit 4, `nothing matching '<glob>'` | `GITLAB_ARTIFACT` does not match the job's `artifacts:paths`; the archive contents are printed. |
| `has version X, expected Y` | The tarball is a `stable`/`dev` build, not the tag build: the wait step found the wrong pipeline or job. |
| `npm ERR! ... 404` or `ENEEDAUTH` at publish | No trusted publisher matches: organization, repository, file name and environment must match exactly; `id-token: write` must be set; no `registry-url` / `NODE_AUTH_TOKEN`. |
| `Provenance repository URL ... does not match package repository URL` | The rewrite step did not run, or `repository-url` points elsewhere than the workflow's repository. |
| PyPI `invalid-publisher` | Publisher settings do not match the workflow (file name, environment), or the publish step runs from a reusable workflow. |
