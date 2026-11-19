"""install_with_app_lock.py: a held install lock skips a rerun of the installed version and fails
an upgrade; a free lock runs the install.

psycopg2 is replaced by a stand-in while the wrapper is loaded, so this needs no PostgreSQL: the
stand-in connection stubs hive.try_acquire_app_install_lock (its result and the NOTICE naming the
holder) and answers the installed-version query, as an app that records its git hash in
`<schema>.version` does. The install command is a real subprocess that leaves a marker file, so a
test sees whether it ran.
"""

import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any, Self

import pytest

_saved = {name: sys.modules.get(name) for name in ("psycopg2", "psycopg2.errors")}
_pg = types.ModuleType("psycopg2")
_pg_errors = types.ModuleType("psycopg2.errors")
_Error = type("Error", (Exception,), {})
for _name in ("UndefinedFunction", "UndefinedTable", "InvalidSchemaName", "InsufficientPrivilege"):
    setattr(_pg_errors, _name, type(_name, (_Error,), {}))
setattr(_pg, "Error", _Error)
setattr(_pg, "errors", _pg_errors)
sys.modules["psycopg2"] = _pg
sys.modules["psycopg2.errors"] = _pg_errors

SCRIPT = Path(__file__).resolve().parent.parent / "haf-app-tools" / "scripts" / "install_with_app_lock.py"
_spec = importlib.util.spec_from_file_location("install_with_app_lock", SCRIPT)
assert _spec is not None and _spec.loader is not None
wrapper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wrapper)
for _name, _module in _saved.items():
    if _module is None:
        del sys.modules[_name]
    else:
        sys.modules[_name] = _module

APP = "balance_tracker"
DSN = "postgresql://haf_admin@haf/haf_block_log"
HOLDER = "NOTICE:  install lock for balance_tracker held by pid 4242 (balance_tracker-postgrest)"
VERSION_QUERY = "SELECT git_hash FROM btracker_app.version"
OLD = "1111111111111111111111111111111111111111"
NEW = "2222222222222222222222222222222222222222"


class FakeDatabase:
    """The server side: whether the lock is free and what the version query sees."""

    def __init__(self: Self, *, lock_free: bool, installed: str | None = None,
                 version_error: type[Exception] | None = None, lock_helpers: bool = True) -> None:
        self.lock_free = lock_free
        self.installed = installed
        self.version_error = version_error
        self.lock_helpers = lock_helpers
        self.queries: list[str] = []
        self.closed = False


class FakeCursor:
    def __init__(self: Self, conn: "FakeConn") -> None:
        self.conn = conn
        self.result: tuple[Any, ...] | None = None

    def __enter__(self: Self) -> Self:
        return self

    def __exit__(self: Self, *exc: object) -> None:
        pass

    def execute(self: Self, sql: str, params: object = None) -> None:
        db = self.conn.db
        db.queries.append(sql)
        if sql.startswith("SET "):
            return
        if sql.startswith("SELECT hive.try_acquire_app_install_lock"):
            if not db.lock_helpers:
                raise _pg_errors.UndefinedFunction("function hive.try_acquire_app_install_lock(text) does not exist")
            if not db.lock_free:
                self.conn.notices.append(HOLDER + "\n")
            self.result = (db.lock_free,)
        elif sql == VERSION_QUERY:
            if db.version_error is not None:
                raise db.version_error("version query failed")
            self.result = None if db.installed is None else (db.installed,)
        else:
            raise AssertionError(f"unexpected query: {sql}")

    def fetchone(self: Self) -> tuple[Any, ...] | None:
        return self.result


class FakeConn:
    def __init__(self: Self, db: FakeDatabase) -> None:
        self.db = db
        self.notices: list[str] = []
        self.autocommit = False

    def cursor(self: Self) -> FakeCursor:
        return FakeCursor(self)

    def close(self: Self) -> None:
        self.db.closed = True


@pytest.fixture(autouse=True)
def _no_version_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAF_APP_INSTALL_VERSION", raising=False)
    monkeypatch.delenv("HAF_APP_INSTALLED_VERSION_QUERY", raising=False)


def run_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db: FakeDatabase,
                options: list[str]) -> tuple[int, bool]:
    """Run main() with `options` before APP DSN CMD; return its exit status and whether CMD ran."""
    marker = tmp_path / "installed"
    install_cmd = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').close()", str(marker)]
    monkeypatch.setattr(_pg, "connect", lambda dsn: FakeConn(db), raising=False)
    monkeypatch.setattr(sys, "argv", ["install_with_app_lock.py", *options, APP, DSN, *install_cmd])
    status = wrapper.main()
    assert db.closed
    return status, marker.exists()


def versioned(version: str) -> list[str]:
    return [f"--version={version}", f"--version-query={VERSION_QUERY}"]


def test_held_lock_same_version_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                      caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    db = FakeDatabase(lock_free=False, installed=OLD)

    status, ran = run_wrapper(monkeypatch, tmp_path, db, versioned(OLD))

    assert (status, ran) == (0, False)
    assert f"Skipping {APP} install — version {OLD} is already installed" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_held_lock_other_version_fails_with_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDatabase(lock_free=False, installed=OLD)

    status, ran = run_wrapper(monkeypatch, tmp_path, db, versioned(NEW))

    assert (status, ran) == (1, False)
    [error] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert f"Cannot install {APP} version {NEW}: the installed version is {OLD}" in error
    assert HOLDER in error
    assert f"stop {APP}-postgrest and any other holder of the lock, rerun the install" in error


@pytest.mark.parametrize("db", [
    FakeDatabase(lock_free=False, installed=None),
    FakeDatabase(lock_free=False, version_error=_pg_errors.UndefinedTable),
    FakeDatabase(lock_free=False, version_error=_pg_errors.InvalidSchemaName),
    FakeDatabase(lock_free=False, version_error=_pg_errors.UndefinedFunction),
], ids=["no-row", "no-table", "no-schema", "no-function"])
def test_held_lock_app_not_installed_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                           caplog: pytest.LogCaptureFixture, db: FakeDatabase) -> None:
    status, ran = run_wrapper(monkeypatch, tmp_path, db, versioned(NEW))

    assert (status, ran) == (1, False)
    assert "the installed version is none (not installed" in caplog.text


def test_held_lock_undeterminable_version_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    db = FakeDatabase(lock_free=False, version_error=_pg_errors.InsufficientPrivilege)

    status, ran = run_wrapper(monkeypatch, tmp_path, db, versioned(NEW))

    assert (status, ran) == (0, False)
    assert f"Cannot determine the installed {APP} version" in caplog.text
    assert f"Skipping {APP} install — lock held" in caplog.text


def test_version_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAF_APP_INSTALL_VERSION", NEW)
    monkeypatch.setenv("HAF_APP_INSTALLED_VERSION_QUERY", VERSION_QUERY)
    db = FakeDatabase(lock_free=False, installed=OLD)

    assert run_wrapper(monkeypatch, tmp_path, db, []) == (1, False)


@pytest.mark.parametrize("options", [[], versioned(NEW)], ids=["unversioned", "versioned"])
def test_free_lock_runs_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, options: list[str]) -> None:
    db = FakeDatabase(lock_free=True, installed=OLD)

    assert run_wrapper(monkeypatch, tmp_path, db, options) == (0, True)
    assert VERSION_QUERY not in db.queries


def test_unversioned_app_skips_with_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                            caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    db = FakeDatabase(lock_free=False, installed=OLD)

    status, ran = run_wrapper(monkeypatch, tmp_path, db, [])

    assert (status, ran) == (0, False)
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert f"for {APP}; upgrade detection is off" in warning
    assert f"Skipping {APP} install — lock held by an active block-processor." in caplog.text
    assert VERSION_QUERY not in db.queries


def test_missing_lock_helpers_runs_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = FakeDatabase(lock_free=False, installed=OLD, lock_helpers=False)

    assert run_wrapper(monkeypatch, tmp_path, db, versioned(NEW)) == (0, True)


@pytest.mark.parametrize("argv", [
    ["--version", APP, DSN, "true"],
    ["--bogus=1", APP, DSN, "true"],
    [APP, DSN],
], ids=["option-without-value", "unknown-option", "no-command"])
def test_invalid_call_is_usage_error(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(_pg, "connect", lambda dsn: pytest.fail("connected on a usage error"), raising=False)
    monkeypatch.setattr(sys, "argv", ["install_with_app_lock.py", *argv])

    assert wrapper.main() == 2


def test_options_after_app_name_reach_the_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    marker = tmp_path / "args"
    install_cmd = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write(sys.argv[2])",
                   str(marker), "--version=x"]
    db = FakeDatabase(lock_free=True)
    monkeypatch.setattr(_pg, "connect", lambda dsn: FakeConn(db), raising=False)
    monkeypatch.setattr(sys, "argv", ["install_with_app_lock.py", APP, DSN, *install_cmd])

    assert wrapper.main() == 0
    assert marker.read_text() == "--version=x"
