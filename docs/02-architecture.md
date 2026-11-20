# 02 · Architecture

## 1. System overview

```mermaid
flowchart LR
    subgraph Input
      SRC[App source<br/>code + Terraform/SQL]
    end
    subgraph Engine["boomerang (Python package)"]
      SC[Scanner<br/>static + AI] --> PAM[(Portable App Model)]
      PAM --> LK[Lock-in score]
      PAM --> PL[Planner<br/>mappings + AI notes]
      PL --> RW[Rewriter<br/>AI, per file]
      PL --> GEN[Generators<br/>Jinja templates]
      RW --> VAL[Validator<br/>provision + pytest]
      GEN --> VAL
      VAL -- failures --> FX[Fixer<br/>AI] --> VAL
      VAL --> REP[Report]
    end
    subgraph Lab["Lab (Docker Compose)"]
      MOTO[moto: fake AWS<br/>S3 · DynamoDB · SQS]
      PG[Postgres]
      MINIO[MinIO]
      REDIS[Redis]
    end
    SRC --> SC
    VAL --> Lab
    LLM[[Claude API<br/>via llm.py + cache]] -.-> SC & PL & RW & FX
    UI[Streamlit UI] --> Engine
    CLI[Typer CLI] --> Engine
```

**Key decisions**

| # | Decision | Why |
|---|---|---|
| D1 | One Python package, called directly by both CLI and UI (no HTTP API between them) | Removes a whole integration layer; 6 hours |
| D2 | Platform-neutral **PAM** in the middle | Both directions share one engine; new targets = new generator |
| D3 | AI for code, **templates for infrastructure** | Infra must be exactly right and match the conventions the AI follows |
| D4 | **Mapping conventions** are one text source used by both the rewriter prompt and the generators | Guarantees the code and the schema agree |
| D5 | Original tests are the spec; AI can never edit `tests/` | Credible proof of correctness |
| D6 | AWS emulated by **moto** in Docker | No AWS account, no cost, works offline-ish |
| D7 | LLM record/replay cache | Fast, deterministic demo; cheap re-runs |
| D8 | Validation runs against the shared lab, with per-run resource names | Fast (seconds) and isolated |
| D9 | Streamlit for UI | Fastest path to a good-looking dashboard in Python |
| D10 | Every stage emits **events**, written to `events.jsonl` | Live progress in UI, and replay of recorded runs |

## 2. Pipeline sequence (one leg)

```mermaid
sequenceDiagram
    participant P as pipeline.migrate()
    participant S as scanner
    participant PL as planner
    participant R as rewriter
    participant G as generators
    participant V as validator
    participant F as fixer
    P->>S: scan(app_dir, platform)
    S-->>P: AppModel (PAM)
    P->>PL: plan(pam, target)
    PL-->>P: MigrationPlan
    P->>P: copy app to runs/<id>/app (tests included)
    par per affected file
      P->>R: rewrite(file, pam, plan)
      R-->>P: RewriteResult
    end
    P->>G: render(pam, target)
    G-->>P: infra files (+ .env, requirements.txt)
    loop attempt 1..3
      P->>V: validate(app_dir, pam, target)
      V-->>P: TestReport
      alt all passed
        P->>P: stop
      else failures
        P->>F: fix(report, files, plan)
        F-->>P: FileEdits (never tests/)
      end
    end
    P->>P: write report.md, run.json
```

Round trip = `migrate(aws → local)` then `migrate(<leg1 output> → aws)`. Leg 2 **re-scans** the generated local app, which also proves the scanner understands local code.

## 3. The contract: `boomerang/models.py`

This file is written by the tech lead in the first 30 minutes, exactly as below. Everyone codes against it. **The LLM-facing models avoid `dict` fields and unions on purpose**: they are passed to Claude's structured outputs, which work best with flat objects and lists.

```python
# boomerang/models.py
from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field

Platform = Literal["aws", "local"]
Kind = Literal["object_store", "kv_table", "queue"]
Provider = Literal["aws.s3", "aws.dynamodb", "aws.sqs",
                   "local.minio", "local.postgres", "local.redis"]

# ---------- Portable App Model (LLM-facing: scanner output) ----------
class EnvVar(BaseModel):
    name: str                     # "ORDERS_TABLE"
    value: str                    # dev/default value, "orders"
    purpose: str                  # "physical name of orders table"

class KeyAttr(BaseModel):
    name: str                     # "order_id"
    type: Literal["S", "N"] = "S"

class Index(BaseModel):
    name: str                     # "customer_id-index"
    partition_key: KeyAttr
    sort_key: KeyAttr | None = None

class CodeRef(BaseModel):
    file: str                     # relative to app root, "app/db.py"
    line: int                     # 1-based
    operation: str                # "put_item", "query", "send_message", "rpush", "execute"...
    snippet: str                  # the source line, stripped

class Component(BaseModel):
    id: str                       # stable logical id: "orders", "receipts", "order_events"
    kind: Kind
    provider: Provider            # current binding
    resource_name: str            # dev physical name: "orders"
    name_env_var: str | None = None   # env var holding the name: "ORDERS_TABLE"
    # kv_table only
    partition_key: KeyAttr | None = None
    sort_key: KeyAttr | None = None
    indexes: list[Index] = []
    # queue only
    fifo: bool = False
    usages: list[CodeRef] = []
    notes: list[str] = []         # semantics that may break: "relies on visibility timeout"

class FileInfo(BaseModel):
    path: str
    role: Literal["app", "infra", "test", "config", "other"]
    component_ids: list[str] = []

class AppModel(BaseModel):        # a.k.a. PAM
    pam_version: Literal["0.1"] = "0.1"
    app_name: str
    platform: Platform
    language: Literal["python"] = "python"
    framework: str = "fastapi"
    entrypoints: list[str]        # ["app.main:app", "app.worker:main"]
    test_command: str = "pytest -q tests"
    components: list[Component]
    files: list[FileInfo]
    env: list[EnvVar]
    notes: list[str] = []

# ---------- Plan ----------
Strategy = Literal["config", "rewrite"]
Risk = Literal["low", "medium", "high"]

class PlanStep(BaseModel):
    component_id: str
    from_provider: Provider
    to_provider: Provider
    strategy: Strategy
    files: list[str]              # app files to rewrite for this component
    risk: Risk
    notes: list[str] = []         # semantic differences, written by the AI

class MigrationPlan(BaseModel):
    source: Platform
    target: Platform
    steps: list[PlanStep]
    files_to_rewrite: list[str]
    lock_in_before: int
    lock_in_after: int
    summary: str                  # 2-4 sentences, AI-written
    warnings: list[str] = []

# ---------- Rewrite / fix (LLM-facing) ----------
class RewriteResult(BaseModel):
    path: str
    new_content: str              # the complete file
    summary: str
    changes: list[str] = []

class FileEdit(BaseModel):
    path: str
    new_content: str
    reason: str

class FixResult(BaseModel):
    diagnosis: str                # what went wrong, 1-3 sentences
    edits: list[FileEdit]

# ---------- Validation ----------
class TestFailure(BaseModel):
    test: str                     # "tests/test_orders.py::test_create_order"
    message: str
    traceback: str                # trimmed to ~60 lines

class TestReport(BaseModel):
    attempt: int
    platform: Platform
    passed: int
    failed: int
    errors: int
    total: int
    duration_s: float
    failures: list[TestFailure] = []
    output_tail: str = ""         # last ~80 lines of pytest output

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.failed == 0 and self.errors == 0

# ---------- Events & runs ----------
Stage = Literal["scan", "plan", "rewrite", "generate", "provision",
                "validate", "fix", "report", "roundtrip"]

class Event(BaseModel):
    ts: float
    run_id: str
    stage: Stage
    level: Literal["info", "success", "warning", "error"] = "info"
    message: str
    data: dict[str, Any] | None = None   # not LLM-facing, dicts are fine

class RunSummary(BaseModel):
    run_id: str
    source_dir: str
    source: Platform
    target: Platform
    status: Literal["running", "passed", "failed", "error"]
    started_at: float
    finished_at: float | None = None
    lock_in_before: int | None = None
    lock_in_after: int | None = None
    attempts: list[TestReport] = []
    files_rewritten: list[str] = []
    out_dir: str

class RoundTripSummary(BaseModel):
    roundtrip_id: str
    baseline: TestReport          # original app tested on its own platform
    legs: list[RunSummary]        # [aws->local, local->aws]
    status: Literal["passed", "failed"]
```

## 4. Mappings (owned by B, `boomerang/mappings.py`)

```python
COUNTERPART: dict[tuple[str, str], str] = {
    ("aws.s3", "local"): "local.minio",
    ("aws.dynamodb", "local"): "local.postgres",
    ("aws.sqs", "local"): "local.redis",
    ("local.minio", "aws"): "aws.s3",
    ("local.postgres", "aws"): "aws.dynamodb",
    ("local.redis", "aws"): "aws.sqs",
}
STRATEGY = {  # (from, to) -> strategy, base risk
    ("aws.s3", "local.minio"): ("config", "low"),
    ("local.minio", "aws.s3"): ("config", "low"),
    ("aws.dynamodb", "local.postgres"): ("rewrite", "medium"),
    ("local.postgres", "aws.dynamodb"): ("rewrite", "medium"),
    ("aws.sqs", "local.redis"): ("rewrite", "medium"),
    ("local.redis", "aws.sqs"): ("rewrite", "medium"),
}
CONVENTIONS: dict[tuple[str, str], str] = {...}   # text below, verbatim
ENV_CONTRACT: dict[str, list[tuple[str, str]]] = {...}  # §6
```

### 4.1 Conventions (the text the rewriter prompt receives AND the generators implement)

**aws.dynamodb → local.postgres**
- Connect with psycopg 3: `psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row)`, one connection per operation (`with ... as conn:`).
- The table name comes from the same env var as before (e.g. `ORDERS_TABLE`). Quote it with `psycopg.sql.Identifier`.
- Boomerang creates the schema. **App code must not create tables.** Schema:
  `<pk> TEXT|NUMERIC NOT NULL, [<sk> ...,] <one column per index key attribute> TEXT|NUMERIC, data JSONB NOT NULL, PRIMARY KEY (<pk>[, <sk>])`, plus `CREATE INDEX` per index on its key column(s).
- `data` holds the **complete item**, including key attributes. Wrap with `psycopg.types.json.Jsonb(item)`.
- `put_item` → `INSERT ... ON CONFLICT (<pk>[,<sk>]) DO UPDATE SET data = EXCLUDED.data, <promoted cols> = EXCLUDED.<...>`.
- `get_item` → `SELECT data FROM t WHERE <pk> = %s` → dict or `None`.
- `update_item SET a = :v` → `UPDATE t SET data = jsonb_set(data, '{a}', to_jsonb(%s::text))` (use the right cast) and also update the promoted column if `a` is a key/index attribute.
- `query(IndexName=...)` → `SELECT data FROM t WHERE <index col> = %s`.
- Numbers come back as `int`/`float` from JSONB (not `Decimal`). Keep return shapes identical.

**local.postgres → aws.dynamodb**
- `boto3.resource("dynamodb")`; region and endpoint come from the standard env (`AWS_REGION`, `AWS_ENDPOINT_URL`). Table name from the same env var.
- Partition key = first primary-key column; sort key = second, if any. Every other promoted/indexed column becomes a GSI named `<column>-index`, projection ALL.
- Item = the `data` JSON (it contains the key attributes).
- UPSERT → `put_item`; select by PK → `get_item(...).get("Item")`; select by indexed column → `query(IndexName="<col>-index", KeyConditionExpression=Key(col).eq(v))`; field update → `update_item(UpdateExpression="SET #f = :v", ...)`.
- Convert `float` → `Decimal` when writing; convert integral `Decimal` → `int` when returning.

**aws.sqs → local.redis**
- `redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)`. Queue name (same env var) is a Redis **list** key.
- `send_message(MessageBody=s)` → `r.rpush(name, s)`.
- `receive_message(...)` → `r.blpop([name], timeout=w)` if waiting, else `r.lpop(name)`. Return the same shape the original function returned.
- `delete_message` → no-op (pop already removed it). Keep public functions, make them no-ops.
- ⚠️ Semantics: SQS is at-least-once with visibility timeout; a Redis list pop is at-most-once. The planner must flag it.

**local.redis → aws.sqs**
- `boto3.client("sqs")`; resolve the URL once with `get_queue_url(QueueName=name)` and cache it.
- `rpush` → `send_message`; `lpop`/`blpop` → `receive_message(MaxNumberOfMessages=1, WaitTimeSeconds=min(timeout, 20))`, then `delete_message` right after reading (keeps pop semantics). Return the body or `None`.

**aws.s3 → local.minio**
- Keep the boto3 S3 client, but construct it with `endpoint_url=os.environ["S3_ENDPOINT_URL"]`, `aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"]`, `aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"]`, `region_name="us-east-1"`, `config=Config(s3={"addressing_style": "path"})`. All S3 calls stay the same.

**local.minio → aws.s3**
- `boto3.client("s3")` with no explicit endpoint or keys; remove `S3_*` env usage.

## 5. Env contract per platform

The app reads **everything** from env vars. Resource-name vars keep their names across platforms; connection vars change.

| Var | aws (lab) | local (lab) |
|---|---|---|
| `AWS_REGION`, `AWS_DEFAULT_REGION` | `us-east-1` | (unset) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `test` / `test` | (unset) |
| `AWS_ENDPOINT_URL` | `http://127.0.0.1:4566` | (unset) |
| `DATABASE_URL` | (unset) | `postgresql://boomerang:boomerang@127.0.0.1:5432/<db>` |
| `REDIS_URL` | (unset) | `redis://127.0.0.1:6379/0` |
| `S3_ENDPOINT_URL` | (unset) | `http://127.0.0.1:9000` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | (unset) | `boomerang` / `boomerang-secret` |
| `ORDERS_TABLE` / `RECEIPTS_BUCKET` / `ORDER_EVENTS_QUEUE` | per-run names | per-run names |

Use `127.0.0.1`, not `localhost`: it avoids slow IPv6 lookups on Windows, and makes boto3 use path-style S3 URLs.

## 6. Run artifacts

```
runs/
  20261121-143002-aws2local/
    run.json                 # RunSummary
    events.jsonl             # one Event per line
    source.pam.json          # scanner output
    plan.json                # MigrationPlan
    app/                     # migrated app (tests copied unchanged)
      .env                   # lab env for this platform
    infra/                   # docker-compose.yml + schema.sql   OR   main.tf
    diffs/app__db.py.diff    # unified diff per rewritten/fixed file
    validation/attempt-1.json
    report.md
  rt-20261121-143002/
    roundtrip.json           # RoundTripSummary
    leg1 -> ../20261121-...-aws2local
    leg2 -> ../20261121-...-local2aws
```
`runs/` is git-ignored except `runs/golden/` (a recorded good run, committed for the demo fallback).

## 7. Repository layout

```
boomerang/                    # the engine (Python package)
  __init__.py
  models.py                   # contract (C)
  config.py                   # settings from env/.env (C)
  events.py                   # Emitter, JSONL sink, console sink (C)
  llm.py                      # Claude client + record/replay cache (C)
  cli.py                      # Typer app (C; others add commands)
  lab.py                      # docker compose wrapper: up/down/status (A)
  mappings.py                 # COUNTERPART, STRATEGY, CONVENTIONS, ENV_CONTRACT, postgres_ddl() (B)
  lockin.py                   # score (B)
  scanner/static.py           # regex/AST hints (B)
  scanner/ai.py               # LLM pass -> AppModel (B)
  scanner/__init__.py         # scan() (B)
  planner.py                  # (B)
  generators/local.py         # compose, schema.sql, .env, requirements (B)
  generators/aws.py           # main.tf, .env, requirements (B)
  generators/templates/*.j2   # (B)
  provisioner.py              # create/destroy resources in lab (A)
  validator.py                # run pytest, parse junit (A)
  rewriter.py                 # (C)
  fixer.py                    # (C)
  pipeline.py                 # migrate(), roundtrip() (C)
  report.py                   # report.md (C)
prompts/                      # scan.md, plan.md, rewrite.md, fix.md (B: scan/plan, C: rewrite/fix)
ui/app.py                     # Streamlit (D)
ui/components/*.py            # (D)
sample-app/aws/               # original locked-in flavor (A)
sample-app/local/             # hand-written local flavor (A)
sample-app/tests/             # shared black-box tests (A)
infra/lab.compose.yml         # the lab (A)
fixtures/                     # example PAM, plan, events, reports (B + D)
tests/                        # engine unit tests (everyone, light)
runs/golden/                  # committed recorded run (D, at the end)
demo/llm-cache/               # committed LLM cache for replay (C, at the end)
docs/
```
