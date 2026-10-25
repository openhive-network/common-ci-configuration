"""pip pointed at the pypi-cache proxy must tolerate a slow cache miss.

On a miss devpi fetches the file upstream before answering, which can outlast
pip's 15 s default read timeout on every retry and fail the job.
"""

from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

import pytest
import yaml

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
REQUIRED = {"PIP_DEFAULT_TIMEOUT": "120", "PIP_RETRIES": "10"}

_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _variable_blocks() -> Iterator[Tuple[str, Dict[str, Any]]]:
    for path in sorted(TEMPLATES.glob("*.yml")):
        doc = yaml.load(path.read_text(), Loader=_Loader) or {}
        if isinstance(doc.get("variables"), dict):
            yield f"{path.name}:variables", doc["variables"]
        for name, job in doc.items():
            if isinstance(job, dict) and isinstance(job.get("variables"), dict):
                yield f"{path.name}:{name}", job["variables"]


_CACHE_BLOCKS = [
    (where, block)
    for where, block in _variable_blocks()
    if "pypi-cache" in str(block.get("PIP_INDEX_URL", ""))
]


def test_some_template_points_pip_at_pypi_cache() -> None:
    assert _CACHE_BLOCKS, "no template sets PIP_INDEX_URL to pypi-cache; update this test"


@pytest.mark.parametrize("where,block", _CACHE_BLOCKS, ids=[w for w, _ in _CACHE_BLOCKS])
def test_pypi_cache_index_sets_timeout_and_retries(where: str, block: Dict[str, Any]) -> None:
    assert {key: block.get(key) for key in REQUIRED} == REQUIRED, where
