"""The contract (docs/02 §3). Changes go through the lead.

Everything up to "Events & runs" is copied verbatim from docs/02 §3. Lead additions (docs/15 L1, L10)
are marked with ``# L1`` / ``# L10``; they are not LLM-facing and every new RunSummary field has a
default, so fixtures written against docs/02 still load.
"""

from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel

Platform = Literal["aws", "leaseweb"]
Light = Literal["green", "amber", "red"]
Kind = Literal["object_store", "kv_table", "queue", "function", "trigger", "identity", "monitoring", "other"]
Provider = Literal[
    "aws.s3", "aws.dynamodb", "aws.sqs", "aws.lambda", "aws.iam", "aws.cloudwatch", "aws.other",
    "lw.object_storage",   # SeaweedFS on the server, or Leaseweb Object Storage
    "lw.postgres", "lw.rabbitmq", "none",
]

# ---------- PAM (scanner output, LLM-facing) ----------
class EnvVar(BaseModel):
    name: str; value: str; purpose: str

class KeyAttr(BaseModel):
    name: str; type: Literal["S", "N"] = "S"

class Index(BaseModel):
    name: str; partition_key: KeyAttr; sort_key: KeyAttr | None = None

class CodeRef(BaseModel):
    file: str                    # "app/db.py" or "infra/main.tf"
    line: int
    symbol: str | None = None    # enclosing function, e.g. "get_order"
    operation: str               # "put_item", "send_message", "aws_lambda_permission"...
    snippet: str

class Component(BaseModel):
    id: str                      # "orders", "receipts", "order_events", "receipt_thumbnailer"
    kind: Kind
    provider: Provider
    resource_name: str
    name_env_var: str | None = None
    partition_key: KeyAttr | None = None      # kv_table
    sort_key: KeyAttr | None = None
    indexes: list[Index] = []
    fifo: bool = False                        # queue
    max_receive_count: int | None = None      # queue DLQ redrive
    visibility_timeout_s: int | None = None
    features: list[str] = []     # used features: "conditional_write", "gsi", "dlq", "event_notification", "ttl", ...
    usages: list[CodeRef] = []

class FileInfo(BaseModel):
    path: str
    role: Literal["app", "infra", "test", "config", "other"]
    component_ids: list[str] = []

class AppModel(BaseModel):       # PAM
    pam_version: Literal["0.2"] = "0.2"
    app_name: str
    platform: Platform
    entrypoints: list[str]
    test_command: str = "pytest -q tests"
    components: list[Component]
    files: list[FileInfo]
    env: list[EnvVar]

# ---------- Traffic lights ----------
class Finding(BaseModel):
    analysis: str                # reasoning first
    component_id: str
    feature: str                 # "basic object ops", "conditional_write", "dlq", "event_notification"...
    light: Light
    what_changes: str            # one sentence, user-facing
    evidence: list[CodeRef] = []

class Assessment(BaseModel):
    findings: list[Finding]
    overall: Light               # worst light that is migrated; reds are listed separately
    summary: str

# ---------- De-lock (LLM-facing, per function) ----------
class FunctionRewrite(BaseModel):
    analysis: str                # what the function does and which calls must change
    new_source: str              # the complete function (def ... ) only
    unsupported: list[str] = []  # features the adapter can't do → become red/amber findings

class FixResult(BaseModel):
    root_cause_analysis: str
    file: str
    function: str
    new_source: str

# ---------- Verification ----------
class Check(BaseModel):
    name: str                    # "compose config", "boot", "tests", "health", "schema"
    ok: bool
    detail: str = ""
    duration_s: float = 0

class TestFailure(BaseModel):
    __test__ = False             # L10: not a pytest test class
    test: str; message: str; traceback: str

class TestReport(BaseModel):
    __test__ = False             # L10: not a pytest test class
    attempt: int; platform: Platform
    passed: int; failed: int; errors: int; total: int; duration_s: float
    failures: list[TestFailure] = []
    output_tail: str = ""
    @property
    def ok(self) -> bool: return self.total > 0 and self.failed == 0 and self.errors == 0

class Verification(BaseModel):
    checks: list[Check]
    tests: TestReport | None = None
    @property
    def ok(self) -> bool: return all(c.ok for c in self.checks) and bool(self.tests and self.tests.ok)

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
    def ok(self) -> bool: return all(c.source_count == c.target_count and c.checksum_match is not False for c in self.counts)

class LostItem(BaseModel):
    component_id: str
    feature: str
    status: Literal["preserved", "changed", "lost"]
    detail: str

class Scope(BaseModel):
    selfhosted: list[str]

    def has(self, component_id: str) -> bool:
        return component_id in self.selfhosted

class ScopeStep(BaseModel):
    at: float
    action: Literal["move_over", "move_back"]
    components: list[str]
    verification_ok: bool | None = None
    data_moves: list[DataMove] = []

# ---------- L1: lead additions (not LLM-facing) ----------
class GeneratedFile(BaseModel):          # L1: B6 returns these
    path: str                            # relative to runs/<id>/leaseweb/
    content: str
    executable: bool = False             # deploy.sh, pg_backup.sh
    secret: bool = False                 # secrets.env → chmod 600, never sent to the LLM

class RewriteRecord(BaseModel):          # L1: one per function touched (D's Code tab reads these)
    file: str; function: str
    kind: Literal["ai", "config", "fix"]
    attempt: int = 0                     # 0 = de-lock, n = fixer round n
    accepted: bool
    changed_lines: int = 0               # 0 = "no change needed (1:1 mapping)", shown honestly
    analysis: str = ""                   # FunctionRewrite.analysis / FixResult.root_cause_analysis / "1:1 mapping, checked by code"
    unsupported: list[str] = []
    rejected_reason: str | None = None
    diff_path: str | None = None         # relative to runs/<id>/

class Approval(BaseModel):               # L1, L2
    approved_by: str                     # "cli", "ui", "auto"
    ts: float
    plan_sha256: str                     # sha256 of pam.json + assessment.json as approved; execute() refuses if either changed
    scope: list[str] | None = None       # docs/16: component ids approved to run self-hosted; None = all movable

# ---------- Boomerang Doctor (docs/18 §2) ----------
DoctorActionId = Literal["restart_service", "reverify", "move_back", "start_service"]
Severity = Literal["degraded", "down"]

class Signal(BaseModel):                           # one observation of the stack (piece 1, A, produces these)
    source: Literal["container", "logs", "health", "probe", "queue"]
    service: str                                   # compose service: postgres | rabbitmq | seaweedfs | api | worker
    ok: bool
    detail: str = ""                               # "exited (1) 2 min ago", "GET /health 500", "connection refused"
    lines: list[str] = []                          # evidence: log lines, newest last (<= 40)
    data: dict[str, Any] = {}                      # e.g. {"state": "exited", "exit_code": 1, "restarts": 3, "depth": 120}

class Action(BaseModel):                           # one fix from the allowlist (piece 1, A, runs it)
    id: DoctorActionId
    target: str                                    # service name, or component id for move_back
    label: str                                     # "Restart postgres", "Move orders back to AWS"
    risk: Literal["low", "medium", "high"]         # restart = low, reverify = low, move_back = high

class Diagnosis(BaseModel):                        # piece 2 (B) produces this from signals
    rule: str                                      # "db_down", "queue_down", ... or "unknown"
    root_cause: str                                # one sentence, from the rule
    service: str | None = None                     # the failing compose service
    component_id: str | None = None                # the PAM component behind it (orders, order_events, receipts)
    impact: list[str] = []                         # user-facing: ["Create order (POST /orders)", ...]
    severity: Severity
    evidence: list[Signal] = []                    # only the signals that support the rule
    actions: list[Action] = []                     # ranked, best first

class AppliedAction(BaseModel):                    # one approved fix and what came of it (Incident.applied)
    action: Action
    by: str                                        # "cli" | "ui"
    at: float
    ok: bool
    detail: str = ""

class Incident(BaseModel):                         # piece 3 (C) writes it to runs/<id>/doctor.json
    run_id: str
    at: float
    diagnosis: Diagnosis
    explanation: str = ""                          # the AI's plain words (empty if the model is unavailable)
    severity: Severity                             # = diagnosis.severity, or raised by the AI
    llm: str = ""                                  # config.Settings.llm_badge
    applied: list[AppliedAction] = []
    resolved_at: float | None = None               # set when a later check finds the stack healthy again

class DoctorExplanation(BaseModel):                # LLM-facing: the AI explains, it never decides (docs/18 §5.3)
    explanation: str                               # three short sentences: what happened, what users see, why fix 1
    raise_severity: bool = False                   # may turn degraded into down, never the other way


# ---------- Events & runs ----------
Stage = Literal["scan", "assess", "approve", "delock", "generate", "verify", "fix", "data", "rollback", "report", "cost",
                "doctor"]   # docs/18

class Event(BaseModel):
    ts: float; run_id: str; stage: Stage
    level: Literal["info", "success", "warning", "error"] = "info"
    message: str
    data: dict[str, Any] | None = None

class RunSummary(BaseModel):
    run_id: str; source_dir: str
    status: Literal["running", "awaiting_approval", "passed", "failed", "error"]
    started_at: float; finished_at: float | None = None
    lock_in_before: int | None = None; lock_in_after: int | None = None
    assessment: Assessment | None = None
    verifications: list[Verification] = []
    data_moves: list[DataMove] = []
    lost: list[LostItem] = []
    out_dir: str
    # L1: new fields, all defaulted so fixtures written against docs/02 still load
    baseline: TestReport | None = None           # original tests on AWS before migrating (L5)
    rewrites: list[RewriteRecord] = []
    tests_unchanged: bool | None = None          # L12
    llm: str = ""                                # "ollama · qwen2.5-coder:7b · local" or "anthropic · … · cloud fallback"
    roundtrip: Literal["not_run", "passed", "failed", "error"] = "not_run"
    roundtrip_checks: list[Check] = []           # count checks + "new order exists on AWS" (D's tab 4)
    roundtrip_tests: TestReport | None = None    # original tests on AWS after the copy back
    fixtures_used: list[str] = []                # L9: stages that ran on fixtures (report and UI show ⚠️)
    scope: Scope | None = None
    steps: list[ScopeStep] = []
