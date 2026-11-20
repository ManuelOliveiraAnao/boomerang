# 03 · Components (v2)

Each section: **owner · interface · behavior · done when**. Interfaces are binding; internals are up to the owner.

---

## 3.1 Config & events (C)

`config.py` settings (from `.env`):

| Setting | Env | Default |
|---|---|---|
| provider | `BOOMERANG_LLM_PROVIDER` | `ollama` (`anthropic` = fallback) |
| model | `BOOMERANG_MODEL` | `qwen2.5-coder:7b` (fallback provider: `claude-opus-5-5`) |
| ollama host | `OLLAMA_HOST` | `http://127.0.0.1:11434` |
| llm mode | `BOOMERANG_LLM_MODE` | `record` (`live` · `record` · `replay`) |
| cache dir / runs dir | `BOOMERANG_CACHE_DIR` / `BOOMERANG_RUNS_DIR` | `.cache/llm` / `runs` |
| fix attempts | `BOOMERANG_MAX_FIX_ATTEMPTS` | `2` |
| auto-approve | `BOOMERANG_AUTO_APPROVE` | `false` (CLI `--yes` sets it) |

`events.py`: `Emitter(run_id, sinks)`; `emit(stage, message, level="info", **data)`; sinks: JSONL file, rich console, UI callback.

---

## 3.2 LLM layer (C), `boomerang/llm.py`

```python
def structured(*, system: str, user: str, schema: type[T], label: str) -> T
```
1. `user = redact(user)` and `system = redact(system)` (see [13](13-security.md)). Always.
2. Cache key = sha256(provider, model, system, user, schema JSON). `record`/`replay` read the cache; `replay` raises `CacheMiss` on a miss.
3. **Ollama** (default):
```python
import ollama
resp = ollama.chat(
    model=settings.model,
    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    format=schema.model_json_schema(),          # JSON-schema constrained output
    options={"temperature": 0, "num_ctx": 8192},
    keep_alive="60m",                            # no unload/reload freeze mid-demo
)
result = schema.model_validate_json(resp.message.content)
```
   On a validation error: retry once with the error appended; then raise.
4. **Anthropic** (fallback, only if Q1 benchmark fails): `client.beta.messages.parse(model="claude-opus-5-5", max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default", system=..., messages=[...], output_format=schema)`; check `stop_reason` for `refusal`/`max_tokens`; use `response.parsed_output`. Drop the two fallback lines if the installed SDK rejects them.
5. Save to cache; emit token counts/duration in event data.

Rules: **one model for every task** (no model swapping). Prompts stay under ~6k tokens: send functions, not files. Every schema starts with a reasoning field (`analysis`, `root_cause_analysis`).

**Done when:** a toy call returns a parsed object on the presenter laptop in < 60 s; a repeat is served from cache; replay raises on a miss.

---

## 3.3 Scanner (B), `boomerang/scanner/`

```python
def scan(app_dir: Path, emit) -> AppModel
```
**Static hints (`static.py`)**, over `*.py`, `*.tf`, `serverless.yml`, `template.yaml` (SAM), `Dockerfile`, `.env.example`, `requirements*.txt`:
- Python: `ast` walk → every call whose receiver comes from `boto3.client/resource("<svc>")`; record file, line, **enclosing function**, operation. Regex fallback for anything `ast` misses.
- Terraform: regex on `resource "aws_<type>" "<name>"` blocks (bucket, dynamodb_table + GSIs + attributes, sqs_queue + `redrive_policy`/`visibility_timeout_seconds`, lambda_function, s3_bucket_notification, iam_role/policy, cloudwatch_metric_alarm).
- Dockerfile/.env: AWS env vars, `AWS_*`, region, instance-metadata assumptions (no keys anywhere = implicit IAM role → 🔴 finding).

**AI pass (`ai.py`)**: hints JSON + numbered snippets around each hint (± 15 lines) + IaC blocks → `AppModel` (prompt `prompts/scan.md`). **Merge**: every static hint must be in some component's usages; add missing ones with a warning.

**Done when:** `boomerang scan sample-app/aws` → 3 data components + lambda trigger + IAM role, all usages with enclosing functions; matches `fixtures/pam.aws.json`.

---

## 3.4 Traffic lights & lock-in (B), `boomerang/assess.py`, `lockin.py`

```python
def assess(pam: AppModel, emit) -> Assessment
```
1. Rules table ([02 §4](02-architecture.md)) → one `Finding` per (component, feature) with the light.
2. One AI call (`prompts/assess.md`): fill `analysis` + `what_changes` for each finding, specific to this code. Rules check: the AI's light is accepted only if it is the **same or worse**.
3. `lockin.score(pam)` as in v1 (weighted by usage count; DynamoDB 90, SQS 70, S3 35, Lambda 95, IAM 80; Leaseweb providers 5–10).

---

## 3.5 Approval gate (C + D)

After scan + assess the pipeline stops with status `awaiting_approval`, writes `assessment.json`, and waits. UI shows **Approve plan** (D); CLI asks or uses `--yes`. Nothing is written into `leaseweb/` before approval. Red findings are listed as "will NOT be migrated".

---

## 3.6 De-lock (C), `slicer.py` + `delock.py`

```python
def delock(app_dir: Path, pam: AppModel, assessment: Assessment, emit) -> list[FunctionRewrite]
```
1. **Slice:** for each app file, `ast.parse`; collect `FunctionDef`s that contain a usage (line within `lineno..end_lineno`).
2. **S3 is config only:** replace `boto3.client("s3")` with `sovereign_sdk.storage.s3_client()` deterministically (no AI). Emit "🟢 config change".
3. **AI per function** (`prompts/delock.md`): input = the function source + the adapter interface (~50 lines from [02 §5](02-architecture.md)) + the component's PAM entry + module-level names it uses. Output `FunctionRewrite`.
4. **Rules check** (`rules.py`, see [13](13-security.md)): `new_source` parses, defines the same function name with the same parameters, contains no forbidden calls/imports, touches no other function.
5. **Stitch:** replace lines `lineno..end_lineno` (bottom-up so line numbers stay valid); fix module-level clients deterministically (`boto3.resource("dynamodb")` → `kv.resource()`, `boto3.client("sqs")` → `queue.client()`), add `from sovereign_sdk import kv, queue, storage`.
6. `unsupported` items become extra 🔴/🟠 findings. Write diffs. Run up to 3 functions concurrently only if the provider is Anthropic; with Ollama run sequentially (one GPU).

### Prompt `prompts/delock.md`
```
You migrate ONE Python function from boto3 to the sovereign_sdk adapter shown below.
The adapter mimics boto3 signatures and return shapes, so most calls map 1:1.
Rules:
- Keep the function name, parameters, return value shape and behavior identical.
- Use only the adapter methods listed. If the function needs something the adapter does not support,
  keep the closest correct behavior and list it in `unsupported`.
- Do not add imports, network calls, file access or subprocesses. Do not touch other functions.
- Start with `analysis`: what the function does and which calls change. Then `new_source`: the full function.
```

---

## 3.7 Leaseweb generator (B), `generators/leaseweb.py`

```python
def render(pam: AppModel, secrets: dict[str, str]) -> list[GeneratedFile]   # paths under runs/<id>/leaseweb/
```
Templates (Jinja2), no AI. Details and file contents in [14 Leaseweb target](14-leaseweb-target.md):
`docker-compose.yml` (postgres, rabbitmq, seaweedfs, api, worker, backup), `.env.example`, `secrets.env` (random passwords from `secrets.token_urlsafe`), `schema.sql` (`kv.ddl`), `rabbitmq/definitions.json` (vhost, user, quorum queue + DLQ), `seaweedfs/s3.json` (bucket-scoped identity), `cloud-init.yaml`, `deploy.sh`, `backup/pg_backup.sh`, `DEPLOY.md`, `app/Dockerfile`, `app/requirements.txt`. Also copies `sovereign_sdk/` into `app/`.

**Done when:** `docker compose -f runs/<id>/leaseweb/docker-compose.yml config` passes; files render for `fixtures/pam.aws.json`.

---

## 3.8 Verifier (A), `boomerang/verifier.py`

```python
def verify(leaseweb_dir: Path, attempt: int, emit) -> Verification
```
Checks in order, each a `Check` (stop at first failure, except tests always report details):
1. **config:** `docker compose config -q`; parse YAML; `bash -n deploy.sh` (if bash exists); JSON files parse.
2. **boot:** `docker compose -p bmr-verify up -d --build --wait` (timeout 180 s).
3. **schema:** `schema.sql` applied (by the compose init) → query `information_schema.tables`.
4. **tests:** run the **original** tests in-process against the booted services: `python -m pytest -q tests --junitxml ...` with `cwd=leaseweb/app`, env = Leaseweb vars pointing at `127.0.0.1` ports, all `AWS_*` removed.
5. **health:** `GET http://127.0.0.1:8080/health` on the `api` container → 200.
Leave the stack running for the data step; `down -v` at the end of the run (or on the next verify).

---

## 3.9 Fixer (C), `boomerang/fixer.py`

On failed tests: map each failure's traceback to the app function it ends in (file + line → function via the slicer). One AI call per failing function (`prompts/fix.md`, schema `FixResult` with `root_cause_analysis` first), rules check, stitch, re-verify. Never touches `tests/` or `sovereign_sdk/` (if the bug is in the adapter, that's a human fix: emit an error). Max 2 attempts.

---

## 3.10 AWS provisioner (A), `boomerang/provisioner_aws.py`

Creates the sample app's resources in moto from the PAM: bucket, table (PAY_PER_REQUEST, GSIs projection ALL), DLQ + queue with `RedrivePolicy` and `VisibilityTimeout`. Seeds demo data (`--seed 25` orders via the AWS app's own API in-process). Used for the baseline and for the rollback leg.

---

## 3.11 Data mover (A), `boomerang/datamover.py`

```python
def move(pam: AppModel, direction, src_env: dict, dst_env: dict, emit) -> DataMove
```
| Kind | AWS → Leaseweb | Leaseweb → AWS | Check |
|---|---|---|---|
| kv_table | boto3 `scan` (paginated) → `kv.Table.put_item` | `kv.Table.scan` → boto3 `put_item` (batch_writer) | counts; sha256 of sorted canonical JSON per side |
| object_store | `list_objects_v2` → `get_object` → `put_object` (same key, ContentType) | reverse | counts; MD5 of bodies |
| queue | receive → send → delete, until empty (pending messages only) | reverse | counts moved; note "in-flight messages at cut-over" |

Numbers are normalized (Decimal ↔ int/float) before hashing, so the checksum reflects data, not types. Any normalization is recorded in `notes` (→ "what got lost").

---

## 3.12 Round trip & "what got lost" (C pipeline, B `lost.py`)

`pipeline.roundtrip(run_id)`:
1. Create one new order **on Leaseweb** through the de-locked app (proves data written after migration comes back).
2. Fresh moto resources (`provisioner_aws`), `datamover.move(leaseweb->aws)`.
3. Run the **original** AWS code's tests against moto; health = import + `TestClient` `/health`.
4. Compare counts: AWS-after == AWS-before + 1 order.
5. `lost.build(pam, assessment, data_moves)` → `list[LostItem]`:
   - from findings: 🔴 → `lost` (e.g. S3 → Lambda trigger, IAM role), 🟠 → `changed` with `what_changes` (e.g. SQS visibility timeout emulated by the adapter; DLQ via RabbitMQ delivery-limit),
   - from data notes: type normalizations, object metadata not copied, in-flight messages,
   - everything else → `preserved`.

---

## 3.13 Costs (D), `boomerang/cost.py`

See [12 Costs](12-costs.md). `def estimate(prices, usage, ops) -> CostReport` (pure function, unit-tested).

---

## 3.14 CLI

| Command | Owner |
|---|---|
| `boomerang lab up/down/status` | A |
| `boomerang scan <dir>` (table with lights) | B |
| `boomerang migrate <dir> [--yes]` → scan, assess, approve, de-lock, generate, verify, data | C |
| `boomerang verify <run_id>` | A |
| `boomerang data <run_id> --direction aws->leaseweb` | A |
| `boomerang roundtrip <run_id>` | C |
| `boomerang cost [--usage file.yaml]` | D |
