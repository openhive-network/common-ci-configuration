"""scripts/github-actions/gitlab-wait-for-artifact.sh: polling a GitLab pipeline and extracting job artifacts.

The script is run against a local HTTP server that plays the GitLab API: each
poll of the pipelines endpoint advances through a scripted sequence of answers,
job listings are paginated like GitLab's, and job artifact archives are zip files
built here.
"""

import io
import json
import re
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "github-actions" / "gitlab-wait-for-artifact.sh"
PROJECT = "452"
SHA = "5d935b802bf63dd6ae9d7911b398e4cc3b84e819"


def pipeline(pipeline_id: int, status: str, sha: str = SHA) -> dict:
    return {"id": pipeline_id, "status": status, "sha": sha, "ref": "1.2.3",
            "web_url": f"https://gitlab.example/p/-/pipelines/{pipeline_id}"}


def job(job_id: int, name: str, status: str) -> dict:
    return {"id": job_id, "name": name, "status": status, "stage": "build",
            "web_url": f"https://gitlab.example/p/-/jobs/{job_id}"}


def zip_bytes(files: Dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FakeGitLab:
    """Scripted GitLab API: pipelines and jobs answers advance per request, the last answer repeats."""

    def __init__(self) -> None:
        self.pipelines: List[List[dict]] = [[]]
        self.jobs: List[List[dict]] = [[]]
        self.artifacts: Dict[int, Optional[bytes]] = {}
        self.requests: List[dict] = []
        self.lock = threading.Lock()

    def next_pipelines(self) -> List[dict]:
        with self.lock:
            if len(self.pipelines) > 1:
                return self.pipelines.pop(0)
            return self.pipelines[0]

    def next_jobs(self) -> List[dict]:
        with self.lock:
            if len(self.jobs) > 1:
                return self.jobs.pop(0)
            return self.jobs[0]


def make_handler(gitlab: FakeGitLab):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # keep pytest output clean
            pass

        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            url = urlparse(self.path)
            query = parse_qs(url.query)
            gitlab.requests.append({"path": url.path, "query": query, "headers": dict(self.headers)})
            prefix = f"/api/v4/projects/{PROJECT}"
            if url.path == f"{prefix}/pipelines":
                self._send(200, json.dumps(gitlab.next_pipelines()).encode())
            elif re.fullmatch(f"{prefix}/pipelines/\\d+/jobs", url.path):
                page = int(query.get("page", ["1"])[0])
                per_page = int(query.get("per_page", ["100"])[0])
                jobs = gitlab.next_jobs() if page == 1 else gitlab.jobs[0]
                self._send(200, json.dumps(jobs[(page - 1) * per_page:page * per_page]).encode())
            elif match := re.fullmatch(f"{prefix}/jobs/(\\d+)/artifacts", url.path):
                archive = gitlab.artifacts.get(int(match.group(1)))
                if archive is None:
                    self._send(404, b'{"message":"404 Not Found"}')
                else:
                    self._send(200, archive, "application/octet-stream")
            else:
                self._send(404, b'{"message":"404 Not Found"}')

    return Handler


@pytest.fixture(name="gitlab")
def gitlab_fixture():
    fake = FakeGitLab()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


def run(gitlab: FakeGitLab, tmp_path: Path, *args: str, timeout: str = "20") -> subprocess.CompletedProcess:
    env_file = tmp_path / "out.env"
    return subprocess.run(
        ["bash", str(SCRIPT), "--gitlab-url", gitlab.url, "--project", PROJECT, "--ref", "1.2.3",  # type: ignore[attr-defined]
         "--output-dir", str(tmp_path / "out"), "--output-env", str(env_file),
         "--poll-interval", "0.05", "--timeout", timeout, *args],
        capture_output=True, text=True, check=False)


def outputs(tmp_path: Path) -> Dict[str, str]:
    return dict(line.split("=", 1) for line in (tmp_path / "out.env").read_text().splitlines())


def test_waits_for_pipeline_and_job_then_extracts_matching_files(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[], [pipeline(10, "running")], [pipeline(10, "running")], [pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "build", "running")], [job(1, "build", "success")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg-1.2.3.tgz": b"tgz", "dist/info.json": b"{}", "README.md": b"#"})

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz", "--expected-sha", SHA[:8])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "dist" / "pkg-1.2.3.tgz").read_bytes() == b"tgz"
    assert not (tmp_path / "out" / "dist" / "info.json").exists()
    assert outputs(tmp_path) == {
        "artifact_dir": str(tmp_path / "out"), "artifact_files": "dist/pkg-1.2.3.tgz",
        "pipeline_id": "10", "pipeline_url": "https://gitlab.example/p/-/pipelines/10",
        "pipeline_sha": SHA, "job_ids": "1"}
    assert "Pipeline 10 (success" in result.stdout


def test_matrix_jobs_are_matched_by_name_prefix_and_all_downloaded(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "build_wheel: [3.12]", "success"), job(2, "build_wheel: [3.14]", "success"),
                    job(3, "build_wheel_docs", "failed")]]
    gitlab.artifacts[1] = zip_bytes({"dist/a-1.2.3-cp312.whl": b"12", "dist/shared.whl": b"s"})
    gitlab.artifacts[2] = zip_bytes({"dist/a-1.2.3-cp314.whl": b"14", "dist/shared.whl": b"s"})

    result = run(gitlab, tmp_path, "--job", "build_wheel", "--artifact", "dist/*.whl")

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (tmp_path / "out" / "dist").iterdir()) == ["a-1.2.3-cp312.whl", "a-1.2.3-cp314.whl", "shared.whl"]
    assert outputs(tmp_path)["job_ids"] == "1 2"
    assert outputs(tmp_path)["artifact_files"] == "dist/a-1.2.3-cp312.whl dist/shared.whl dist/a-1.2.3-cp314.whl"


def test_pipeline_of_expected_commit_wins_over_newer_pipeline_of_another_commit(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(11, "success", sha="f" * 40), pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "build", "success")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz", "--expected-sha", SHA)

    assert result.returncode == 0, result.stderr
    assert outputs(tmp_path)["pipeline_id"] == "10"


def test_job_listing_is_paginated(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success")]]
    gitlab.jobs = [[job(i, f"test_{i}", "success") for i in range(1, 121)] + [job(121, "build", "success")]]
    gitlab.artifacts[121] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz")

    assert result.returncode == 0, result.stderr
    pages = [r["query"].get("page", ["1"])[0] for r in gitlab.requests if r["path"].endswith("/jobs")]
    assert pages == ["1", "2", "3"]


@pytest.mark.parametrize("status", ["failed", "canceled", "skipped", "manual"])
def test_job_that_did_not_succeed_fails_with_exit_2(gitlab: FakeGitLab, tmp_path: Path, status: str) -> None:
    gitlab.pipelines = [[pipeline(10, "running")]]
    gitlab.jobs = [[job(1, "build", status)]]

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz")

    assert result.returncode == 2
    assert f"build: {status}" in result.stderr


def test_finished_pipeline_without_the_job_fails_with_exit_2(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "other", "success")]]

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz")

    assert result.returncode == 2
    assert "has no job named 'build'" in result.stderr


def test_successful_job_in_failed_pipeline_fails_unless_waiting_for_the_job_only(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "failed")]]
    gitlab.jobs = [[job(1, "build", "success")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    assert run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz").returncode == 2
    assert run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz", "--wait-for", "job").returncode == 0


def test_blocked_pipeline_counts_as_finished(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "manual")]]
    gitlab.jobs = [[job(1, "build", "success"), job(2, "deploy", "manual")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz")

    assert result.returncode == 0, result.stderr
    assert "blocked on a manual job" in result.stdout


def test_times_out_with_exit_3_while_pipeline_keeps_running(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "running")]]
    gitlab.jobs = [[job(1, "build", "success")]]

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz", timeout="1")

    assert result.returncode == 3
    assert "timed out after 1s" in result.stderr


def test_times_out_when_no_pipeline_appears_for_the_commit(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success", sha="f" * 40)]]

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz", "--expected-sha", SHA, timeout="1")

    assert result.returncode == 3
    assert "No pipeline for ref '1.2.3' at commit" in result.stdout


def test_missing_or_expired_artifacts_fail_with_exit_4(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "build", "success")]]

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz")

    assert result.returncode == 4
    assert "expired" in result.stderr


def test_archive_without_a_matching_entry_fails_with_exit_4_and_lists_contents(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "build", "success")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    result = run(gitlab, tmp_path, "--job", "build", "--artifact", "build/*.whl")

    assert result.returncode == 4
    assert "nothing matching 'build/*.whl'" in result.stderr
    assert "dist/pkg.tgz" in result.stderr


def test_annotated_tag_sha_is_peeled_to_its_commit_inside_a_checkout(gitlab: FakeGitLab, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "release"], check=True)
    subprocess.run([*git, "tag", "-a", "-m", "1.2.3", "1.2.3"], check=True)
    commit = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    tag_object = subprocess.run([*git, "rev-parse", "1.2.3"], capture_output=True, text=True, check=True).stdout.strip()
    assert tag_object != commit
    gitlab.pipelines = [[pipeline(10, "success", sha=commit)]]
    gitlab.jobs = [[job(1, "build", "success")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    result = subprocess.run(
        ["bash", str(SCRIPT), "--gitlab-url", gitlab.url, "--project", PROJECT, "--ref", "1.2.3",  # type: ignore[attr-defined]
         "--job", "build", "--artifact", "dist/*.tgz", "--output-dir", str(tmp_path / "out"),
         "--expected-sha", tag_object, "--poll-interval", "0.05", "--timeout", "5"],
        cwd=repo, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert f"resolved in the checkout to commit {commit}" in result.stdout


def test_token_is_sent_as_private_token_header(gitlab: FakeGitLab, tmp_path: Path) -> None:
    gitlab.pipelines = [[pipeline(10, "success")]]
    gitlab.jobs = [[job(1, "build", "success")]]
    gitlab.artifacts[1] = zip_bytes({"dist/pkg.tgz": b"tgz"})

    assert run(gitlab, tmp_path, "--job", "build", "--artifact", "dist/*.tgz", "--token", "glpat-x").returncode == 0
    assert {r["headers"].get("PRIVATE-TOKEN") for r in gitlab.requests} == {"glpat-x"}


@pytest.mark.parametrize("args, message", [
    (["--job", "build"], "--artifact is required"),
    (["--job", "build", "--artifact", "x", "--wait-for", "never"], "--wait-for must be"),
    (["--job", "build", "--artifact", "x", "--timeout", "soon"], "--timeout must be"),
])
def test_usage_errors(gitlab: FakeGitLab, tmp_path: Path, args: List[str], message: str) -> None:
    result = run(gitlab, tmp_path, *args)

    assert result.returncode == 1
    assert message in result.stderr
    assert gitlab.requests == []


def test_missing_required_option_prints_usage(gitlab: FakeGitLab, tmp_path: Path) -> None:
    result = run(gitlab, tmp_path, "--job", "build")

    assert "Usage:" in result.stderr
