from contextlib import contextmanager
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from app import main


@pytest.mark.parametrize("missing", [False, True])
def test_reconcile_existing_jobs_even_when_new_job_quota_is_exhausted(monkeypatch, missing):
    active, queued = uuid4(), uuid4()
    updates = []

    class Database:
        def execute(self, query, params=None):
            if query.startswith("UPDATE"):
                updates.append(params)
            return self

        def fetchall(self):
            return [{"id": active}]

        def fetchone(self):
            return {"id": queued}

    @contextmanager
    def connection():
        yield Database()

    class Cluster:
        def get(self, report_id):
            assert report_id == active
            if missing:
                return 404, None
            return 200, {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}

        def create(self, report_id):
            assert report_id == queued
            raise HTTPError("https://cluster/jobs", 403, "Job quota exhausted", {}, None)

    monkeypatch.setattr(main, "connection", connection)
    with pytest.raises(HTTPError):
        main.reconcile(Cluster())
    assert len(updates) == 1
    assert updates[0][0] == ("lost" if missing else "failed")
    assert updates[0][2] == active
