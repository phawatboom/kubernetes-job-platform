"""A Job may execute again. The report ID, not the Pod, identifies its effect."""
import sys
import time
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db import connection


def calculate():
    # Generated order lines: no private, licensed, or external dataset.
    totals = {region: {"lines": 0, "revenue_cents": 0} for region in ("North", "East", "South", "West")}
    for index in range(10000):
        region = tuple(totals)[index % 4]
        totals[region]["lines"] += 1
        totals[region]["revenue_cents"] += (100 + index % 9900) * (1 + index % 5)
    return {"source": "synthetic-v1", "lines": 10000, "regions": totals,
            "revenue_cents": sum(row["revenue_cents"] for row in totals.values())}


def run(report_id):
    with connection() as lock_conn:
        # A session lock survives commits but is released when a killed Pod disconnects.
        acquired = lock_conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (str(report_id),)).fetchone()["ok"]
        if not acquired:
            return 1
        with connection() as conn:
            report = conn.execute("SELECT * FROM reports WHERE id = %s FOR UPDATE", (report_id,)).fetchone()
            if report is None:
                return 1
            if report["status"] == "succeeded":
                return 0
            attempt = report["attempts"] + 1
            conn.execute("UPDATE reports SET attempts=%s, status='running' WHERE id=%s", (attempt, report_id))
        if report["mode"] == "fail" or (report["mode"] == "retry" and attempt == 1):
            with connection() as conn:
                conn.execute("UPDATE reports SET last_error='Injected worker failure' WHERE id=%s", (report_id,))
            return 1
        # Make replacement observable; this is not a CPU benchmark.
        time.sleep(5)
        result = calculate()
        with connection() as conn:
            conn.execute("UPDATE reports SET status='succeeded', result=%s, finished_at=now(), last_error=NULL WHERE id=%s",
                         (Jsonb(result), report_id))
        return 0


if __name__ == "__main__":
    sys.exit(run(UUID(sys.argv[1])))
