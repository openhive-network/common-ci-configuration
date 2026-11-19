"""haf_app_driver.py: every server notice a call raises reaches the log.

psycopg2 truncates its default conn.notices list to the last 50 entries; connect() replaces it
with an unbounded deque, which psycopg2 appends to without truncating. The connection here is
a fake that behaves that way, so this needs no PostgreSQL; psycopg2 is replaced by a stand-in
while the driver is loaded when it is not installed (the test-tools image has none).
"""

import importlib.util
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_STAND_IN = importlib.util.find_spec("psycopg2") is None
if _STAND_IN:
    _pg = types.ModuleType("psycopg2")
    _pg_extensions = types.ModuleType("psycopg2.extensions")
    setattr(_pg, "Error", type("Error", (Exception,), {}))
    setattr(_pg, "extensions", _pg_extensions)
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg_extensions

SCRIPT = Path(__file__).resolve().parent.parent / "haf-app-tools" / "scripts" / "haf_app_driver.py"
_spec = importlib.util.spec_from_file_location("haf_app_driver_notices", SCRIPT)
assert _spec is not None and _spec.loader is not None
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)
if _STAND_IN:
    del sys.modules["psycopg2"], sys.modules["psycopg2.extensions"]

PSYCOPG2_LIST_LIMIT = 50


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self.result: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: object = None) -> None:
        if sql.startswith("SELECT contexts"):
            self.result = (["app"], "app.process", False)
        elif sql.startswith("SELECT to_regprocedure"):
            self.result = (True,)
        elif sql.startswith("SELECT hive.app_get_current_block_num"):
            for notice in self.conn.raised:
                self.conn.notice(notice)
            self.result = (100,)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result


class FakeConn:
    """Collects notices as psycopg2 2.7+ does: appended to whatever conn.notices is, and a
    list (the default) trimmed to its last 50 entries."""

    def __init__(self, raised: list[str]) -> None:
        self.raised = raised
        self.notices: Any = []
        self.autocommit = False

    def notice(self, text: str) -> None:
        self.notices.append(text)
        if isinstance(self.notices, list) and len(self.notices) > PSYCOPG2_LIST_LIMIT:
            del self.notices[:-PSYCOPG2_LIST_LIMIT]

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def make_driver(server_messages: str = "INFO") -> Any:
    args = types.SimpleNamespace(
        app="app", server_messages=server_messages, process_python=None, lock=None,
        postgres_url="postgresql://unused",
    )
    return driver.Driver(args)


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> list[FakeConn]:
    """Each psycopg2.connect() returns the next FakeConn; the test appends them."""
    queue: list[FakeConn] = []
    monkeypatch.setattr(driver.psycopg2, "connect", lambda url: queue.pop(0), raising=False)
    return queue


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(driver, "emit", lines.append)
    ignore: Callable[[str], None] = lambda msg: None
    monkeypatch.setattr(driver, "log", ignore)
    return lines


def messages(lines: list[str]) -> list[str]:
    """The emitted lines without their leading drain timestamp (continuations kept as-is)."""
    return [line.split(" ", 1)[1] if not line.startswith(" ") else line for line in lines]


def test_every_notice_of_one_call_is_emitted_in_order(connections: list[FakeConn], emitted: list[str]) -> None:
    raised = [f"INFO:  finalize step {i}\n" for i in range(200)]
    connections.append(FakeConn(raised))
    d = make_driver()
    d.connect()
    assert messages(emitted) == [f"INFO:  finalize step {i}" for i in range(200)]
    assert not d.conn.notices, "drained notices are cleared"


def test_level_filter_and_multi_line_formatting(connections: list[FakeConn], emitted: list[str]) -> None:
    raised: list[str] = []
    for i in range(100):
        raised += [f"LOG:  chatter {i}\n", f"NOTICE:  step {i}\ndetail {i}\n\n", f"WARNING:  slow {i}\n"]
    connections.append(FakeConn(raised))
    d = make_driver(server_messages="NOTICE")
    d.connect()
    expected: list[str] = []
    for i in range(100):
        expected += [f"NOTICE:  step {i}", f"    detail {i}", f"WARNING:  slow {i}"]
    assert messages(emitted) == expected


def test_a_reconnect_is_uncapped_again(connections: list[FakeConn], emitted: list[str]) -> None:
    connections.append(FakeConn([f"INFO:  first {i}\n" for i in range(120)]))
    connections.append(FakeConn([f"INFO:  second {i}\n" for i in range(120)]))
    d = make_driver()
    d.connect()
    first = d.conn
    d.conn = None  # as run() does before reconnecting
    d.connect()
    assert d.conn is not first and d.conn.notices is not first.notices
    assert messages(emitted) == (
        [f"INFO:  first {i}" for i in range(120)] + [f"INFO:  second {i}" for i in range(120)]
    )
