import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connection():
    # shortcut: shared bootstrap DB role in this local lab; restrict roles before public deployment.
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=3) as conn:
        conn.execute("SET statement_timeout = '5s'")
        yield conn


def initialize():
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(78102)")
        conn.execute("""CREATE TABLE IF NOT EXISTS reports (
            id uuid PRIMARY KEY, owner text NOT NULL, request_key uuid NOT NULL,
            mode text NOT NULL CHECK (mode IN ('normal','retry','fail')),
            status text NOT NULL DEFAULT 'queued', attempts integer NOT NULL DEFAULT 0,
            result jsonb, last_error text,
            created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
            UNIQUE(owner, request_key))""")
        conn.execute("CREATE INDEX IF NOT EXISTS reports_active ON reports(status, created_at)")
