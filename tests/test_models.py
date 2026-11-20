from __future__ import annotations

import pytest
from pydantic import ValidationError

from boomerang.models import (
    Approval,
    Check,
    DataCount,
    DataMove,
    Event,
    Finding,
    FixResult,
    FunctionRewrite,
    GeneratedFile,
    RewriteRecord,
    RunSummary,
    TestFailure,
    TestReport,
    Verification,
)


def _report(**kw) -> TestReport:
    base = {"attempt": 1, "platform": "leaseweb", "passed": 7, "failed": 0, "errors": 0, "total": 7,
            "duration_s": 1.0}
    return TestReport(**{**base, **kw})


def test_test_models_are_not_collected_by_pytest():
    assert TestReport.__test__ is False and TestFailure.__test__ is False
    assert "__test__" not in TestReport.model_fields
    assert "__test__" not in _report().model_dump()


def test_llm_facing_models_put_reasoning_first():  # docs/02 D10
    assert next(iter(FunctionRewrite.model_fields)) == "analysis"
    assert next(iter(FixResult.model_fields)) == "root_cause_analysis"
    assert next(iter(Finding.model_fields)) == "analysis"


def test_run_summary_from_docs02_shape_gets_defaults():
    summary = RunSummary.model_validate(
        {"run_id": "r1", "source_dir": "sample-app/aws", "status": "running", "started_at": 1.0, "out_dir": "runs/r1"}
    )
    assert summary.baseline is None and summary.rewrites == [] and summary.tests_unchanged is None
    assert summary.roundtrip == "not_run" and summary.roundtrip_checks == [] and summary.fixtures_used == []


def test_run_summary_json_round_trip_with_lead_fields():
    summary = RunSummary(
        run_id="r1", source_dir="s", status="passed", started_at=1.0, out_dir="runs/r1",
        baseline=_report(platform="aws"),
        rewrites=[RewriteRecord(file="app/db.py", function="get_order", kind="config", accepted=True)],
        tests_unchanged=True, llm="ollama · qwen2.5-coder:7b · local", roundtrip="passed",
        roundtrip_checks=[Check(name="orders", ok=True, detail="26 == 25 + 1")],
        roundtrip_tests=_report(attempt=99, platform="aws"), fixtures_used=["scan"],
    )
    assert RunSummary.model_validate_json(summary.model_dump_json()) == summary


def test_ok_properties():
    assert _report().ok
    assert not _report(total=0, passed=0).ok
    assert not _report(failed=1).ok
    assert Verification(checks=[Check(name="boot", ok=True)], tests=_report()).ok
    assert not Verification(checks=[Check(name="boot", ok=True)]).ok
    good = DataCount(component_id="orders", kind="kv_table", source_count=25, target_count=25, checksum_match=True)
    bad = good.model_copy(update={"checksum_match": False})
    assert DataMove(direction="aws->leaseweb", counts=[good]).ok
    assert not DataMove(direction="aws->leaseweb", counts=[good, bad]).ok


def test_new_models_validate():
    assert GeneratedFile(path="deploy.sh", content="#!/bin/sh\n", executable=True).secret is False
    assert Approval(approved_by="cli", ts=1.0, plan_sha256="ab").plan_sha256 == "ab"
    with pytest.raises(ValidationError):
        RewriteRecord(file="a.py", function="f", kind="magic", accepted=True)
    with pytest.raises(ValidationError):
        Approval(approved_by="cli", ts=1.0)


def test_event_stage_is_closed():
    Event(ts=1.0, run_id="r", stage="delock", message="ok")
    with pytest.raises(ValidationError):
        Event(ts=1.0, run_id="r", stage="baseline", message="not a stage")
