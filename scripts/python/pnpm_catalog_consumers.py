'''
The projects consuming ccc's pnpm catalogs (pnpm-config/catalog-consumers.txt)
and how a clone of one is pointed at a ccc commit's catalogs: shared by
pnpm-catalog-resync.py and pnpm-catalog-consumer-check.py.
'''
import base64
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
CONSUMERS = ROOT / "pnpm-config" / "catalog-consumers.txt"
MERGE = "merge"
SYMLINK = "symlink"
NO_AUTO_MERGE = "no-auto-merge"
SYNC_SCRIPT = Path("scripts") / "python" / "pnpm-catalog-sync.py"
_CATALOG = posixpath.join("pnpm-config", "pnpm-workspace.yaml")


class ConsumerError(Exception):
    """A consumer that cannot be processed: no ccc submodule, an API call refused, ..."""


class Consumer(NamedTuple):
    """A project consuming ccc's catalogs.

    `mode` is MERGE for a monorepo merging them into its own pnpm-workspace.yaml,
    SYMLINK for one whose pnpm-workspace.yaml files are symlinks into the submodule.
    """
    project: str
    submodule: str
    mode: str
    auto_merge: bool = True


def read_consumers(path: Path = CONSUMERS) -> List[Consumer]:
    """The consumers listed in `path`, one per line, `#` comments:
    `<project path> <ccc submodule path> <merge|symlink> [no-auto-merge]`.

    Raises ConsumerError for a line that does not have that shape.
    """
    consumers = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) not in (3, 4) or fields[2] not in (MERGE, SYMLINK) or fields[3:] not in ([], [NO_AUTO_MERGE]):
            raise ConsumerError(f"{path}:{number}: expected `<project path> <ccc submodule path> <{MERGE}|{SYMLINK}>"
                                f" [{NO_AUTO_MERGE}]`, got {line.strip()!r}")
        consumers.append(Consumer(fields[0], fields[1], fields[2], auto_merge=len(fields) == 3))
    return consumers


def git(repo: Path, *args: str, stdout: Optional[int] = None) -> str:
    """Run git in `repo`; its output when `stdout` is subprocess.PIPE. Raises CalledProcessError."""
    output = subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=stdout, text=True).stdout
    return output or ""


def _symlinked_workspaces(repo: Path, submodule: str) -> List[str]:
    """Directories of `repo` (POSIX, "." for the root) whose tracked pnpm-workspace.yaml is a
    symlink to `submodule`'s catalog file and which track a pnpm-lock.yaml."""
    catalog = posixpath.join(posixpath.normpath(submodule), _CATALOG)
    tracked = set(git(repo, "ls-files", stdout=subprocess.PIPE).splitlines())
    directories = []
    for entry in git(repo, "ls-files", "--stage", stdout=subprocess.PIPE).splitlines():
        meta, path = entry.split("\t", 1)
        if not meta.startswith("120000 ") or posixpath.basename(path) != "pnpm-workspace.yaml":
            continue
        directory = posixpath.dirname(path) or "."
        target = os.readlink(repo / path)
        if posixpath.normpath(posixpath.join(directory, target)) != catalog:
            continue
        if posixpath.normpath(posixpath.join(directory, "pnpm-lock.yaml")) in tracked:
            directories.append(directory)
    return directories


def point_at(repo: Path, consumer: Consumer, ccc: Path, ccc_commit: str) -> List[str]:
    """Check `ccc_commit` of the ccc repository `ccc` out as the clone `repo`'s ccc submodule and,
    for a MERGE consumer, merge its catalogs into pnpm-workspace.yaml (as the consumer's CI does).

    Returns the directories (POSIX, relative to `repo`) whose pnpm-lock.yaml follows the catalogs:
    "." for a MERGE consumer, each directory with a symlinked pnpm-workspace.yaml for a SYMLINK one.
    Raises ConsumerError when `consumer.submodule` is not a submodule of `repo` or a SYMLINK consumer
    has no such directory, and subprocess.CalledProcessError when git or the merge fails.
    """
    if not git(repo, "ls-tree", "HEAD", "--", consumer.submodule, stdout=subprocess.PIPE).startswith("160000 "):
        raise ConsumerError(f"no ccc submodule at {consumer.submodule}")
    directories = ["."]
    if consumer.mode == SYMLINK:
        directories = _symlinked_workspaces(repo, consumer.submodule)
        if not directories:
            raise ConsumerError(f"no pnpm-workspace.yaml symlink to {consumer.submodule}/{_CATALOG} next to a pnpm-lock.yaml")
    checkout = repo / consumer.submodule
    checkout.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(ccc), "archive", "--format=tar", ccc_commit], check=True, stdout=subprocess.PIPE).stdout
    subprocess.run(["tar", "-x", "-C", str(checkout)], input=archive, check=True)
    if consumer.mode == MERGE:
        subprocess.run([sys.executable, str(checkout / SYNC_SCRIPT), "pnpm-workspace.yaml"], cwd=repo, check=True)
    return directories


def clone(server_url: str, project: str, destination: Path, branch: Optional[str] = None) -> None:
    """Shallow-clone `project` from `server_url` (its default branch unless `branch`), without submodules."""
    selected = ["--branch", branch] if branch else []
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", *selected, f"{server_url}/{project}.git", str(destination)], check=True)


def _check_pnpm() -> None:
    """Raise ConsumerError unless the pnpm on PATH is docker-bake.hcl's PNPM_VERSION (OSError when there is none)."""
    found = re.search(r'variable "PNPM_VERSION" \{\s*default = "([^"]+)"', (ROOT / "docker-bake.hcl").read_text(encoding="utf-8"))
    if not found:
        raise ConsumerError("docker-bake.hcl defines no PNPM_VERSION")
    running = subprocess.run(["pnpm", "--version"], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    if running != found.group(1):
        raise ConsumerError(f"pnpm on PATH is {running}, docker-bake.hcl's PNPM_VERSION is {found.group(1)}")


def start(commit: str) -> Tuple[List[Consumer], str]:
    """The listed consumers and `commit` resolved to a commit SHA of ROOT, once the pnpm on PATH is checked.

    Raises ConsumerError for a bad consumer list or pnpm version, OSError when there is no pnpm,
    and subprocess.CalledProcessError when `commit` does not resolve.
    """
    consumers = read_consumers()
    _check_pnpm()
    return consumers, git(ROOT, "rev-parse", "--verify", f"{commit}^{{commit}}", stdout=subprocess.PIPE).strip()


def git_auth_environment(server_url: str, token: str) -> Dict[str, str]:
    """git config, passed through the environment so the token is in no command line or .git/config."""
    credentials = base64.b64encode(f"oauth2:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{server_url}/.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}"}
