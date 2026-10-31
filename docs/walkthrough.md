# Five-minute architecture walkthrough

**0:00 — Establish the workload.** Open Dispatch and explain why finite report
execution belongs in Jobs while HTTP serving belongs in a Deployment. Point out
the synthetic dataset and separate request/result lifetime from worker lifetime.

**1:00 — Follow durable intent.** Submit a clean run. Show the queued request,
worker attempt and durable result. In `main.py`, follow acceptance, reconciliation
and deterministic Job creation. Explain the DB/cluster commit gap and why an
existing Job returns 409 rather than creating a second execution identity.

**2:00 — Let the controller recover.** Submit “Fail, then recover.” Inspect
`python scripts/lab.py kubectl get jobs` and Pod events. Explain retry backoff,
the one-retry budget, deadline and finished-Job TTL. The worker session lock
protects result publication; Kubernetes alone does not make external side
effects exactly once. Show retry exhaustion as a separate terminal outcome.

**3:00 — Operate the serving layer.** Show two API Pods, the database-dependent
readiness probe, process-only liveness, resource limits and the Deployment rollout
strategy. Explain the actual scaling, eviction and rolling-update checks in
`scripts/verify.py`, then open the recorded verification report. Zero errors in
sampled local probes is evidence for that run, not an availability SLA.

**4:00 — Defend the boundaries.** Open the Role and worker manifest: API create/get
Jobs, workers no cluster token, restricted security context. Explain that Job
creation can indirectly access namespace Secrets and therefore is a trusted
application permission. Close with the real limits: one node, one database,
no HA/backup, no enforced NetworkPolicy, no HPA or cloud identity. Choose the next
control based on a concrete workload or failure requirement.
