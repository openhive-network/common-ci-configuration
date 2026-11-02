#!/usr/bin/env python3
"""Write the pip constraints of a python-test-tools image to a file.

    python3 python-test-tools-constraints.py <image reference> <output file>

python-test-tools publishes its Python tools' pins (`name==version`, exactly the
versions it installs, from docker-bake.hcl) as the image label
io.hive.python-test-tools.constraints. This reads that label from the registry,
without pulling the image, so a job in any image (or a Docker build) can install
the same versions with `pip install -c <output file> pytest ...`.

Standard library only (with registry_auth.py next to it), so it runs in any image
with python3; the template downloads both files.

Exit status: 0 when the file was written, 1 when the constraints could not be
obtained (the message names the image and the cause); no file is written then.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from registry_auth import TIMEOUT, bearer_token

CONSTRAINTS_LABEL = "io.hive.python-test-tools.constraints"
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
IMAGE_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
PLATFORM = {"os": "linux", "architecture": "amd64"}
_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.+!_-]*")


class ConstraintsError(Exception):
    """The constraints of an image could not be obtained; the message says why."""


def split_reference(reference: str) -> Tuple[str, str, str]:
    """registry/repository:tag or registry/repository@digest -> (registry, repository, tag or digest)."""
    registry, _, path = reference.partition("/")
    if "@" in path:
        repository, _, tag = path.partition("@")
    else:
        repository, _, tag = path.rpartition(":")
    if not ("." in registry or ":" in registry) or not repository or not tag or "/" in tag:
        raise ConstraintsError(f"not a registry/repository:tag image reference: {reference!r}")
    return registry, repository, tag


class _Registry:
    """GET access to one repository of a v2 registry, taking a pull token when asked."""

    def __init__(self, registry: str, repository: str) -> None:
        self.base = f"https://{registry}/v2/{repository}"
        self.token: Optional[str] = None

    def get(self, path: str, accept: str) -> bytes:
        """Body of `path` under the repository; raises HTTPError / URLError."""
        for _ in range(2):
            request = urllib.request.Request(f"{self.base}/{path}", headers={"Accept": accept})
            if self.token:
                # Not sent on to the blob storage a registry redirects to
                request.add_unredirected_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    body: bytes = response.read()
                    return body
            except urllib.error.HTTPError as error:
                challenge = error.headers.get("WWW-Authenticate", "")
                if error.code == 401 and self.token is None and challenge.lower().startswith("bearer "):
                    try:
                        self.token = bearer_token(challenge)
                    except KeyError as missing:
                        raise ConstraintsError(f"registry auth gave no {missing} for challenge {challenge!r}") from missing
                    continue
                raise
        raise ConstraintsError(f"{self.base}/{path}: still unauthorized with a pull token")

    def manifest(self, reference: str) -> Dict[str, Any]:
        """The image manifest for tag or digest `reference`, an index resolved to PLATFORM."""
        manifest: Dict[str, Any] = json.loads(self.get(f"manifests/{reference}", ", ".join(INDEX_TYPES + IMAGE_TYPES)))
        if manifest.get("mediaType") in INDEX_TYPES or "manifests" in manifest:
            entries = [
                entry
                for entry in manifest.get("manifests", [])
                if all((entry.get("platform") or {}).get(key) == value for key, value in PLATFORM.items())
            ]
            if not entries:
                raise ConstraintsError(f"the image index has no {PLATFORM['os']}/{PLATFORM['architecture']} image")
            return self.manifest(entries[0]["digest"])
        return manifest


def parse_constraints(label: str) -> List[str]:
    """The `name==version` lines of a constraints label (pins separated by whitespace)."""
    pins = label.split()
    malformed = [pin for pin in pins if not _PIN.fullmatch(pin)]
    if not pins or malformed:
        raise ConstraintsError(
            f"label {CONSTRAINTS_LABEL} is not a list of name==version pins: {label!r}"
        )
    return pins


def image_constraints(reference: str) -> List[str]:
    """The constraints lines image `reference` publishes; raises ConstraintsError."""
    registry, repository, tag = split_reference(reference)
    client = _Registry(registry, repository)
    try:
        manifest = client.manifest(tag)
        config_digest = (manifest.get("config") or {}).get("digest")
        if not config_digest:
            raise ConstraintsError("the image manifest names no config blob")
        config = json.loads(client.get(f"blobs/{config_digest}", "application/json, */*"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ConstraintsError(f"tag {tag!r} is not in the registry (HTTP 404)") from error
        raise ConstraintsError(f"the registry answered HTTP {error.code} {error.reason}") from error
    except urllib.error.URLError as error:
        raise ConstraintsError(f"the registry {registry} is unreachable: {error.reason}") from error
    except (OSError, ValueError) as error:
        raise ConstraintsError(f"reading the image from {registry} failed: {error}") from error
    label = ((config.get("config") or {}).get("Labels") or {}).get(CONSTRAINTS_LABEL)
    if label is None:
        raise ConstraintsError(
            f"the image has no {CONSTRAINTS_LABEL} label (built before python-test-tools"
            " published its constraints, or not a python-test-tools image)"
        )
    return parse_constraints(label)


def main(argv: List[str]) -> int:
    """Write the constraints of argv[1] to argv[2]; report and return the exit status."""
    if len(argv) != 3:
        print(f"usage: {argv[0]} <python-test-tools image reference> <output file>", file=sys.stderr)
        return 1
    reference, output = argv[1], argv[2]
    try:
        pins = image_constraints(reference)
    except ConstraintsError as error:
        print(f"ERROR: no pip constraints for {reference}: {error}", file=sys.stderr)
        return 1
    with open(output, "w", encoding="utf-8") as constraints:
        constraints.write("".join(f"{pin}\n" for pin in pins))
    print(f"Wrote the pip constraints of {reference} to {output}:")
    print("".join(f"  {pin}\n" for pin in pins), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
