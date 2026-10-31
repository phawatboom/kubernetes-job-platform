from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.kube import job_manifest
from app.main import Submission, owner
from app.worker import calculate


def test_generated_result_is_exact_integer_data():
    result = calculate()
    assert result["lines"] == 10000
    assert sum(r["lines"] for r in result["regions"].values()) == 10000
    assert result["revenue_cents"] == sum((100 + i % 9900) * (1 + i % 5) for i in range(10000))
    assert calculate() == result


def test_job_has_bounded_retries_and_no_cluster_token():
    report_id = uuid4()
    manifest = job_manifest(report_id)
    assert manifest["metadata"]["name"] == f"report-{report_id}"
    assert manifest["spec"]["backoffLimit"] == 1
    assert manifest["spec"]["activeDeadlineSeconds"] == 80
    spec = manifest["spec"]["template"]["spec"]
    assert not spec["automountServiceAccountToken"]
    container = spec["containers"][0]
    assert container["command"][-1] == str(report_id)
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert container["resources"]["limits"]["memory"] == "128Mi"


@pytest.mark.parametrize("token", [None, "Bearer short", "Bearer " + "!" * 43, "Basic " + "a" * 43])
def test_session_boundary_rejects_invalid_tokens(token):
    with pytest.raises(HTTPException) as error:
        owner(token)
    assert error.value.status_code == 401


def test_owner_hash_is_stable_and_scoped():
    assert owner("Bearer " + "a" * 43) == owner("Bearer " + "a" * 43)
    assert owner("Bearer " + "a" * 43) != owner("Bearer " + "b" * 43)


@pytest.mark.parametrize("extra", [{"image": "attacker"}, {"mode": "arbitrary"}, {"request_key": "bad"}])
def test_caller_cannot_choose_cluster_execution_parameters(extra):
    with pytest.raises(ValidationError):
        Submission(**({"request_key": str(uuid4())} | extra))
