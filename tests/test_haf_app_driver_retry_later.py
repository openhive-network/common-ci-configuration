"""haf_app_driver.py: a process procedure raising SQLSTATE 55R23 (RETRY_LATER_SQLSTATE).

The range is rolled back and asked for again every --poll-interval seconds on the same
connection, with one log line when the wait starts and one when a range goes through. Any
other error keeps its old handling: a connection error reconnects, anything else ends the
driver.

The connection is a scripted fake, so this needs no PostgreSQL; psycopg2 is replaced by a
stand-in when it is not installed (the test-tools image has none).
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

try:
    import psycopg2  # noqa: F401
    import psycopg2.extensions  # noqa: F401
except ImportError:
    _pg = types.ModuleType("psycopg2")

    class _Error(Exception):
        pgcode = None
        diag = None

    _pg.Error = _Error
    _pg.DatabaseError = type("DatabaseError", (_Error,), {})
    _pg.OperationalError = type("OperationalError", (_pg.DatabaseError,), {})
    _pg.InternalError = type("InternalError", (_pg.DatabaseError,), {})
    _pg.InterfaceError = type("InterfaceError", (_Error,), {})
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

import psycopg2  # noqa: E402  (the real module, or the stand-in above)

SCRIPT = Path(__file__).resolve().parent.parent / "haf-app-tools" / "scripts" / "haf_app_driver.py"
_spec = importlib.util.spec_from_file_location("haf_app_driver", SCRIPT)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def pg_error(base, pgcode, message):
    """An instance of a psycopg2 exception class carrying pgcode and diag.message_primary, as
    psycopg2 raises for a server error (its own attributes are read-only, so a subclass)."""
    cls = type(base.__name__, (base,), {
        "pgcode": property(lambda self: pgcode),
        "diag": property(lambda self: types.SimpleNamespace(message_primary=message)),
    })
    return cls(f"ERROR:  {message}\n")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = None

    def execute(self, sql, params=None):
        self.conn.statements.append(sql)
        if sql.startswith("CALL hive.app_next_iteration"):
            if self.conn.iteration_error is not None:
                raise self.conn.iteration_error
            self.result = (self.conn.range_text,)
        elif sql.startswith("CALL app.process"):
            if self.conn.procedure_error is not None:
                raise self.conn.procedure_error
        elif sql.startswith("SELECT EXTRACT"):
            self.result = (None,)
        elif sql.startswith("SELECT hive.app_perform_maintenance"):
            self.result = (False,)

    def fetchone(self):
        return self.result


class FakeConn:
    def __init__(self, range_text="(101,110)", procedure_error=None, iteration_error=None):
        self.range_text = range_text
        self.procedure_error = procedure_error
        self.iteration_error = iteration_error
        self.statements = []
        self.notices = []

    def cursor(self):
        return FakeCursor(self)


def make_driver(conn=None, **overrides):
    args = types.SimpleNamespace(
        app="app", override_max_batch=None, stop_at_block=None, server_messages="INFO",
        poll_interval=1.0, safety_interval=15.0, live_log_interval=60.0,
        retry_delay=5.0, max_retry_delay=60.0, startup_marker="", process_python=None,
        postgres_url="postgresql://unused",
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    d = driver.Driver(args)
    d.conn = conn
    d.contexts = ["app"]
    d.lead = "app"
    d.procedure = "app.process"
    return d


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(driver, "log", lines.append)
    return lines


def test_sqlstate_constant_is_class_55():
    # class 55 is what makes an older driver retry it too (psycopg2: OperationalError)
    assert driver.RETRY_LATER_SQLSTATE == "55R23"


def test_retry_later_rolls_back_and_names_the_range():
    conn = FakeConn(procedure_error=pg_error(psycopg2.OperationalError, "55R23", "app: dependency not at block 110"))
    d = make_driver(conn)
    with pytest.raises(driver.RetryLater) as raised:
        d.iterate()
    assert raised.value.blocks == (101, 110)
    assert raised.value.message == "app: dependency not at block 110"
    assert conn.statements[-1] == "ROLLBACK"
    assert "COMMIT" not in conn.statements


@pytest.mark.parametrize("base, pgcode", [
    (psycopg2.InternalError, "P0001"),          # an application error: ends the driver
    (psycopg2.OperationalError, None),          # a dropped connection: run() reconnects
    (psycopg2.OperationalError, "55000"),       # another class-55 code keeps the old handling
])
def test_other_errors_are_not_retry_later(base, pgcode):
    error = pg_error(base, pgcode, "something else")
    conn = FakeConn(procedure_error=error)
    with pytest.raises(base) as raised:
        make_driver(conn).iterate()
    assert raised.value is error
    assert conn.statements[-1] == "ROLLBACK"


def test_55r23_outside_the_procedure_is_not_retry_later():
    # only a range the process procedure was handed is retried this way
    error = pg_error(psycopg2.OperationalError, "55R23", "from app_next_iteration")
    conn = FakeConn(iteration_error=error)
    with pytest.raises(psycopg2.OperationalError) as raised:
        make_driver(conn).iterate()
    assert raised.value is error


def test_a_ready_range_commits():
    conn = FakeConn()
    assert make_driver(conn).iterate() == (101, 110)
    assert "COMMIT" in conn.statements and "ROLLBACK" not in conn.statements


class ScriptedDriver(driver.Driver):
    """iterate() plays a script: an Exception instance is raised, anything else returned.
    Stops when the script runs out; connect() and sleep() are only counted."""

    def __init__(self, script, **overrides):
        base = make_driver(**overrides)
        self.__dict__.update(base.__dict__)
        self.script = list(script)
        self.connects = 0
        self.sleeps = []

    def connect(self):
        self.connects += 1

    def iterate(self):
        if not self.script:
            self.stop_requested = True
            return None
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def explain_idle(self):
        return 0

    def wait_for_event(self):
        pass

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def retry(first=101, last=110, message="not there yet"):
    return driver.RetryLater((first, last), message)


def test_loop_waits_quietly_on_one_connection(logged):
    d = ScriptedDriver([retry(), retry(), retry(), (101, 110), (111, 120)], poll_interval=0.5)
    assert d.run() == 0
    assert d.connects == 1, "a retry-later must not reconnect"
    assert d.sleeps == [0.5, 0.5, 0.5]
    waiting = [line for line in logged if "not ready" in line]
    assert waiting == ["blocks 101..110 not ready, retrying every 0.5s: not there yet"]
    assert len([line for line in logged if line.startswith("resumed after")]) == 1
    assert not [line for line in logged if "database connection problem" in line or "reconnecting" in line]


def test_each_wait_logs_once(logged):
    d = ScriptedDriver([retry(), retry(), (101, 110), retry(111, 120), retry(111, 120), (111, 120)])
    d.run()
    assert len([line for line in logged if "not ready" in line]) == 2
    assert len([line for line in logged if line.startswith("resumed after")]) == 2


def test_connection_errors_still_reconnect(logged):
    dropped = pg_error(psycopg2.OperationalError, None, "server closed the connection")
    d = ScriptedDriver([retry(), dropped, (101, 110)])
    d.run()
    assert d.connects == 2
    assert d.sleeps == [1.0, 5.0], "the retry-later poll, then the reconnect delay"
    assert any("database connection problem" in line for line in logged)


def test_other_errors_still_end_the_driver():
    failed = pg_error(psycopg2.InternalError, "P0001", "app bug")
    d = ScriptedDriver([retry(), failed, (101, 110)])
    with pytest.raises(psycopg2.InternalError):
        d.run()
    assert d.connects == 1


def test_stop_during_a_wait(logged):
    d = ScriptedDriver([retry(), retry()])
    d.sleep = lambda seconds: setattr(d, "stop_requested", True)
    assert d.run() == 0
    assert d.connects == 1
    assert logged[-1] == "stopped"
