"""Shared contract (docs/02-architecture.md §3).

Provisional copy written by role A from the docs so A's modules can be built and tested.
Owner: C (tech lead). If C's scaffold differs, C's version wins.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Platform = Literal["aws", "leaseweb"]
Light = Literal["green", "amber", "red"]
Kind = Literal["object_store", "kv_table", "queue", "function", "trigger", "identity", "monitoring", "other"]
Provider = Literal[
    "aws.s3", "aws.dynamodb", "aws.sqs", "aws.lambda", "aws.iam", "aws.cloudwatch", "aws.other",
    "lw.object_storage", "lw.postgres", "lw.rabbitmq", "none",
]


# ---------- PAM ----------
class EnvVar(BaseModel):
    name: str
    value: str
    purpose: str


class KeyAttr(BaseModel):
    name: str
    type: Literal["S", "N"] = "S"


class Index(BaseModel):
    name: str
    partition_key: KeyAttr
    sort_key: KeyAttr | None = None


class CodeRef(BaseModel):
    file: str
    line: int
    symbol: str | None = None
    operation: str
    snippet: str


class Component(BaseModel):
    id: str
    kind: Kind
    provider: Provider
    resource_name: str
    name_env_var: str | None = None
    partition_key: KeyAttr | None = None
    sort_key: KeyAttr | None = None
    indexes: list[Index] = []
    fifo: bool = False
    max_receive_count: int | None = None
    visibility_timeout_s: int | None = None
    features: list[str] = []
    usages: list[CodeRef] = []


class FileInfo(BaseModel):
    path: str
    role: Literal["app", "infra", "test", "config", "other"]
    component_ids: list[str] = []


class AppModel(BaseModel):
    pam_version: Literal["0.2"] = "0.2"
    app_name: str
    platform: Platform
    entrypoints: list[str]
    test_command: str = "pytest -q tests"
    components: list[Component]
    files: list[FileInfo]
    env: list[EnvVar]


# ---------- Verification ----------
class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    duration_s: float = 0


class TestFailure(BaseModel):
    __test__ = False  # not a pytest class
    test: str
    message: str
    traceback: str


class TestReport(BaseModel):
    __test__ = False
    attempt: int
    platform: Platform
    passed: int
    failed: int
    errors: int
    total: int
    duration_s: float
    failures: list[TestFailure] = []
    output_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.failed == 0 and self.errors == 0


class Verification(BaseModel):
    checks: list[Check]
    tests: TestReport | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks) and bool(self.tests and self.tests.ok)


# ---------- Data ----------
class DataCount(BaseModel):
    component_id: str
    kind: Kind
    source_count: int
    target_count: int
    checksum_match: bool | None = None
    notes: list[str] = []


class DataMove(BaseModel):
    direction: Literal["aws->leaseweb", "leaseweb->aws"]
    counts: list[DataCount]

    @property
    def ok(self) -> bool:
        return all(c.source_count == c.target_count and c.checksum_match is not False for c in self.counts)


# ---------- Events ----------
Stage = Literal["scan", "assess", "approve", "delock", "generate", "verify", "fix", "data", "rollback", "report", "cost"]


class Event(BaseModel):
    ts: float
    run_id: str
    stage: Stage
    level: Literal["info", "success", "warning", "error"] = "info"
    message: str
    data: dict[str, Any] | None = None
