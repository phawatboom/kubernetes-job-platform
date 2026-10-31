# Why a report factory belongs on Jobs

An operations team needs downloadable regional sales summaries. Requests arrive
intermittently; each report is finite, and its compute can be retried separately
from the HTTP interface. Kubernetes Jobs fit that execution model. A Deployment
fits the continuously serving API. A simple in-process worker would be cheaper
for a small application; the value here is learning these platform boundaries.

The lab aggregates a fixed, synthetic 10,000-line dataset. A five-second pause
makes Pod replacement visible. This is an execution/recovery demonstration, not
a benchmark of computation, Kubernetes startup time, or production capacity.

## Sources of truth

| State | Owner | Lifetime |
|---|---|---|
| Accepted request, idempotency key, attempts, result | PostgreSQL | Until the lab is deleted; no automatic report retention |
| Pod scheduling, replacement, backoff, Job terminal condition | Kubernetes | Finished Jobs and dependent Pods have a 300-second TTL |
| Session capability | Browser sessionStorage | Tab/session lifetime; only its SHA-256 scope is stored in PostgreSQL |
| API instances | Deployment/ReplicaSet | Replaceable |
| Database files | PVC on kind node | Survive database Pod replacement, not cluster deletion |

There is one application database and one trusted application DB role. This is
not a microservices database-isolation project. The API and workers intentionally
share the report schema. Application schema changes need coordination; startup
creates the initial table under a PostgreSQL advisory lock, not a general
migration system.

## Accept, dispatch, execute

1. A caller sends a UUID request key and one of three allowed modes. Arbitrary
   images, commands, environment variables, namespaces, and durations are rejected.
2. A transaction serializes admission with an advisory lock. At most six reports
   may be queued/submitted/running across the namespace. `(owner, request_key)`
   is unique. The same key and inputs returns its existing row; changed inputs
   returns 409. The API responds 202 after committing durable intent.
3. Each API replica runs a small reconciliation loop. It claims one queued row
   using `FOR UPDATE SKIP LOCKED`, creates `report-<UUID>`, and records submission.
   A short Kubernetes HTTP call occurs while holding that row lock (three-second
   network timeout, five-second SQL statement timeout). The bounded lab accepts
   that simplicity; a larger dispatcher should lease work outside transactions.
4. If Job creation succeeded but the DB acknowledgement was lost, the next
   create receives 409 and adopts the same name. It never invents another Job
   ID. The dedicated namespace and generated UUID establish name ownership;
   a cluster administrator remains trusted.
5. A worker obtains a nonblocking PostgreSQL session advisory lock for the report
   ID. Its attempt counter commits before execution. Only one lock holder computes
   and writes the single result row. A killed process loses its connection/lock;
   a replacement can continue. Hash collisions could serialize unrelated UUIDs.
6. Kubernetes retries failed Pods once. The reconciler records terminal Job
   failures when no successful business result exists. A missing previously
   submitted Job becomes `lost`, requiring operator investigation rather than
   silently re-running work after an uncertain outcome.

`queued → submitted → running → succeeded` is the usual path. Failed worker
attempts leave a report active until Kubernetes exhausts its budget or deadline;
then it becomes `failed`. A previous successful DB result is not overwritten by
late failed/missing-Job observations.

This is retry-safe result publication, not an exactly-once execution claim.
Work can run again after a process dies. Adding an email, payment, or object-store
side effect requires that provider's idempotency/reconciliation mechanism.

## Kubernetes controls and their boundaries

- **Deployment:** two replicas, zero unavailable during rollout, one surge Pod,
  three seconds minimum readiness. Replica count is explicitly managed; no HPA.
- **Probes:** startup permits application initialization; readiness checks the
  database; liveness checks the serving process only. A database failure should
  remove traffic without endlessly restarting healthy API processes. Readiness
  does not require the cluster API: requests may be accepted durably during a
  temporary Kubernetes control-plane outage.
- **Job:** `restartPolicy: Never`, `backoffLimit: 1`, `activeDeadlineSeconds: 80`.
  There can be more infrastructure replacement events than the application's
  observed attempt counter. Deadline and retry budget are limits, not an SLA.
- **Disruption budget:** `minAvailable: 1` protects voluntary Eviction API requests.
  Direct Pod deletion, Deployment scaling, node loss and an unavailable database
  are outside this guarantee. Both API replicas run on one node in this lab.
- **Quota:** CPU/memory, Pod and Job counts, and storage are bounded. Twelve retained
  Jobs can temporarily exhaust object quota; new requests remain queued until
  TTL cleanup frees capacity. Six active reports does not mean six running Pods.
- **Persistence:** one StatefulSet, one 1 GiB PVC with kind's local provisioner.
  This proves Pod-level persistence only. It is neither replication nor a backup.
- **Exposure:** kind maps the API NodePort to host loopback 8095. PostgreSQL has
  no host port. This local arrangement is not a public ingress/TLS design.

## Security model

All workloads pass the namespace's pinned restricted Pod Security policy. They
run nonroot, drop capabilities, deny privilege escalation, use RuntimeDefault
seccomp, and have read-only root filesystems. PostgreSQL receives explicit writable
data/socket/temp volumes. Requests and limits apply to all containers.

The API's namespace Role only grants `create/get` on Jobs. It cannot directly
read Secrets, exec into Pods, or modify Deployments. Workers do not mount a
Kubernetes service-account token. The application reads its projected API token
on every request to support rotation and validates the cluster's CA.

**Job creation is a powerful permission:** a compromised dispatcher could create
a Job that mounts namespace Secrets, despite being denied direct Secret reads.
The namespace must contain only this application's trusted credentials; RBAC
alone is not a sandbox for untrusted code. Pod Security does not prevent reading
application Secrets. A multi-tenant service needs stronger admission/image and
secret controls or separately isolated worker namespaces/identities.

The browser uses random bearer capabilities to isolate report listings. Tokens
are not stored in report rows or logs. This is local demo session isolation,
not user identity, organizational authorization, abuse prevention or a login
system. Anyone with access can create a session and submit bounded work.
Secrets are Kubernetes Secret objects, not encryption-at-rest assurance.

No NetworkPolicy is shipped: default kind networking does not establish an
enforcement claim. No cloud IAM, metrics-server, HPA, service mesh, autoscaled
nodes, cross-zone placement, managed database, external monitoring, TLS ingress,
disaster recovery, or load-test capacity is claimed.

## What a real workload would justify next

Start with workload measurements: queue age, scheduling delay, completion/error
rate, resource use, and report sizes. Add object storage for large outputs and
define retention. Adopt migrations and restricted DB roles before evolving the
schema or exposing more services. Add identity/rate limits and TLS before public
access. A managed HA database and tested backups address the actual data-loss
boundary. Multi-node placement plus a CNI that enforces NetworkPolicy would make
availability and network isolation claims testable. HPA needs measured signals
and metrics infrastructure; CPU is not automatically a useful queue signal.

## Primary references

- [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/)
- [kind v0.33.0 release and node image digests](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0)
- [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)
- [Job REST API](https://kubernetes.io/docs/reference/kubernetes-api/batch/job-v1/)
- [Pod disruption budgets](https://kubernetes.io/docs/tasks/run-application/configure-pdb/)
- [Restricted Pod Security requirements](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
