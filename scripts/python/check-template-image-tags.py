#!/usr/bin/env python3
"""Fail when a template pins an image tag that is not in the registry yet.

Templates pin tags of images this repo builds (EMSCRIPTEN_IMAGE_TAG,
ALPINE_IMAGE_TAG). A new tag reaches a template only in a second MR, merged once
the image MR's develop pipeline has published it (CLAUDE.md, "Moving Templates
to a New Image Tag"). This check is what makes that MR's pipeline fail until
then: it asks the registry for the manifest of every pinned image.

Exit status: 0 when every pinned tag exists, 1 when one is missing, 2 when the
registry could not be asked.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
# Template variable pinning a tag of an image this repo builds -> template defining it
PINNED_TAG_VARIABLES = {
    "EMSCRIPTEN_IMAGE_TAG": "templates/npm_projects.gitlab-ci.yml",
    "ALPINE_IMAGE_TAG": "templates/base.gitlab-ci.yml",
}
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
TIMEOUT = 30


class _Loader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """SafeLoader that accepts GitLab's !reference tags."""


_Loader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _expand(value: str, variables: Dict[str, str]) -> str:
    """`value` with $NAME / ${NAME} of `variables` replaced, recursively."""
    for _ in range(10):
        expanded = re.sub(
            r"\$\{(\w+)\}|\$(\w+)",
            lambda m: str(variables.get(m.group(1) or m.group(2), m.group(0))),
            value,
        )
        if expanded == value:
            break
        value = expanded
    return value


def image_references(root: Path = ROOT) -> Dict[str, str]:
    """Pinned tag variable -> the image reference its template builds from it."""
    references = {}
    for tag_variable, template in PINNED_TAG_VARIABLES.items():
        variables = yaml.load((root / template).read_text(), Loader=_Loader)["variables"]
        using = re.compile(rf"\$\{{{tag_variable}\}}|\${tag_variable}\b")
        images = [name for name, value in variables.items() if using.search(str(value))]
        if len(images) != 1:
            raise ValueError(f"{template}: expected one variable using {tag_variable}, found {images}")
        references[tag_variable] = _expand(str(variables[images[0]]), variables)
    return references


def split_reference(reference: str) -> Tuple[str, str, str]:
    """registry/repository:tag -> (registry, repository, tag)."""
    registry, _, path = reference.partition("/")
    repository, _, tag = path.rpartition(":")
    if not registry or not repository or not tag or "/" in tag:
        raise ValueError(f"not a registry/repository:tag reference: {reference}")
    return registry, repository, tag


def _bearer_token(challenge: str) -> str:
    """A pull token for a `WWW-Authenticate: Bearer realm=...,service=...,scope=...`
    challenge; with the CI job token when the job has one, anonymous otherwise."""
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    query = urllib.parse.urlencode({key: params[key] for key in ("service", "scope") if key in params})
    request = urllib.request.Request(f"{params['realm']}?{query}")
    job_token = os.environ.get("CI_JOB_TOKEN")
    if job_token:
        credentials = base64.b64encode(f"gitlab-ci-token:{job_token}".encode()).decode()
        request.add_header("Authorization", f"Basic {credentials}")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = json.loads(response.read())
    return body.get("token") or body["access_token"]


def manifest_exists(registry: str, repository: str, tag: str) -> bool:
    """Whether the registry has a manifest for repository:tag (HEAD, as `docker
    pull` asks); raises on anything but found / not found."""
    url = f"https://{registry}/v2/{repository}/manifests/{tag}"
    token: Optional[str] = None
    for _ in range(2):
        request = urllib.request.Request(url, method="HEAD", headers={"Accept": MANIFEST_TYPES})
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT):
                return True
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            challenge = error.headers.get("WWW-Authenticate", "")
            if error.code == 401 and token is None and challenge.lower().startswith("bearer "):
                token = _bearer_token(challenge)
                continue
            raise
    raise RuntimeError(f"{url}: still unauthorized with a pull token")


def main() -> int:
    """Check every pinned tag; report and return the exit status."""
    missing = []
    for tag_variable, reference in image_references().items():
        try:
            found = manifest_exists(*split_reference(reference))
        except (urllib.error.URLError, RuntimeError, KeyError, ValueError) as error:
            print(f"ERROR {tag_variable}: could not ask the registry for {reference}: {error}")
            return 2
        print(f"{'OK     ' if found else 'MISSING'} {tag_variable}: {reference}")
        if not found:
            missing.append((tag_variable, reference))
    for tag_variable, reference in missing:
        print(
            f"\n{reference} ({tag_variable} in {PINNED_TAG_VARIABLES[tag_variable]}) is not in the registry."
            "\nA template may only name a published tag: merge the MR that builds this tag first,"
            "\nwait for its develop pipeline to publish it, then rebase this branch and retry this job."
            '\nSee "Moving Templates to a New Image Tag" in CLAUDE.md.'
        )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
