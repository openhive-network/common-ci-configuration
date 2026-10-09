# The image AIDEV runs this project's checks in (`environment.image` in
# .aidev/project.yaml). It carries the tools .gitlab-ci.yml's validation jobs use:
#   shellcheck v0.11.0 (lint_bash_scripts: koalaman/shellcheck-alpine:v0.11.0)
#   python 3.14 + yamllint (lint_ci_templates) + pylint 4.1.2 and
#   scripts/python/requirements.txt (lint_python_scripts)
#   + pytest for tests/, git and bash.
# Its inputs are this file and scripts/python/requirements.txt (the only file in the
# build context). Build and pin it with .aidev/runtime/build.sh; see .aidev/README.md.
FROM koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d AS shellcheck

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git bash \
    && rm -rf /var/lib/apt/lists/*

COPY --from=shellcheck /bin/shellcheck /usr/local/bin/shellcheck

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt pylint==4.1.2 yamllint==1.37.1 pytest==8.4.2 \
    && rm /tmp/requirements.txt

ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT []
CMD ["bash"]
