import hashlib
import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from typing import Literal

from app.db import connection, initialize
from app.kube import Kubernetes

log = logging.getLogger("dispatch")


def reconcile(kube):
    # Deterministic names bridge the DB/cluster commit gap. Multiple API replicas
    # claim separate rows; 409 means the previous create already took effect.
    with connection() as conn:
        row = conn.execute("SELECT id FROM reports WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if row:
            code, _ = kube.create(row["id"])
            if code not in (201, 409):
                raise RuntimeError("Unexpected Job create response")
            conn.execute("UPDATE reports SET status='submitted' WHERE id=%s", (row["id"],))
    with connection() as conn:
        rows = conn.execute("SELECT id FROM reports WHERE status IN ('submitted','running') LIMIT 10").fetchall()
    for row in rows:
        code, job = kube.get(row["id"])
        conditions = [] if job is None else job.get("status", {}).get("conditions", [])
        failed = any(c["type"] == "Failed" and c["status"] == "True" for c in conditions)
        if code == 404 or failed:
            with connection() as conn:
                conn.execute("""UPDATE reports SET status=%s, finished_at=now(),
                    last_error=COALESCE(last_error, %s) WHERE id=%s AND status IN ('submitted','running')""",
                    ("lost" if code == 404 else "failed", "Job missing; operator reconciliation required" if code == 404 else "Job retry budget or deadline exhausted", row["id"]))


@asynccontextmanager
async def lifespan(app):
    initialize()
    kube = Kubernetes()
    stopped = threading.Event()

    def dispatch():
        while not stopped.is_set():
            try:
                reconcile(kube)
            except Exception:
                # Keep sensitive database URLs and HTTP headers out of logs.
                log.warning("Reconciliation unavailable; persisted intent will be retried")
            stopped.wait(1)

    worker = threading.Thread(target=dispatch, daemon=True)
    worker.start()
    yield
    stopped.set()
    worker.join(timeout=8)


app = FastAPI(title="Dispatch report platform", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


def owner(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Start a session")
    token = authorization[7:]
    if len(token) != 43 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in token):
        raise HTTPException(401, "Invalid session")
    return hashlib.sha256(token.encode()).hexdigest()


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    mode: Literal["normal", "retry", "fail"] = "normal"


@app.post("/api/sessions")
def session():
    return {"token": secrets.token_urlsafe(32)}


@app.post("/api/reports", status_code=202)
def submit(body: Submission, authorization: str | None = Header(default=None)):
    scope = owner(authorization)
    with connection() as conn:
        # Namespace-wide admission is deliberately bounded, even across replicas.
        conn.execute("SELECT pg_advisory_xact_lock(78103)")
        prior = conn.execute("SELECT * FROM reports WHERE owner=%s AND request_key=%s", (scope, body.request_key)).fetchone()
        if prior:
            if prior["mode"] != body.mode:
                raise HTTPException(409, "Request key already used with different inputs")
            return prior
        active = conn.execute("SELECT count(*) AS n FROM reports WHERE status IN ('queued','submitted','running')").fetchone()["n"]
        if active >= 6:
            raise HTTPException(429, "Six reports already active; wait for completion")
        return conn.execute("INSERT INTO reports(id,owner,request_key,mode) VALUES(%s,%s,%s,%s) RETURNING *",
                            (uuid4(), scope, body.request_key, body.mode)).fetchone()


@app.get("/api/reports")
def reports(authorization: str | None = Header(default=None)):
    scope = owner(authorization)
    with connection() as conn:
        rows = conn.execute("SELECT * FROM reports WHERE owner=%s ORDER BY created_at DESC LIMIT 30", (scope,)).fetchall()
    return {"reports": rows, "revision": os.environ.get("APP_REVISION", "v1")}


@app.get("/health/live")
def live():
    return {"live": True}


@app.get("/health/ready")
def ready(response: Response):
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
    except Exception:
        response.status_code = 503
        return {"ready": False}
    return {"ready": True, "revision": os.environ.get("APP_REVISION", "v1")}


@app.get("/")
def index():
    return FileResponse("web/index.html")


app.mount("/assets", StaticFiles(directory="web"), name="assets")
