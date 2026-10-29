"""haf_app_driver.py: a range processor raising SQLSTATE 55R23 (RETRY_LATER_SQLSTATE).

The iteration is rolled back and retried on the same connection, after --poll-interval
seconds doubling up to --retry-delay, with a log line when the wait starts, one every
STILL_WAITING_LOG_INTERVAL, and one when a range next goes through. Every other error keeps
its old handling: OperationalError/InterfaceError reconnect, anything else ends the driver.

The connection is a scripted fake, so this needs no PostgreSQL; psycopg2 is replaced by a
stand-in while the driver is loaded when it is not installed (the test-tools image has none).
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

try:
    import psycopg2  # noqa: F401
    import psycopg2.extensions  # noqa: F401
    _STAND_IN = False
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
    _STAND_IN = True

SCRIPT = Path(__file__).resolve().parent.parent / "haf-app-tools" / "scripts" / "haf_app_driver.py"
_spec = importlib.util.spec_from_file_location("haf_app_driver", SCRIPT)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)
psycopg2 = driver.psycopg2  # the real module, or the stand-in
if _STAND_IN:
    # only the driver keeps it: a later test importing psycopg2 still sees it missing
    del sys.modules["psycopg2"], sys.modules["psycopg2.extensions"]


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
        self.notifies = []

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


@pytest.fixture
def clock(monkeypatch):
    """The driver's monotonic clock, advanced only by the scripted driver's sleep()."""
    now = types.SimpleNamespace(t=1000.0)
    monkeypatch.setattr(driver, "time", types.SimpleNamespace(monotonic=lambda: now.t))
    return now


def test_sqlstate_constant_is_class_55():
    # class 55 is what makes an older driver retry it too (psycopg2: OperationalError)
    assert driver.RETRY_LATER_SQLSTATE == "55R23"
    assert not issubclass(driver.RetryLater, psycopg2.Error), "run() must not take it for a lost connection"


def test_retry_later_rolls_back_and_names_the_range():
    error = pg_error(psycopg2.OperationalError, "55R23", "app: dependency not at block 110")
    conn = FakeConn(procedure_error=error)
    with pytest.raises(driver.RetryLater) as raised:
        make_driver(conn).iterate()
    assert raised.value.blocks == (101, 110)
    assert raised.value.message == "app: dependency not at block 110"
    assert raised.value.__cause__ is error
    assert conn.statements[-1] == "ROLLBACK"
    assert "COMMIT" not in conn.statements


def test_retry_later_from_a_python_processor():
    conn = FakeConn()
    d = make_driver(conn)

    def processor(_conn, first, last):
        raise pg_error(psycopg2.OperationalError, "55R23", f"not at {last}")

    d.process_python = processor
    with pytest.raises(driver.RetryLater) as raised:
        d.iterate()
    assert raised.value.blocks == (101, 110) and raised.value.message == "not at 110"
    assert conn.statements[-1] == "ROLLBACK"


def test_message_falls_back_without_diag():
    class Custom(Exception):
        pgcode = "55R23"

    conn = FakeConn(procedure_error=Custom("  plain text  "))
    with pytest.raises(driver.RetryLater) as raised:
        make_driver(conn).iterate()
    assert raised.value.message == "plain text"


@pytest.mark.parametrize("base, pgcode", [
    (psycopg2.InternalError, "P0001"),          # an application error: ends the driver
    (psycopg2.OperationalError, None),          # a dropped connection: run() reconnects
    (psycopg2.OperationalError, "55000"),       # other class-55 codes keep the old handling
    (psycopg2.OperationalError, "55P03"),
    (psycopg2.OperationalError, "55R22"),
])
def test_other_errors_are_not_retry_later(base, pgcode):
    error = pg_error(base, pgcode, "something else")
    conn = FakeConn(procedure_error=error)
    with pytest.raises(base) as raised:
        make_driver(conn).iterate()
    assert raised.value is error
    assert conn.statements[-1] == "ROLLBACK"


def test_55r23_outside_the_processor_is_not_retry_later():
    # only a range the processor was handed is retried this way
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
    Stops when the script runs out; connect() is counted, sleep() recorded and, when a clock
    is given, advances it."""

    def __init__(self, script, clock=None, **overrides):
        base = make_driver(FakeConn(), **overrides)
        self.__dict__.update(base.__dict__)
        self.script = list(script)
        self.clock = clock
        self.connects = 0
        self.sleeps = []

    def connect(self):
        self.connects += 1
        if self.conn is None:  # run() closed it to reconnect
            self.conn = FakeConn()

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
        if self.clock is not None:
            self.clock.t += seconds


def retry(first=101, last=110, message="not there yet"):
    return driver.RetryLater((first, last), message)


def lines(logged, text):
    return [line for line in logged if text in line]


def test_loop_waits_quietly_on_one_connection(logged):
    d = ScriptedDriver([retry(), retry(), retry(), (101, 110), (111, 120)], poll_interval=0.5)
    assert d.run() == 0
    assert d.connects == 1, "a retry-later must not reconnect"
    assert lines(logged, "not ready") == ["blocks 101..110 not ready, retrying: not there yet"]
    assert len(lines(logged, "range ready after")) == 1
    assert not lines(logged, "database connection problem") and not lines(logged, "reconnecting")


def test_retries_back_off_to_retry_delay(logged):
    d = ScriptedDriver([retry()] * 6 + [(101, 110), retry(111, 120), (111, 120)],
                       poll_interval=0.5, retry_delay=3.0)
    d.run()
    assert d.sleeps == [0.5, 1.0, 2.0, 3.0, 3.0, 3.0, 0.5], "doubling, capped, and reset by the next wait"


def test_each_wait_logs_its_start_and_end(logged):
    d = ScriptedDriver([retry(), retry(), (101, 110), retry(111, 120), retry(111, 120), (111, 120)])
    d.run()
    assert len(lines(logged, "not ready")) == 2
    assert len(lines(logged, "range ready after")) == 2


def test_no_range_does_not_end_the_wait(logged):
    # an iteration that hands out nothing (paused, gated, a fork) is not "a range went through"
    d = ScriptedDriver([retry(), None, retry(), (101, 110)])
    d.run()
    assert len(lines(logged, "not ready")) == 1
    assert len(lines(logged, "range ready after")) == 1
    assert logged.index(lines(logged, "range ready after")[0]) == len(logged) - 2  # then "stopped"


def test_a_long_wait_logs_now_and_then(logged, clock):
    d = ScriptedDriver([retry()] * 400 + [(101, 110)], clock=clock, poll_interval=1.0, retry_delay=5.0)
    d.run()
    waited = sum(d.sleeps)
    still = lines(logged, "still not ready after")
    assert len(still) == int(waited // driver.STILL_WAITING_LOG_INTERVAL), (waited, still)
    assert still and still[0].startswith("blocks 101..110 still not ready after 60")


def test_the_live_summary_is_flushed_before_the_wait(logged):
    d = ScriptedDriver([retry(), (101, 110)])
    d.live_blocks, d.live_first, d.live_last = 3, 98, 100
    d.run()
    assert logged[0].startswith("live: 3 block(s) 98..100")
    assert "not ready" in logged[1]


def test_notifies_received_during_a_wait_are_dropped():
    d = ScriptedDriver([retry(), retry(), (101, 110)])
    d.conn.notifies.extend(["haf_new_block"] * 5)
    d.sleep = lambda seconds: d.conn.notifies.append("haf_new_block")
    d.run()
    assert len(d.conn.notifies) <= 1


def test_connection_errors_still_reconnect(logged):
    dropped = pg_error(psycopg2.OperationalError, None, "server closed the connection")
    d = ScriptedDriver([retry(), dropped, retry(), (101, 110)])
    d.run()
    assert d.connects == 2
    assert d.sleeps == [1.0, 5.0, 2.0], "the first retry, the reconnect delay, then the same wait's next retry"
    assert lines(logged, "database connection problem")
    assert len(lines(logged, "not ready")) == 1, "a reconnect does not start a new wait"
    assert len(lines(logged, "range ready after")) == 1


def test_other_errors_still_end_the_driver():
    failed = pg_error(psycopg2.InternalError, "P0001", "app bug")
    d = ScriptedDriver([retry(), failed, (101, 110)])
    with pytest.raises(psycopg2.InternalError):
        d.run()
    assert d.connects == 1


def test_stop_during_a_wait(logged):
    d = ScriptedDriver([retry()] * 50)

    def sleep(seconds):
        d.sleeps.append(seconds)
        d.stop_requested = True

    d.sleep = sleep
    assert d.run() == 0
    assert d.connects == 1
    assert len(d.sleeps) == 1 and len(d.script) == 49, "the stop is honoured after the current sleep"
    assert logged[-1] == "stopped"
