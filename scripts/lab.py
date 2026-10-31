"""All cluster operations are scoped to this repository's kubeconfig and kind name."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
CONFIG = LOCAL / "kubeconfig"
CLUSTER = "dispatch-lab"
VERSION = "v0.33.0"


def command(args, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=ROOT, text=True, check=True, **kwargs)


def kind():
    binary = LOCAL / ("kind.exe" if os.name == "nt" else "kind")
    if binary.exists():
        return str(binary)
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"amd64": "amd64", "x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if system not in ("windows", "linux", "darwin") or not arch:
        raise SystemExit("Unsupported kind platform")
    LOCAL.mkdir(exist_ok=True)
    name = f"kind-{system}-{arch}"
    url = f"https://github.com/kubernetes-sigs/kind/releases/download/{VERSION}/{name}"
    with urlopen(url, timeout=60) as response:
        data = response.read()
    with urlopen(url + ".sha256sum", timeout=30) as response:
        expected = response.read().decode().split()[0]
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("kind checksum mismatch; executable was not written")
    binary.write_bytes(data)
    binary.chmod(0o700)
    return str(binary)


def kubectl(*args, **kwargs):
    if not CONFIG.exists():
        raise SystemExit("No repository-local kubeconfig. Run python scripts/lab.py up first.")
    return command(["kubectl", "--kubeconfig", CONFIG, "--context", "kind-dispatch-lab", "--namespace", "dispatch", *args], **kwargs)


def up():
    for tool in ("docker", "kubectl"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} must be on PATH")
    executable = kind()
    clusters = command([executable, "get", "clusters"], capture_output=True).stdout.splitlines()
    if CLUSTER in clusters:
        if not CONFIG.exists():
            raise SystemExit("Named cluster exists without this repo's kubeconfig; refusing to adopt it")
    else:
        command([executable, "create", "cluster", "--name", CLUSTER, "--kubeconfig", CONFIG,
                 "--config", "k8s/kind.yaml", "--wait", "180s"])
    command(["docker", "build", "-t", "dispatch-lab:v1", "."])
    command([executable, "load", "docker-image", "dispatch-lab:v1", "--name", CLUSTER])
    kubectl("apply", "-f", "k8s/namespace.yaml")
    existing = kubectl("get", "secret", "database", "--ignore-not-found", "-o", "name", capture_output=True).stdout.strip()
    if not existing:
        password = secrets.token_urlsafe(32)
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "database", "namespace": "dispatch"},
                  "type": "Opaque", "stringData": {"POSTGRES_PASSWORD": password,
                      "DATABASE_URL": f"postgresql://dispatch:{password}@postgres:5432/dispatch"}}
        kubectl("create", "-f", "-", input=json.dumps(secret), capture_output=True)
    kubectl("apply", "-f", "k8s/access.yaml", "-f", "k8s/database.yaml")
    kubectl("rollout", "status", "statefulset/postgres", "--timeout=180s")
    kubectl("apply", "-f", "k8s/api.yaml")
    kubectl("rollout", "restart", "deployment/api")
    kubectl("rollout", "status", "deployment/api", "--timeout=180s")
    print("Dispatch is ready: http://localhost:8095")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "status", "down", "kubectl"])
    args, remaining = parser.parse_known_args()
    if args.action == "up":
        up()
    elif args.action == "status":
        kubectl("get", "pods,jobs,pvc,pdb,resourcequota")
    elif args.action == "down":
        if not CONFIG.exists():
            raise SystemExit("No local kubeconfig; refusing cluster deletion")
        command([kind(), "delete", "cluster", "--name", CLUSTER])
        CONFIG.unlink(missing_ok=True)
    else:
        if not remaining:
            parser.error("kubectl arguments required")
        if any(a.startswith(("--context", "--kubeconfig", "--namespace")) or a == "-n" for a in remaining):
            parser.error("Context, kubeconfig, and namespace overrides are not allowed")
        kubectl(*remaining)
