#!/usr/bin/env python3
"""
haf_app_driver.py - generic block-processing driver for HAF applications.

Replaces the eternal `CALL <app>.main()` loop (haf#341). One process per
application, one connection:

    LISTEN haf_new_block / haf_new_irreversible            (once)
    loop:
        BEGIN
        CALL hive.app_next_iteration(contexts, NULL, batch, limit, _wait => FALSE)
        if a range came back: CALL <process_procedure>(range)
        COMMIT                                             (range + position atomically)
        SELECT hive.app_perform_maintenance(contexts)      (shadow-table vacuum, own connection)
        if no range: idle on the socket until a NOTIFY or the poll interval

Because the process idles on the client side, the backend shows as idle in
pg_stat_activity, notifications are consumed (no re-signalling spin), and no
transaction is ever held open across a wait. Which contexts to move and which
procedure to call come from the application registry (hafd.applications,
hive.app_register); a paused application (hive.app_pause) simply gets no ranges.

Requires the application to be registered with a process procedure taking
( hive.blocks_range ). Uses only psycopg2 (as shipped in the psql image).
"""

import argparse
import datetime
import os
import select
import signal
import sys
import time

import psycopg2
import psycopg2.extensions

NOTIFY_CHANNELS = ("haf_new_block", "haf_new_irreversible")
SERVER_LEVELS = {"DEBUG": 0, "LOG": 1, "INFO": 2, "NOTICE": 3, "WARNING": 4}


LOG_FILE = None  # opened by main() when --log-file is given


def emit(line):
    """Write one line to stdout and, when configured, the log file (appended,
    line-buffered; logrotate's copytruncate works with O_APPEND writers)."""
    if not line.endswith("\n"):
        line += "\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    if LOG_FILE is not None:
        LOG_FILE.write(line)
        LOG_FILE.flush()


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    emit(f"{ts} {msg}")


def parse_range(text):
    """'(12,34)' -> (12, 34). NULL and an all-NULL composite '(,)' (what an empty
    iteration yields through SELECT ... INTO in PL/pgSQL, where it tests IS NULL)
    both mean "nothing to process" -> None."""
    if text is None:
        return None
    first, last = text.strip("()").split(",")
    if first == "" or last == "":
        return None
    return int(first), int(last)


class Stop(Exception):
    pass


class Driver:
    def __init__(self, args):
        self.args = args
        self.app = args.app
        self.conn = None
        self.contexts = None
        self.lead = None
        self.procedure = None
        self.stop_requested = False
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_w, False)
        # live-sync log aggregation
        self.live_window_start = time.monotonic()
        self.live_blocks = 0
        self.live_first = None
        self.live_last = None
        self.live_time = 0.0
        self.iterations = 0          # app_next_iteration calls since the last summary
        self.min_server_level = SERVER_LEVELS[args.server_messages]
        self.process_python = None
        self.have_maintenance = False
        self.was_paused = False
        self.was_gated = False

    # -- signals -----------------------------------------------------------
    def request_stop(self, signum, frame):
        if not self.stop_requested:
            log(f"signal {signal.Signals(signum).name} received, stopping after the current iteration")
        self.stop_requested = True
        try:
            os.write(self.wake_w, b"x")
        except BlockingIOError:
            pass

    # -- connection --------------------------------------------------------
    def connect(self):
        self.conn = psycopg2.connect(self.args.postgres_url)
        self.conn.autocommit = True
        cur = self.conn.cursor()
        for channel in NOTIFY_CHANNELS:
            cur.execute(f"LISTEN {channel}")
        cur.execute(
            "SELECT contexts, process_procedure, paused FROM hafd.applications WHERE name = %s",
            (self.app,),
        )
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"application '{self.app}' is not registered (hive.app_register)")
        self.contexts, self.procedure, paused = row
        if self.args.process_python:
            module_name, _, func_name = self.args.process_python.partition(":")
            import importlib
            self.process_python = getattr(importlib.import_module(module_name), func_name or "process_blocks")
        elif self.procedure is None:
            raise SystemExit(
                f"application '{self.app}' is self-driven (no process procedure registered) - "
                "run it with --process-python to supply a client-side range processor"
            )
        self.lead = self.contexts[0]
        if self.args.lock:
            # Shared advisory locks that application installers check before
            # touching the schema (hive.try_acquire_app_install_lock); held by
            # this session until it disconnects, so re-taken on every reconnect.
            # Blocks while an installer holds the exclusive lock.
            cur.execute("SELECT hive.acquire_app_block_processor_locks(%s)", (self.args.lock,))
            self.drain_notices()
        cur.execute("SELECT to_regprocedure('hive.app_perform_maintenance(hive.contexts_group)') IS NOT NULL")
        self.have_maintenance = cur.fetchone()[0]
        cur.execute("SELECT hive.app_get_current_block_num(%s)", (self.lead,))
        current = cur.fetchone()[0]
        self.drain_notices()
        log(
            f"application '{self.app}': contexts {self.contexts}, procedure {self.procedure}, "
            f"current block {current}{' (paused)' if paused else ''}"
            + (f", holding block-processor lock(s) {self.args.lock}" if self.args.lock else "")
        )
        if not self.have_maintenance:
            log("warning: hive.app_perform_maintenance is not available; shadow tables will not be vacuumed")
        self.was_paused = paused

    def drain_notices(self):
        """Server-side RAISE INFO/NOTICE/WARNING from the application. Multi-line
        messages are kept together; empty trailing lines are dropped; messages
        below --server-messages are discarded."""
        if not self.conn.notices:
            return
        for n in self.conn.notices:
            lines = [l.rstrip() for l in n.rstrip("\n").split("\n")]
            lines = [l for l in lines if l]
            if not lines:
                continue
            level = lines[0].split(":", 1)[0].strip().upper()
            if SERVER_LEVELS.get(level, 99) < self.min_server_level:
                continue
            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            emit(f"{ts} {lines[0]}")
            for extra in lines[1:]:
                emit(f"    {extra}")
        del self.conn.notices[:]

    # -- one iteration -----------------------------------------------------
    def iterate(self):
        """Returns the processed range or None."""
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            cur.execute(
                "CALL hive.app_next_iteration(%s::hive.contexts_group, NULL, %s, %s, FALSE)",
                (self.contexts, self.args.override_max_batch, self.args.stop_at_block),
            )
            blocks = parse_range(cur.fetchone()[0])
            self.iterations += 1
            if blocks is not None:
                started = time.monotonic()
                if self.process_python is not None:
                    # client-side range processor: runs inside this transaction on
                    # this connection (may use additional connections of its own);
                    # must not COMMIT the driver's transaction
                    self.process_python(self.conn, blocks[0], blocks[1])
                else:
                    cur.execute(f"CALL {self.procedure}(%s::hive.blocks_range)", (f"({blocks[0]},{blocks[1]})",))
                elapsed = time.monotonic() - started
                # the stage's processing alarm threshold; HAF's own SLOW_PROCESSING
                # check measures the gap between iterations, which for a client
                # that idles between blocks is just the block interval
                cur.execute(
                    "SELECT EXTRACT(EPOCH FROM ((loop).current_stage).processing_alarm_threshold) FROM hafd.contexts WHERE name = %s",
                    (self.lead,),
                )
                row = cur.fetchone()
                threshold = float(row[0]) if row and row[0] is not None else None
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            self.drain_notices()

        if blocks is not None:
            if threshold is not None and elapsed >= threshold:
                log(f"SLOW_PROCESSING: blocks {blocks[0]}..{blocks[1]} took {elapsed:.2f}s (stage threshold {threshold:.0f}s)")
            self.report(blocks, elapsed)
            if self.have_maintenance:
                cur.execute("SELECT hive.app_perform_maintenance(%s::hive.contexts_group)", (self.contexts,))
                if cur.fetchone()[0]:
                    log("shadow tables vacuumed")
                self.drain_notices()
        return blocks

    def report(self, blocks, elapsed):
        first, last = blocks
        count = last - first + 1
        if count > 1:
            # massive-sync batches: one line per batch
            self.flush_live_summary()
            log(f"blocks {first}..{last} ({count}) processed in {elapsed:.2f}s ({elapsed * 1000 / count:.1f} ms/block), "
                f"{self.iterations} iteration(s)")
            self.iterations = 0
            return
        # live sync: aggregate single blocks into a periodic summary
        self.live_blocks += 1
        self.live_first = first if self.live_first is None else self.live_first
        self.live_last = last
        self.live_time += elapsed
        if time.monotonic() - self.live_window_start >= self.args.live_log_interval:
            self.flush_live_summary()

    def flush_live_summary(self):
        if self.live_blocks:
            log(
                f"live: {self.live_blocks} block(s) {self.live_first}..{self.live_last} processed, "
                f"{self.live_time * 1000 / self.live_blocks:.1f} ms/block avg, {self.live_time:.2f}s busy "
                f"in the last {time.monotonic() - self.live_window_start:.0f}s, {self.iterations} iteration(s)"
            )
        self.live_window_start = time.monotonic()
        self.live_blocks = 0
        self.live_first = self.live_last = None
        self.live_time = 0.0
        self.iterations = 0

    # -- idle state ---------------------------------------------------------
    def explain_idle(self):
        """Log transitions into/out of paused / dependency-gated states."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT hive.app_is_paused(%s::hive.contexts_group), hive.app_dependencies_block_limit(%s::hive.contexts_group), "
            "hive.app_get_current_block_num(%s)",
            (self.contexts, self.contexts, self.lead),
        )
        paused, dep_limit, current = cur.fetchone()
        self.drain_notices()
        if paused != self.was_paused:
            log("paused (hive.app_pause); waiting" if paused else "resumed")
            self.was_paused = paused
        gated = dep_limit is not None and dep_limit <= current
        if gated != self.was_gated:
            log(f"waiting for dependencies (they are at block {dep_limit})" if gated else "dependencies caught up")
            self.was_gated = gated
        return current

    def wait_for_event(self):
        # NOTIFY wakes us for new blocks; only dependency progress and resumes are
        # silent, so poll tightly just while gated or paused
        timeout = self.args.poll_interval if (self.was_gated or self.was_paused) else self.args.safety_interval
        rlist, _, _ = select.select([self.conn, self.wake_r], [], [], timeout)
        if self.wake_r in rlist:
            os.read(self.wake_r, 1024)
        if self.conn in rlist:
            self.conn.poll()
            del self.conn.notifies[:]

    # -- main loop ---------------------------------------------------------
    def run(self):
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        if self.args.startup_marker:
            with open(self.args.startup_marker, "w") as f:
                f.write(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00\n"))

        delay = self.args.retry_delay
        while not self.stop_requested:
            try:
                self.connect()
                delay = self.args.retry_delay
                self.loop()
                return 0
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                log(f"database connection problem: {str(e).strip()}")
                self.close()
                if self.stop_requested:
                    return 1
                log(f"reconnecting in {delay}s")
                self.sleep(delay)
                delay = min(delay * 2, self.args.max_retry_delay)
            except Stop:
                return 0
        return 0

    def loop(self):
        idle_checks = 0.0
        while not self.stop_requested:
            blocks = self.iterate()
            if blocks is not None:
                continue
            # nothing to do: explain why (rate limited), check the limit, then idle
            now = time.monotonic()
            if now - idle_checks >= self.args.poll_interval:
                current = self.explain_idle()
                idle_checks = now
                if self.args.stop_at_block is not None and current >= self.args.stop_at_block:
                    self.flush_live_summary()
                    log(f"block limit {self.args.stop_at_block} reached at block {current}, exiting")
                    raise Stop()
            self.wait_for_event()
        self.flush_live_summary()
        log("stopped")

    def sleep(self, seconds):
        select.select([self.wake_r], [], [], seconds)

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


def main():
    p = argparse.ArgumentParser(description="Generic block-processing driver for a registered HAF application.")
    p.add_argument("--app", required=True, help="application name as registered with hive.app_register")
    p.add_argument("--postgres-url", help="PostgreSQL URL (default built from --host/--port/--user)")
    p.add_argument("--host", default=os.environ.get("POSTGRES_HOST", "localhost"))
    p.add_argument("--port", default=os.environ.get("POSTGRES_PORT", "5432"))
    p.add_argument("--user", default=os.environ.get("POSTGRES_USER", "haf_admin"))
    p.add_argument("--database", default="haf_block_log")
    p.add_argument("--stop-at-block", type=int, default=None, help="stop after this block is processed")
    p.add_argument("--lock", action="append", default=[], metavar="APP_LOCK_NAME",
                   help="block-processor advisory lock to hold (hive.acquire_app_block_processor_locks); repeatable")
    p.add_argument("--override-max-batch", type=int, default=None, help="cap the blocks per iteration")
    p.add_argument("--poll-interval", type=float, default=1.0,
                   help="seconds between polls while paused or waiting for a dependency (their progress does not notify)")
    p.add_argument("--safety-interval", type=float, default=15.0,
                   help="seconds to idle without any notification before asking again anyway")
    p.add_argument("--live-log-interval", type=float, default=60.0, help="seconds between live-sync summary lines")
    p.add_argument("--retry-delay", type=float, default=5.0)
    p.add_argument("--max-retry-delay", type=float, default=60.0)
    p.add_argument("--startup-marker", default="/tmp/block_processing_startup_time.txt",
                   help="file stamped with the start time for health checks ('' to disable)")
    p.add_argument("--log-file", default=os.environ.get("LOG_FILE", ""),
                   help="also append all output to this file (env LOG_FILE; empty or STDOUT for none)")
    p.add_argument("--server-messages", default="INFO", choices=list(SERVER_LEVELS),
                   help="lowest server message level to show (RAISE INFO/NOTICE/WARNING)")
    p.add_argument("--process-python", default=None, metavar="MODULE[:FUNCTION]",
                   help="process ranges with a Python callable f(conn, first_block, last_block) "
                        "imported from MODULE (default FUNCTION: process_blocks) instead of the "
                        "registered SQL procedure; for applications whose per-range work involves "
                        "client-side calls (e.g. embedding HTTP requests)")
    args = p.parse_args()
    global LOG_FILE
    if args.log_file and args.log_file != "STDOUT":
        LOG_FILE = open(args.log_file, "a", buffering=1)
    if args.stop_at_block is not None and args.stop_at_block <= 0:
        args.stop_at_block = None
    if not args.postgres_url:
        args.postgres_url = (
            f"postgresql://{args.user}@{args.host}:{args.port}/{args.database}"
            f"?application_name={args.app}_block_processing"
        )
    return Driver(args).run()


if __name__ == "__main__":
    sys.exit(main())
