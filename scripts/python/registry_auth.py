"""Pull tokens for a container registry's bearer challenge (standard library only,
so the scripts using it run in any image with python3)."""

import base64
import json
import os
import re
import urllib.parse
import urllib.request

TIMEOUT = 30


def bearer_token(challenge: str) -> str:
    """A pull token for a `WWW-Authenticate: Bearer realm=...,service=...,scope=...`
    challenge; with the CI job token when the job has one, anonymous otherwise.

    Raises KeyError when the challenge or the answer lacks the realm or the token,
    and urllib's errors when the realm cannot be asked.
    """
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    query = urllib.parse.urlencode({key: params[key] for key in ("service", "scope") if key in params})
    request = urllib.request.Request(f"{params['realm']}?{query}")
    job_token = os.environ.get("CI_JOB_TOKEN")
    if job_token:
        credentials = base64.b64encode(f"gitlab-ci-token:{job_token}".encode()).decode()
        request.add_unredirected_header("Authorization", f"Basic {credentials}")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = json.loads(response.read())
    token: str = body.get("token") or body["access_token"]
    return token
