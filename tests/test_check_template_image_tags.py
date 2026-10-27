"""scripts/python/check-template-image-tags.py: a template may only name a published image tag."""

import importlib.util
import urllib.error
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".gitlab-ci.yml"
JOB = "check_template_image_tags"

_Loader: Any = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load("check_template_image_tags", ROOT / "scripts" / "python" / "check-template-image-tags.py")


class _Response:
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def __init__(self, body: bytes = b"") -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body


def _http_error(url: str, code: int, challenge: Optional[str] = None) -> urllib.error.HTTPError:
    headers = Message()
    if challenge:
        headers["WWW-Authenticate"] = challenge
    return urllib.error.HTTPError(url, code, "error", headers, None)


class _Registry:
    """Fake urlopen: answers manifest HEADs with `codes` in turn and token GETs with a token."""

    def __init__(self, codes: List[int], challenge: Optional[str] = None) -> None:
        self.codes = codes
        self.challenge = challenge
        self.requests: List[Any] = []

    def __call__(self, request: Any, timeout: int) -> _Response:
        self.requests.append(request)
        if "/manifests/" not in request.full_url:
            return _Response(b'{"token": "pull-token"}')
        code = self.codes.pop(0)
        if code == 200:
            return _Response()
        raise _http_error(request.full_url, code, self.challenge if code == 401 else None)


def test_pinned_variables_are_those_of_the_versions_test() -> None:
    versions_test = _load("versions_test", ROOT / "tests" / "test_tool_versions_defined_once.py")

    assert check.PINNED_TAG_VARIABLES == {
        variable: template for variable, (template, _) in versions_test.TEMPLATE_PINNED_TAGS.items()
    }


def test_image_references_name_this_repos_images_at_the_pinned_tag() -> None:
    for variable, reference in check.image_references().items():
        template = yaml.load((ROOT / check.PINNED_TAG_VARIABLES[variable]).read_text(), Loader=_Loader)
        registry, repository, tag = check.split_reference(reference)

        assert registry == "registry.gitlab.syncad.com"
        assert repository.startswith("hive/common-ci-configuration/")
        assert tag == template["variables"][variable]


@pytest.mark.parametrize(
    ("reference", "parts"),
    [
        ("registry.example/hive/app/emsdk:5.0.2-8", ("registry.example", "hive/app/emsdk", "5.0.2-8")),
        ("registry.example:5000/a/b:1", ("registry.example:5000", "a/b", "1")),
    ],
)
def test_split_reference(reference: str, parts: Any) -> None:
    assert check.split_reference(reference) == parts


@pytest.mark.parametrize("reference", ["registry.example/a/b", "a:1", "registry.example:5000/a/b"])
def test_split_reference_rejects_a_reference_without_a_tag(reference: str) -> None:
    with pytest.raises(ValueError):
        check.split_reference(reference)


@pytest.mark.parametrize(("code", "found"), [(200, True), (404, False)])
def test_manifest_exists_reports_found_and_missing(
    monkeypatch: pytest.MonkeyPatch, code: int, found: bool
) -> None:
    registry = _Registry([code])
    monkeypatch.setattr(check.urllib.request, "urlopen", registry)

    assert check.manifest_exists("registry.example", "hive/app/emsdk", "5.0.2-8") is found
    assert registry.requests[0].get_method() == "HEAD"
    assert registry.requests[0].full_url == "https://registry.example/v2/hive/app/emsdk/manifests/5.0.2-8"


@pytest.mark.parametrize(("job_token", "basic"), [(None, False), ("job-token", True)])
def test_manifest_exists_takes_a_pull_token_on_a_bearer_challenge(
    monkeypatch: pytest.MonkeyPatch, job_token: Optional[str], basic: bool
) -> None:
    challenge = 'Bearer realm="https://gitlab.example/jwt/auth",service="container_registry",scope="repository:hive/app/emsdk:pull"'
    registry = _Registry([401, 200], challenge)
    monkeypatch.setattr(check.urllib.request, "urlopen", registry)
    if job_token:
        monkeypatch.setenv("CI_JOB_TOKEN", job_token)
    else:
        monkeypatch.delenv("CI_JOB_TOKEN", raising=False)

    assert check.manifest_exists("registry.example", "hive/app/emsdk", "5.0.2-8")
    token_request, retried = registry.requests[1], registry.requests[2]
    assert token_request.full_url == (
        "https://gitlab.example/jwt/auth?service=container_registry&scope=repository%3Ahive%2Fapp%2Femsdk%3Apull"
    )
    assert (token_request.get_header("Authorization") or "").startswith("Basic ") is basic
    assert retried.get_header("Authorization") == "Bearer pull-token"


def test_manifest_exists_raises_on_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check.urllib.request, "urlopen", _Registry([500]))

    with pytest.raises(urllib.error.HTTPError):
        check.manifest_exists("registry.example", "hive/app/emsdk", "5.0.2-8")


def _main(monkeypatch: pytest.MonkeyPatch, found: Dict[str, Any]) -> int:
    monkeypatch.setattr(
        check, "image_references", lambda: {variable: f"registry.example/hive/app/{variable.lower()}:1" for variable in found}
    )

    def manifest_exists(registry: str, repository: str, tag: str) -> bool:
        result = found[repository.rsplit("/", 1)[1].upper()]
        if isinstance(result, Exception):
            raise result
        return bool(result)

    monkeypatch.setattr(check, "manifest_exists", manifest_exists)
    return int(check.main())


def test_main_passes_when_every_tag_is_published(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _main(monkeypatch, {"EMSCRIPTEN_IMAGE_TAG": True, "ALPINE_IMAGE_TAG": True}) == 0


def test_main_fails_and_says_what_to_do_when_a_tag_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(monkeypatch, {"EMSCRIPTEN_IMAGE_TAG": False, "ALPINE_IMAGE_TAG": True}) == 1
    out = capsys.readouterr().out
    assert "MISSING EMSCRIPTEN_IMAGE_TAG" in out
    assert "Moving Templates to a New Image Tag" in out


def test_main_reports_a_registry_it_could_not_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.URLError("connection refused")

    assert _main(monkeypatch, {"EMSCRIPTEN_IMAGE_TAG": error}) == 2


def test_ci_job_runs_the_check_on_every_branch_pipeline() -> None:
    job = yaml.load(CI.read_text(), Loader=_Loader)[JOB]

    assert job["script"] == ["python3 scripts/python/check-template-image-tags.py"]
    assert {"if": "$CI_COMMIT_BRANCH"} in job["rules"]
