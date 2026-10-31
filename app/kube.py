"""Only the two Kubernetes REST operations this application needs."""
import json
import os
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAMESPACE = "dispatch"


class Kubernetes:
    def __init__(self):
        self.base = f'https://{os.environ["KUBERNETES_SERVICE_HOST"]}:{os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]}'
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=CA)))

    def request(self, method, path, body=None):
        # Projected tokens rotate; read the file for each request.
        req = Request(self.base + path, method=method,
                      data=None if body is None else json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + TOKEN.read_text().strip(),
                               "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            if error.code in (404, 409):
                return error.code, None
            raise

    def create(self, report_id):
        return self.request("POST", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", job_manifest(report_id))

    def get(self, report_id):
        return self.request("GET", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/report-{report_id}")


def job_manifest(report_id):
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": f"report-{report_id}", "labels": {"app": "report-worker"}},
        "spec": {
            "backoffLimit": 1, "activeDeadlineSeconds": 80, "ttlSecondsAfterFinished": 300,
            "template": {"metadata": {"labels": {"app": "report-worker"}}, "spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "worker", "image": os.environ.get("WORKER_IMAGE", "dispatch-lab:v1"),
                    "imagePullPolicy": "IfNotPresent", "command": ["python", "-m", "app.worker", str(report_id)],
                    "envFrom": [{"secretRef": {"name": "database"}}],
                    "resources": {"requests": {"cpu": "100m", "memory": "64Mi"},
                                  "limits": {"cpu": "500m", "memory": "128Mi"}},
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                }],
            }},
        },
    }
