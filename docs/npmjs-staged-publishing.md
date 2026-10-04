# Publishing `@hiveio` packages to npmjs with staged publishing

Jobs derived from `.registry_npmjs_org_deploy_package_template` (`templates/npm_projects.gitlab-ci.yml`)
do not publish to registry.npmjs.org directly. They **stage** the release with `npm stage publish`, and
it goes live only after a maintainer approves it with 2FA. A token leaked from CI can therefore stage
packages, but cannot publish them.

The GitLab package registry deploy (`.npm_deploy_package_template`, `npm_publish.sh` with
`CI_JOB_TOKEN`) is unchanged and still publishes directly.

## Requirements

- npm >= 11.15 in the job image. The emsdk image `5.0.2-4` (Node 24.21.0, npm 11.19) is the
  template default. The job fails with a clear message on an older npm.
- An npm **granular access token** with:
  - permission **Read and write (stage only)**;
  - package/scope access limited to the `@hiveio` packages (organization `hiveio`);
  - an expiry date (note it, so the variable can be rotated before it expires).
- The token stored in GitLab as the **group variable `NPM_STAGE_TOKEN`** on the `hive` group,
  with **Protected** and **Masked** set. Protected means it is only exposed to pipelines on
  protected branches and tags, which is where the npmjs job runs (manual job, protected tags only).

The template reads `NPM_STAGE_TOKEN` first and falls back to `NPM_PUBLISH_TOKEN`, so existing
derived jobs that set `NPM_PUBLISH_TOKEN: "$INTERNAL_HIDDEN_PUBLISH_TOKEN"` keep working: once
the group variable exists, it wins. Those job-level lines can be removed later.

## What the job does

1. Regenerates `package.json` name/version/dist-tag for registry.npmjs.org (`npm_generate_version.sh`).
2. Checks `npm --version` >= 11.15.
3. Packs every non-private package of the pnpm workspace in `SOURCE_DIR` with `pnpm pack`. This
   resolves `workspace:`/`catalog:` specifiers and runs `prepack`, as `pnpm publish` did. For a
   project without a workspace, that is just the root package. For wax, it also includes the
   `@hiveio/wax-signers-*` packages.
4. Unpacks each tarball into a clean directory under `npmjs-staged/unpacked/` and, from there:
   - skips it if `npm view <name>@<version>` shows it is already published;
   - skips it if `npm stage list <name> --json` shows that version is already staged;
   - otherwise runs `npm stage publish --access public --tag <dist-tag>`.
5. Keeps the tarballs it staged as job artifacts (`npmjs-staged/tarballs/*.tgz`, 1 month) and
   logs each one's sha512.

Re-running the job is safe: already staged or published versions are skipped.

## Approving a staged release (maintainer, needs 2FA)

```bash
npm login                                   # account with publish rights on @hiveio and 2FA enabled
npm stage list @hiveio/<package>            # find the stage id of the new version
npm stage view <stage-id>                   # check name, version, dist-tag

# Compare the staged tarball with the one CI published to the GitLab registry for the same build
npm stage download <stage-id>               # writes the staged .tgz to the current directory
npm pack @hiveio/<package>@<gitlab-version> \
  --registry https://gitlab.syncad.com/api/v4/projects/<project-id>/packages/npm/
mkdir staged gitlab
tar -xzf <staged>.tgz -C staged && tar -xzf <gitlab>.tgz -C gitlab
diff -r staged/package gitlab/package
```

The GitLab-registry package of a tag build has the same version as the npmjs one. The
`deploy_*` dev package job publishes it, and the build job's `*.tgz` artifact is the same file.
Expect differences only in `package.json` (`publishConfig.registry`, and `name` if the GitLab
scope differs). Any other difference means the staged content is not what was built and
tested, so reject it with `npm stage reject <stage-id> --otp <code>`. You can also compare
against `npmjs-staged/tarballs/*.tgz` from the CI job's artifacts and the sha512 in its log.

Then approve:

```bash
npm stage approve <stage-id> --otp <code>
npm view @hiveio/<package>@<version>        # now published under the requested dist-tag
```

Staged releases can also be reviewed and approved on npmjs.com (package page, staged versions).
The dist-tag is fixed when staging. To change it, reject the staged version and re-run the job.

## Rotating the token

Create a new stage-only granular token as above, update the `NPM_STAGE_TOKEN` group variable, then
revoke the old token on npmjs.com.
