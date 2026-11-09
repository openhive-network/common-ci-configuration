"""The stack run.sh starts: REWRITER_URL is the rewriter, POSTGREST_URL the PostgREST behind it,
RPC_ARGUMENTS the argument map process_openapi.py wrote for the rewriter."""
import os
import time
from pathlib import Path

import pytest
from rest_client import Service

READY_TIMEOUT_S = 60


@pytest.fixture(scope="session")
def rewriter() -> Service:
    service = Service(os.environ["REWRITER_URL"])
    # PostgREST answers once it has loaded its schema cache
    deadline = time.monotonic() + READY_TIMEOUT_S
    while (answer := _probe(service)) != 200:
        if time.monotonic() > deadline:
            pytest.fail(f"the stack does not answer /version after {READY_TIMEOUT_S}s (last: {answer})")
        time.sleep(0.5)
    return service


def _probe(service: Service) -> object:
    try:
        return service.get("/version").status
    except OSError as err:
        return err


@pytest.fixture(scope="session")
def postgrest() -> Service:
    return Service(os.environ["POSTGREST_URL"])


@pytest.fixture(scope="session")
def rpc_arguments() -> str:
    return Path(os.environ["RPC_ARGUMENTS"]).read_text()
