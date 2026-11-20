# 03 · Components

Each section: **owner · files · interface · behavior · done when**. Interfaces are binding; internals are up to the owner.

---

## 3.1 Config & events (C)

`boomerang/config.py`: load `.env` with python-dotenv; expose a `Settings` object:

| Setting | Env | Default |
|---|---|---|
| `model` | `BOOMERANG_MODEL` | `claude-opus-5-5` |
| `llm_mode` | `BOOMERANG_LLM_MODE` | `record` (`live` · `record` · `replay`) |
| `cache_dir` | `BOOMERANG_CACHE_DIR` | `.cache/llm` |
| `runs_dir` | `BOOMERANG_RUNS_DIR` | `runs` |
| `max_fix_attempts` | `BOOMERANG_MAX_FIX_ATTEMPTS` | `3` |

`boomerang/events.py`:
```python
Emit = Callable[[Event], None]
class Emitter:
    def __init__(self, run_id: str, sinks: list[Emit]): ...
    def __call__(self, stage, message, level="info", **data) -> None   # builds Event(ts=time.time(), ...)
def jsonl_sink(path: Path) -> Emit
def console_sink() -> Emit           # rich colored output for the CLI
```
Every stage takes an `emit: Emitter` argument. The UI passes its own sink to render live.

---

## 3.2 LLM layer (C), `boomerang/llm.py`

One function that every AI stage uses:

```python
def structured(*, system: str, user: str, schema: type[T], label: str) -> T:
    """Call Claude, return a validated Pydantic object. Cached by request hash."""
```

Implementation sketch (Anthropic Python SDK 1.x, structured outputs via `parse`):

```python
import anthropic, hashlib, json
client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY

def structured(*, system, user, schema, label):
    key = hashlib.sha256(json.dumps(
        {"m": settings.model, "s": system, "u": user, "schema": schema.model_json_schema()},
        sort_keys=True).encode()).hexdigest()[:24]
    path = settings.cache_dir / f"{label}-{key}.json"
    if settings.llm_mode in ("record", "replay") and path.exists():
        return schema.model_validate_json(path.read_text(encoding="utf-8"))
    if settings.llm_mode == "replay":
        raise CacheMiss(label, key)
    response = client.beta.messages.parse(
        model=settings.model,                       # claude-opus-5-5
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],  # re-run on a fallback model if a request is declined
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        raise LLMRefused(label)
    if response.stop_reason == "max_tokens":
        raise LLMTruncated(label)
    result = response.parsed_output
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
```

Notes
- **Model:** default `claude-opus-5-5` (best quality for code rewriting). If latency hurts during the event, the team can set `BOOMERANG_MODEL=claude-sonnet-5-5`; it's a one-line env change.
- Thinking is always on with Opus 5.5 (adaptive); don't pass `thinking` or `temperature`. Effort defaults to `medium`, which is fine.
- If the installed SDK rejects `fallbacks` on `parse`, delete the `betas`/`fallbacks` lines. Refusals are unlikely for this workload.
- Catch `anthropic.RateLimitError` / `APIConnectionError` in the pipeline and surface them as `error` events. The SDK already retries twice.
- Log `usage.input_tokens` / `output_tokens` into the event `data` so the UI can show cost.
- Cache files are small JSON. At the end, copy the cache of the golden run into `demo/llm-cache/` and commit it, so replay works on any machine.

**Done when:** a test call with a toy schema returns a parsed object, a second identical call is served from cache in < 50 ms, and `replay` mode raises on a miss.

---

## 3.3 Scanner (B), `boomerang/scanner/`

```python
def scan(app_dir: Path, platform: Platform, emit: Emitter) -> AppModel
```

**Step 1: static hints (`static.py`)**, no AI. Walk `*.py`, `*.tf`, `*.sql`, `.env*`, `requirements*.txt` (skip `.venv`, `__pycache__`). Regex per provider:

| Provider | Patterns (examples) |
|---|---|
| aws.s3 | `boto3\.(client|resource)\(\s*["']s3`, `\.(put_object|get_object|upload_fileobj|download_fileobj|head_object|delete_object)\(` |
| aws.dynamodb | `boto3\.(client|resource)\(\s*["']dynamodb`, `\.(put_item|get_item|update_item|delete_item|query|scan|batch_write_item)\(` |
| aws.sqs | `boto3\.(client|resource)\(\s*["']sqs`, `\.(send_message|receive_message|delete_message|get_queue_url)\(` |
| local.postgres | `psycopg|psycopg2`, `\.execute\(` with SQL keywords |
| local.redis | `redis\.Redis|from_url`, `\.(rpush|lpush|lpop|blpop|brpop|xadd|xreadgroup)\(` |
| local.minio | S3 patterns **plus** `endpoint_url=` / `S3_ENDPOINT_URL` |
| env vars | `os\.environ\[["'](\w+)`, `os\.getenv\(["'](\w+)` |

Output: `list[Hint(file, line, provider_guess, operation, snippet)]` + env var names. Also parse `.env.example` for default values, and Terraform resource blocks / `CREATE TABLE` statements as raw text for the AI.

**Step 2: AI pass (`ai.py`)**: `llm.structured(system=prompts/scan.md, user=<hints JSON + files with line numbers + IaC text>, schema=AppModel, label="scan")`.

Send files **with line numbers** (`  12| code`). The sample app is small; send all app files whole. Never truncate silently; if the input would exceed ~150k characters, error out with a clear message.

**Step 3: merge & check (deterministic).** Make sure every static hint is covered by some component usage; add missing ones, emit a `warning`. Make sure `platform` matches the argument. Assign `role="test"` to everything under `tests/`.

**Done when:** `boomerang scan sample-app/aws` produces a PAM equivalent to `fixtures/pam.aws.json` (3 components, all usages, GSI found), and `scan sample-app/local` produces the local equivalent.

### Prompt: `prompts/scan.md`
```
You are Boomerang's scanner. You read a small Python application and produce a Portable App Model (PAM):
a platform-neutral description of the infrastructure the app depends on.

Rules:
- Report only components the code actually uses. Kinds: object_store, kv_table, queue.
  Providers: aws.s3, aws.dynamodb, aws.sqs, local.minio, local.postgres, local.redis.
- One component per physical resource. Give it a short snake_case id (e.g. "orders", "receipts", "order_events").
- List every call site in `usages` with file, 1-based line, operation and the code line. Use the static hints as
  ground truth for locations; never invent files or lines.
- If the code reads a resource name from an environment variable, set name_env_var and use the default value from
  the env/IaC files as resource_name.
- kv_table: take partition_key, sort_key and indexes from Terraform or SQL DDL when present; otherwise infer from code.
  For Postgres tables following the Boomerang convention (key columns + `data JSONB`), the primary key is the
  partition key and every other indexed column is an index named "<column>-index".
- files: role "app" for application code, "test" for tests, "infra" for Terraform/compose/SQL, "config" for env and
  requirements files; component_ids = components that file touches.
- notes: record semantics that could break in a migration (conditional writes, transactions, visibility timeouts,
  FIFO ordering, TTL, presigned URLs, batch operations, pagination, scans).
- entrypoints: the ASGI app and any worker main function, as "module.path:object".
```

---

## 3.4 Lock-in score (B), `boomerang/lockin.py`

```python
PROVIDER_LOCKIN = {"aws.dynamodb": 90, "aws.sqs": 70, "aws.s3": 35,
                   "local.postgres": 5, "local.redis": 10, "local.minio": 10}
def score(pam: AppModel) -> int        # weighted by usage count
def breakdown(pam: AppModel) -> list[tuple[str, str, int, int]]  # (component, provider, score, usages)
```
`score = round(Σ PROVIDER_LOCKIN[p] × max(1, usages) / Σ max(1, usages))`. Rationale for the UI tooltip: *proprietary API and data model (DynamoDB) > proprietary API (SQS) > de-facto standard API (S3) > open source (Postgres/Redis/MinIO)*. Also `score_for_target(pam, target)` = the score after swapping providers via `COUNTERPART` (used by the planner for `lock_in_after`).

---

## 3.5 Planner (B), `boomerang/planner.py`

```python
def plan(pam: AppModel, target: Platform, emit: Emitter) -> MigrationPlan
```
1. Deterministic: for each component, look up `COUNTERPART`, `STRATEGY`; files = files whose usages touch the component (role `app`). Components already on the target platform are skipped.
2. AI: one call with `prompts/plan.md` + PAM + steps + the relevant `CONVENTIONS` → returns `PlanNotes { summary: str, step_notes: list[StepNote(component_id, notes: list[str], risk)], warnings: list[str] }`. Merge into the plan. The AI may raise risk, never lower it.
3. `lock_in_before = score(pam)`, `lock_in_after = score_for_target(pam, target)`.

### Prompt: `prompts/plan.md`
```
You are Boomerang's migration planner. Given an app model, the mechanical migration steps and the mapping
conventions, explain the migration for an engineer.
- summary: 2-4 sentences on what moves where and what stays the same.
- For each step: concrete notes about behavior that changes (delivery guarantees, consistency, ordering, types,
  limits), and a risk level (low/medium/high). Be specific to this code; cite functions or operations.
- warnings: anything the automated migration will NOT handle (data migration, IAM, monitoring, scaling).
Never claim something is tested or verified.
```

---

## 3.6 Rewriter (C), `boomerang/rewriter.py`

```python
def rewrite_file(app_dir: Path, rel_path: str, pam: AppModel, plan: MigrationPlan,
                 emit: Emitter) -> RewriteResult
def rewrite_all(app_dir, pam, plan, emit, max_workers=3) -> list[RewriteResult]
```
- Only files in `plan.files_to_rewrite`. Run in a `ThreadPoolExecutor` (3 workers) to cut wall time.
- User message contains: the file with line numbers; the PAM components this file touches (JSON); the `CONVENTIONS` for each relevant step; the target env contract (§5 of architecture); the list of other app files and their top-level `def`/`class` names (so it knows what's imported elsewhere).
- Guardrails after the call: `path` must equal the requested path; `new_content` must compile (`compile(src, path, "exec")`); if not, emit a warning and retry once with the syntax error appended.
- Write the file, save a unified diff to `diffs/`, emit a `rewrite` event with `data={"path", "summary", "changes", "diff_lines"}`.

### Prompt: `prompts/rewrite.md`
```
You are Boomerang's rewriter. You migrate ONE Python file from {source} to {target}.

Hard rules:
1. Keep every public function, class and module-level name with the same signature and the same return
   shapes (dict keys and value types). Other files import them.
2. Change only what is needed to move the listed components to their target providers. Business logic,
   validation and HTTP behavior stay identical.
3. Read every connection setting and resource name from the environment variables in the ENV CONTRACT.
   Never hardcode hosts, credentials or names.
4. Follow the MAPPING CONVENTIONS exactly. The infrastructure (tables, buckets, queues) is generated from the
   same conventions and already exists; never create or migrate schema in app code.
5. Return the complete new file in new_content, not a diff. Python 3.12. No placeholder comments, no TODOs.
6. If the file needs no change, return it unchanged and say so in summary.
```

---

## 3.7 Generators (B), `boomerang/generators/`

```python
def render(pam: AppModel, target: Platform, names: dict[str, str] | None = None) -> list[GeneratedFile]
# GeneratedFile(path: str, content: str), paths relative to the run dir: "infra/...", "app/.env", "app/requirements.txt"
```
Deterministic Jinja2 templates; no AI.

**local** → `infra/docker-compose.yml` (postgres:16-alpine, quay.io/minio/minio, redis:7-alpine, plus `api` and `worker` services built from a generated `app/Dockerfile`), `infra/schema.sql` (from `mappings.postgres_ddl(component)` for every kv_table), `app/.env` (local env contract), `app/requirements.txt` (`fastapi uvicorn boto3 psycopg[binary] redis pytest httpx`).

**aws** → `infra/main.tf` (provider `aws`; `aws_s3_bucket`, `aws_dynamodb_table` with `billing_mode = "PAY_PER_REQUEST"`, `hash_key`, `attribute` blocks, `global_secondary_index` blocks with `projection_type = "ALL"`; `aws_sqs_queue`), `app/.env`, `app/requirements.txt` (`fastapi uvicorn boto3 pytest httpx`).

`mappings.postgres_ddl(component) -> str` is the **single source** of the Postgres schema: the provisioner executes it and the generator writes it to `schema.sql`.

**Done when:** rendering `fixtures/pam.aws.json` for target `local` gives a compose file that passes `docker compose -f infra/docker-compose.yml config`, and a `schema.sql` that applies cleanly to the lab Postgres.

---

## 3.8 Provisioner (A), `boomerang/provisioner.py`

```python
def run_names(pam: AppModel, run_id: str) -> dict[str, str]          # component_id -> physical name
def provision(pam: AppModel, platform: Platform, names: dict[str, str], run_id: str) -> dict[str, str]
    """Create resources in the lab; return the env vars the app needs."""
def teardown(pam, platform, names, run_id) -> None
```
- Per-run names: `f"{base}-{short_run_id}"` (lowercase, hyphens; Postgres tables keep the base name inside a per-run database).
- **aws:** boto3 against `http://127.0.0.1:4566`, region `us-east-1`, keys `test/test`. `create_bucket`; `create_table` (PAY_PER_REQUEST, GSIs with `Projection={"ProjectionType": "ALL"}`) then the `table_exists` waiter; `create_queue`.
- **local:** connect to Postgres as admin → `CREATE DATABASE bmr_<short_run_id>`; connect to it and execute `postgres_ddl(c)` for every kv_table; MinIO `create_bucket` via boto3 with the S3_* settings; Redis: `DELETE` the queue key.
- Return the env dict for the platform (architecture §5) with the per-run names filled in.
- `teardown` is best-effort (never raises).

**Done when:** both flavors of the sample app pass their tests using only `provision()` + `validator.run_tests()`.

---

## 3.9 Validator (A), `boomerang/validator.py`

```python
def run_tests(app_dir: Path, env: dict[str, str], platform: Platform, attempt: int,
              timeout_s: int = 180) -> TestReport
```
- Command: `[sys.executable, "-m", "pytest", "-q", "tests", "-p", "no:cacheprovider", "--junitxml", <tmp>/junit.xml]`, `cwd=app_dir`.
- Env: copy `os.environ`, **remove** every `AWS_*`, `DATABASE_URL`, `REDIS_URL`, `S3_*` and the app's resource-name vars, then add `env` and `PYTHONPATH=app_dir`. (Keep `PATH`/`SYSTEMROOT`; Windows needs them.)
- Parse the JUnit XML with `xml.etree` into `TestReport` (trim tracebacks to 60 lines; output tail to 80 lines).
- A timeout or a crash before JUnit exists → `TestReport(errors=1, failures=[TestFailure(test="<session>", ...)])`.

The pipeline's `validate()` step = `provision()` → `run_tests()` → `teardown()`, emitting `provision` and `validate` events.

---

## 3.10 Fixer (C), `boomerang/fixer.py`

```python
def fix(app_dir: Path, report: TestReport, pam: AppModel, plan: MigrationPlan, emit: Emitter) -> FixResult
```
- User message: failing tests (message + traceback), the test file(s) involved (read-only context), all rewritten app files with line numbers, conventions, env contract.
- Apply edits with guardrails: path must exist under `app_dir`, must **not** start with `tests/`, must compile. Rejected edits → `warning` event.
- Save diffs as `diffs/fix-<attempt>__<file>.diff`.

### Prompt: `prompts/fix.md`
```
You are Boomerang's fixer. A migrated app fails some of its original tests on the target platform.
The tests are the specification: you may NOT edit anything under tests/. Fix the application code.
- diagnosis: the root cause in 1-3 sentences.
- edits: complete new content for each app file you change (only files that need changes).
Keep public signatures and return shapes. Follow the mapping conventions and the env contract.
```

---

## 3.11 Pipeline (C), `boomerang/pipeline.py`

```python
def migrate(source_dir: Path, target: Platform, *, emit_extra: Emit | None = None,
            source_platform: Platform | None = None) -> RunSummary
def baseline(source_dir: Path, platform: Platform) -> TestReport    # test the original app as-is
def roundtrip(source_dir: Path, *, emit_extra=None) -> RoundTripSummary
```
`migrate`:
1. Create `runs/<run_id>/`; set up `Emitter` with jsonl + console + optional UI sink.
2. `scan` → write `source.pam.json`. Platform defaults to the opposite of target.
3. `plan` → write `plan.json`.
4. Copy the source app to `out/app/` (skip `.venv`, `__pycache__`, `.env`).
5. `rewrite_all`; `generators.render` → write files.
6. Loop `attempt in 1..max_fix_attempts`: provision → run_tests → teardown; write `validation/attempt-N.json`; if `ok` break; else `fix`.
7. `report.write(run_dir)`; write `run.json` with status `passed`/`failed`.
Any exception → `error` event + status `error` (never crash the UI).

`roundtrip`: `baseline(aws)` → `migrate(aws→local)` → `migrate(leg1/app → aws)` → `roundtrip.json`.

---

## 3.12 CLI (C, others add commands), `boomerang/cli.py`

Typer, console script `boomerang` (declare in `pyproject.toml`).

| Command | Does |
|---|---|
| `boomerang lab up / down / status` | `docker compose -f infra/lab.compose.yml ...` (A) |
| `boomerang scan <dir> [--platform aws] [-o pam.json]` | print a component table + lock-in score (B) |
| `boomerang plan <pam.json> --to local` | print steps and notes (B) |
| `boomerang provision <pam.json> --platform aws` | create resources; print env (A) |
| `boomerang test <app_dir> --platform aws` | baseline: provision + run tests (A) |
| `boomerang migrate <dir> --to local` | full pipeline; prints run dir (C) |
| `boomerang roundtrip <dir>` | full round trip (C) |

---

## 3.13 Report (C), `boomerang/report.py`

`report.md` per run: header (source → target, status, duration), lock-in before → after, components table, plan notes, per-file change summaries, test results per attempt, warnings. Deterministic template filled from `run.json`, `plan.json`, events.
