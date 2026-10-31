# Operate the local lab

Run commands from the repository root. `python` means a working Python 3.12+
interpreter; on Windows `py -3` or your virtualenv's `Scripts/python.exe` may be
needed when the Microsoft Store alias is enabled.

## Start and inspect

```sh
python scripts/lab.py up
python scripts/lab.py status
python scripts/lab.py kubectl get events --sort-by=.lastTimestamp
python scripts/lab.py kubectl logs deployment/api
python scripts/lab.py kubectl describe resourcequota lab-budget
```

Only this repository's kubeconfig/context is used. `.local/kubeconfig` contains
cluster credentials; keep it private. The script refuses to adopt an existing
named cluster when this file is absent. Do not move the repository while the
cluster is running, share its credentials, or manually overwrite this file.

If 8095 is occupied, stop the conflicting service you own or consistently edit
the kind host mapping and the verification/browser URLs before creating the lab.
Do not delete somebody else's container to free the port.

## Run the three scenarios

Open http://localhost:8095. **Clean run** succeeds after a short visible execution
period. **Fail, then recover** intentionally exits the first worker with code 1;
the Job controller starts a replacement after backoff. **Exhaust the budget**
fails both attempts; the report becomes terminal when Kubernetes marks the Job
failed. Allow up to two minutes for recovery and controller observations.

The report ledger polls the API, not the Kubernetes API. Expand a successful
result to inspect exact integer-cent regional totals. A new browser session
sees only its own reports. Closing the tab loses that session's capability;
database records remain. There is no recovery/login workflow in this lab.

## Verify real behavior

```sh
uv sync --locked --extra test
uv run --locked --extra test python -m pytest -q
python scripts/verify.py
```

For the dashboard checks, install Node 24+ and then run:

```sh
npm ci
npx playwright install chromium
npm run test:e2e
```

On Linux, `npx playwright install --with-deps chromium` also installs the browser's
system libraries and may require sudo. Browser artifacts use
`test-results/browser/`; they do not clear the cluster verification report.

The cluster check deliberately deletes one worker Pod, briefly scales the API
to zero, inserts one controlled queued row to model a crash after acceptance,
scales up, tests a repeated Job create, exercises real RBAC denials, scales
replicas, tests an Eviction API denial, rolls out a revision while sampling HTTP,
and replaces the database Pod. Run it without other experiments in progress.
Its fault-injection SQL uses generated UUIDs and synthetic records only.

The 409 check explicitly requeues an existing report in the test database and
then restores its completed status. It tests deterministic Job adoption, not a
claim to have interrupted the API at the precise commit instruction.

Output goes to ignored `test-results/verification.json` only after all checks
pass. The committed [verification report](verification.json) is a recorded run,
not live monitoring. It includes the tested source commit and environment.
CI publishes fresh results as an artifact.

## Diagnose a queued report

```sh
python scripts/lab.py kubectl get jobs
python scripts/lab.py kubectl describe resourcequota lab-budget
python scripts/lab.py kubectl get events --sort-by=.lastTimestamp
python scripts/lab.py kubectl logs deployment/api
```

The API can retain queued intent while Kubernetes is unreachable or denies new
Jobs due to quota. Completed Jobs consume quota until their five-minute TTL
expires. Retry exhaustion requires a new report request, not unlimited redrive.
Do not delete a submitted Job as a retry mechanism: the API records `lost` when
its outcome cannot be safely inferred. Investigate the result row and original
Job/Pod events first. Infrastructure errors are logged without credential details.

## Scale and roll out

```sh
python scripts/lab.py kubectl scale deployment/api --replicas=3
python scripts/lab.py kubectl rollout status deployment/api --timeout=180s
python scripts/lab.py kubectl set env deployment/api APP_REVISION=demo-v2
python scripts/lab.py kubectl rollout status deployment/api --timeout=180s
python scripts/lab.py kubectl scale deployment/api --replicas=2
```

`up` rebuilds the fixed local image tag and restarts API Pods. Existing Jobs retain
their worker image reference; this local workflow is not an immutable release
strategy. For production, use content-addressed images and versioned manifests.
Manual scaling through the Deployment API is not constrained by its disruption
budget. To show the budget, use the real eviction check in `verify.py`.

## Stop and data loss

There is no cloud bill or public deployment. The cluster runs as one Docker
container. Keep it for later with Docker running, or delete it deliberately:

```sh
python scripts/lab.py down
```

This removes the named cluster and its local database data. Rebuilding the
application preserves data; deleting the cluster does not. No backup or restore
process is implemented or verified.
