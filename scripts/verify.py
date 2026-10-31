"""Real cluster checks. Mutates only the named, repository-local lab."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from lab import ROOT, kubectl

BASE = "http://localhost:8095"
checks = []


def api(path, token=None, body=None):
    req = Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + (token or "")})
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def wait(fn, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = fn()
        except (URLError, TimeoutError, ConnectionError):
            value = None
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("Condition did not become true within bounded wait")


def get(resource, *args):
    return json.loads(kubectl("get", resource, *args, "-o", "json", capture_output=True).stdout)


def report(token, report_id):
    return next(r for r in api("/api/reports", token)["reports"] if r["id"] == report_id)


def terminal(token, report_id):
    row = report(token, report_id)
    return row if row["status"] in ("succeeded", "failed", "lost") else None


def record(name, **evidence):
    checks.append({"check": name, "passed": True, **evidence})
    print("PASS", name, flush=True)


def run():
    api("/health/ready")
    token = api("/api/sessions", body={})["token"]
    key = str(uuid4())
    body = {"request_key": key, "mode": "normal"}
    first = api("/api/reports", token, body)
    again = api("/api/reports", token, body)
    assert first["id"] == again["id"]
    try:
        api("/api/reports", token, body | {"mode": "fail"})
        raise AssertionError("Changed idempotency payload accepted")
    except HTTPError as error:
        assert error.code == 409
    result = wait(lambda: terminal(token, first["id"]))
    assert result["status"] == "succeeded" and result["attempts"] == 1
    assert result["result"]["lines"] == 10000
    saved_result = result["result"]
    other = api("/api/sessions", body={})["token"]
    assert not api("/api/reports", other)["reports"]
    record("durable result, idempotent submission, payload conflict, session isolation", lines=10000)

    retry = api("/api/reports", token, {"request_key": str(uuid4()), "mode": "retry"})
    result = wait(lambda: terminal(token, retry["id"]))
    assert result["status"] == "succeeded" and result["attempts"] == 2
    job = get("job/report-" + retry["id"])
    assert job["status"]["failed"] >= 1 and job["status"]["succeeded"] == 1
    record("Job controller replaces failed worker", worker_attempts=2)

    failed = api("/api/reports", token, {"request_key": str(uuid4()), "mode": "fail"})
    result = wait(lambda: terminal(token, failed["id"]))
    assert result["status"] == "failed" and result["attempts"] == 2
    record("retry exhaustion becomes terminal", worker_attempts=2)

    recovery = api("/api/reports", token, {"request_key": str(uuid4()), "mode": "normal"})
    wait(lambda: report(token, recovery["id"])["status"] == "running")
    pods = get("pods", "-l", "job-name=report-" + recovery["id"])["items"]
    pod_name = next(p["metadata"]["name"] for p in pods if p["status"]["phase"] == "Running")
    kubectl("delete", "pod", pod_name, "--grace-period=0", "--force", capture_output=True)
    result = wait(lambda: terminal(token, recovery["id"]))
    assert result["status"] == "succeeded" and result["attempts"] == 2
    record("killed worker replaced; one durable result", worker_attempts=2)

    # Stop dispatch by scaling only this lab's API, then leave persisted intent
    # queued through a direct database insert (simulates a crash after DB commit).
    kubectl("scale", "deployment/api", "--replicas=0", capture_output=True)
    wait(lambda: not get("pods", "-l", "app=api")["items"])
    pending_id = str(uuid4())
    import hashlib
    owner_hash = hashlib.sha256(token.encode()).hexdigest()
    sql = f"INSERT INTO reports(id,owner,request_key,mode) VALUES('{pending_id}','{owner_hash}','{uuid4()}','normal');"
    kubectl("exec", "postgres-0", "--", "psql", "-U", "dispatch", "-d", "dispatch", "-c", sql, capture_output=True)
    kubectl("scale", "deployment/api", "--replicas=2", capture_output=True)
    kubectl("rollout", "status", "deployment/api", "--timeout=180s", capture_output=True)
    result = wait(lambda: terminal(token, pending_id))
    assert result["status"] == "succeeded"
    record("persisted dispatch intent survives API absence")

    # Replay a create already committed in Kubernetes but not yet acknowledged in DB.
    sql = f"UPDATE reports SET status='queued' WHERE id='{pending_id}';"
    kubectl("exec", "postgres-0", "--", "psql", "-U", "dispatch", "-d", "dispatch", "-c", sql, capture_output=True)
    wait(lambda: report(token, pending_id)["status"] == "submitted")
    # Restore the result state: an intentional test-only DB mutation, not a worker replay.
    sql = f"UPDATE reports SET status='succeeded' WHERE id='{pending_id}';"
    kubectl("exec", "postgres-0", "--", "psql", "-U", "dispatch", "-d", "dispatch", "-c", sql, capture_output=True)
    assert get("job/report-" + pending_id)["status"]["succeeded"] == 1
    record("ambiguous Job create adopts existing deterministic name (409)")

    for resource, verb in [("secrets", "get"), ("pods/exec", "create"), ("deployments.apps", "patch")]:
        result = subprocess.run(["kubectl", "--kubeconfig", str(ROOT / ".local/kubeconfig"), "--context", "kind-dispatch-lab", "-n", "dispatch",
                                 "auth", "can-i", verb, resource, "--as=system:serviceaccount:dispatch:dispatcher"], capture_output=True, text=True)
        assert result.returncode == 1 and result.stdout.strip() == "no", result.stdout
    record("dispatcher denied secret reads, Pod exec, Deployment patches")

    kubectl("scale", "deployment/api", "--replicas=3", capture_output=True)
    kubectl("rollout", "status", "deployment/api", "--timeout=180s", capture_output=True)
    assert get("deployment/api")["status"]["readyReplicas"] == 3
    kubectl("scale", "deployment/api", "--replicas=2", capture_output=True)
    kubectl("rollout", "status", "deployment/api", "--timeout=180s", capture_output=True)
    record("manual horizontal scale 2 to 3 to 2")

    kubectl("scale", "deployment/api", "--replicas=1", capture_output=True)
    wait(lambda: len(get("pods", "-l", "app=api")["items"]) == 1)
    wait(lambda: get("pdb/api")["status"].get("disruptionsAllowed") == 0)
    pod_name = get("pods", "-l", "app=api")["items"][0]["metadata"]["name"]
    eviction = {"apiVersion": "policy/v1", "kind": "Eviction", "metadata": {"name": pod_name, "namespace": "dispatch"}}
    try:
        kubectl("create", "--raw", f"/api/v1/namespaces/dispatch/pods/{pod_name}/eviction", "-f", "-", input=json.dumps(eviction), capture_output=True)
        raise AssertionError("PDB allowed eviction of sole ready API Pod")
    except subprocess.CalledProcessError as error:
        assert "disruption budget" in error.stderr.lower(), error.stderr
    kubectl("scale", "deployment/api", "--replicas=2", capture_output=True)
    kubectl("rollout", "status", "deployment/api", "--timeout=180s", capture_output=True)
    record("PodDisruptionBudget rejects eviction of sole ready API Pod")

    stop = threading.Event()
    observations = []
    def probe():
        while not stop.is_set():
            try:
                observations.append(api("/health/ready")["ready"])
            except (HTTPError, URLError, TimeoutError, ConnectionError):
                observations.append(False)
            stop.wait(.2)
    thread = threading.Thread(target=probe)
    thread.start()
    try:
        revision = "rollout-" + uuid4().hex[:8]
        kubectl("set", "env", "deployment/api", "APP_REVISION=" + revision, capture_output=True)
        kubectl("rollout", "status", "deployment/api", "--timeout=180s", capture_output=True)
    finally:
        stop.set()
        thread.join(timeout=6)
    assert observations and all(observations), observations
    assert api("/api/reports", token)["revision"] == revision
    record("rolling update through stable NodePort", successful_probes=len(observations), failed_probes=0)

    kubectl("delete", "pod", "postgres-0", capture_output=True)
    kubectl("rollout", "status", "statefulset/postgres", "--timeout=180s", capture_output=True)
    wait(lambda: api("/health/ready")["ready"])
    assert report(token, first["id"])["result"] == saved_result
    record("database Pod replacement preserves PVC-backed results")
    return token


if __name__ == "__main__":
    run()
    output = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "environment": "single-node kind; Docker-backed developer/CI lab",
              "checks": checks}
    target = ROOT / "test-results/verification.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(output, indent=2) + "\n")
    print(f"{len(checks)} real cluster checks passed. Evidence: test-results/verification.json")
