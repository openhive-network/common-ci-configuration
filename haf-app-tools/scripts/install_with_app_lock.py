#!/usr/bin/env python3
"""Hold a HAF app advisory install lock while running an installer command.

Usage:
    install_with_app_lock.py APP_NAME DSN CMD [ARG ...]

Tries to acquire the EXCLUSIVE advisory install lock for APP_NAME using DSN
(via hive.try_acquire_app_install_lock from the hive_fork_manager extension).

- If the lock is held by another session (typically a block-processor holding
  the shared lock), this prints the holder description (forwarded from PG
  NOTICE) and exits 0 so docker compose sees `service_completed_successfully`
  without running the destructive install steps.
- If the lock is acquired, runs CMD with ARGs as a subprocess. The lock is
  held for the lifetime of THIS process via the psycopg connection, so all
  sub-step `psql` invocations inside CMD are atomic with respect to BPs.
  The lock is released when this process exits (cleanly, on signal, or on
  crash) because the psycopg connection is dropped.
"""
import logging
import subprocess
import sys

import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  install_with_app_lock  %(message)s",
)
log = logging.getLogger(__name__)


def forward_notices(conn):
    """Drain conn.notices into the logger and clear the deque."""
    while conn.notices:
        notice = conn.notices.pop(0).rstrip()
        if notice:
            log.info("%s", notice)


def main():
    if len(sys.argv) < 4:
        log.error("usage: %s APP_NAME DSN CMD [ARG ...]", sys.argv[0])
        return 2

    app_name = sys.argv[1]
    dsn = sys.argv[2]
    install_cmd = sys.argv[3:]

    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("SET application_name = %s", (f"{app_name}-install",))
        cur.execute("SELECT hive.try_acquire_app_install_lock(%s)", (app_name,))
        acquired = cur.fetchone()[0]

    # Forward the NOTICE from the function (the holder description, on failure)
    forward_notices(conn)

    if not acquired:
        log.info("Skipping %s install — lock held by an active block-processor.", app_name)
        conn.close()
        return 0

    log.info("Acquired install lock for %s; running install command.", app_name)
    try:
        result = subprocess.run(install_cmd)
        return result.returncode
    finally:
        # Closing the connection releases the session-scoped advisory lock.
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
