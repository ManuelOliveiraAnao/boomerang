# Role A → integration handoff (merge guide for Joao / C)

Branch: `role-a-platform-plan`. Read this before merging **A + B + C**.
Checked on Windows 11, Python 3.12, Docker Desktop 29.9.3.

> **We already rehearsed your merge.** In a throwaway local copy we merged `origin/role-c` (`2878155`, C3) + `origin/role-a-platform-plan` + `origin/member-b-after-c1` (`6625c7b`) and ran every test, unit and integration. §3 lists exactly what happened: every conflict, how to resolve it, and every test that fails after the merge and why. Nothing from that rehearsal was pushed.

---

## 1. What is on `role-a-platform-plan`

- **All of role A, tasks A1–A6.** A7 (golden run, video, submission) is not started; it needs the full pipeline.
- **`role-c` merged in at `845563e` (C2).** C3 (`2878155`) is **not** in A's branch; it comes in with your merge.
- **No B work.** B's branches (`integration-member-a-b`, `member-b-after-c1`) contain an **old copy of role A** (from `483afdb`, before the RabbitMQ fix and before A adapted to your contract).
- When `role-c` was merged into A: **C's version** was kept for `boomerang/models.py`, `events.py`, `cli.py`, `pyproject.toml`, `boomerang/__init__.py`, `sovereign_sdk/__init__.py`. A's modules (`lab.py`, `provisioner_aws.py`, `verifier.py`, `datamover.py`, `sovereign_sdk/queue.py`) replaced your stubs and were adapted to your contract. A's `boomerang/cli_platform.py` + `tests/test_cli_platform.py` were deleted (your CLI owns the commands).

## 2. A's public API (your docs/15 L4, L5, L8, L13, L14, L15, L17)

| Module | Function | Notes |
|---|---|---|
| `boomerang.lab` | `up()`, `down()`, `status() -> bool`, `reset()` | `up(adapters=False, backend="auto")`. Backend `docker` (`infra/lab.compose.yml`, project `boomerang-lab`) or `process` (local `python -m moto.server`) when Docker is missing. `reset()` = `POST /moto-api/reset`. Extras: `services() -> dict`, `aws_env()`, `TEST_POSTGRES_URL` (`127.0.0.1:55432`), `TEST_AMQP_URL` (`127.0.0.1:55672`). |
| `boomerang.provisioner_aws` | `provision(pam, emit=None) -> dict` | Bucket, table (+GSIs, projection ALL), DLQ + queue (VisibilityTimeout, RedrivePolicy; FIFO supported) in moto from the PAM. Idempotent. **Writes `.cache/lab/aws_names.json`**, which `verifier.aws_env()` reads. Never creates Lambda/IAM/alarms. Optional `run_tag=`. |
| | `seed(pam, count=25, emit=None, *, pending=3) -> dict` | Plain boto3 (L17), deterministic. Returns `{"orders","receipts","order_events"}`. |
| `boomerang.verifier` | `verify(leaseweb_dir, attempt, emit) -> Verification` | `config` (YAML/JSON, `docker compose config`, `bash -n deploy.sh` via **Git Bash**, `#cloud-config`) → `boot` (`docker compose -p bmr-verify … up -d --build --wait`, with `--env-file .env.example --env-file secrets.env`) → `schema` → `tests` (**worker stopped** during tests, restarted after, L4) → `health` (`GET 127.0.0.1:8080/health`). Stops at the first failing infra check. |
| | `run_tests(app_dir, env, platform, attempt, emit=None) -> TestReport` | `app_dir/tests` in a **subprocess** with `-c <empty pytest.ini> --rootdir <app_dir> -p no:cacheprovider --junitxml`. **Env = allowlist** (PATH, SYSTEMROOT, TEMP/TMP, USERPROFILE, HOME, …) + `env` + `PYTHONPATH`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`. No `ANTHROPIC_API_KEY`, no developer AWS keys. Timeout 180 s → reported, not raised. |
| | `aws_env()`, `leaseweb_env(leaseweb_dir)` | AWS: lab env + names of the last `provision()`. Leaseweb: B's `laptop.env`; fallback `.env.example` + `secrets.env` with hosts rewritten to `127.0.0.1:<port>`. |
| | `reset_data(leaseweb_dir, emit=None) -> dict` | `DELETE` rows of every table in `schema.sql` (schema kept), purge queues in `rabbitmq/definitions.json`, delete all objects. Needs the stack running. **No test yet** (G3). |
| | `stop_service(dir, name)`, `start_service(dir, name)` | `docker compose stop/start`. Extras: `run_pytest()`, `reset_stack()`, `teardown()`, `find_bash()`. |
| `boomerang.datamover` | `move(pam, direction, src_env, dst_env, emit=None) -> DataMove` | **src/dst follow the direction.** kv: paginated scan → put_item, type-neutral checksum; objects: paginated list → get → put (ContentType + Metadata), MD5 each; queue: drain receive → send → delete. A failing component is reported, never raised. Leaseweb side built from the env dicts (`kv.resource(env.get("DATABASE_URL"))`, `queue.Queue(url=…)`, S3 client from `S3_*`); never touches `os.environ`. `MissingAdapter` if kv is still a stub. |
| `sovereign_sdk.queue` | `client()`, `Queue(url=None)`: `get_queue_url(QueueName)`, `send_message(QueueUrl, MessageBody)`, `receive_message(QueueUrl, MaxNumberOfMessages=1, WaitTimeSeconds=0, VisibilityTimeout=None)`, `delete_message(QueueUrl, ReceiptHandle)`; `queue_arguments()`, `declare()`, `dlq_name()` | **Explicit signatures, no `**kwargs`**, so your rule 7 sees unsupported arguments (fixed after the rehearsal; see §3.3). Lazy connect, one reconnect, RLock, `heartbeat=0`. Expired messages → **`basic_reject`** (RabbitMQ 4 ignores `nack` for the delivery limit; verified). `x-delivery-limit = maxReceiveCount - 1` → exactly `maxReceiveCount` receives, then DLQ (verified; dead-lettering is asynchronous). Missing queue → `QueueDoesNotExist`. B's generator uses `queue_arguments()`. |

A's modules emit with `emit.emit(stage, message, level, **data)` and accept `emit=None`. Stages: `verify` (`step=provision|tests`, `check=`), `data` (`direction=`, `count=`; `step=reset_data`).

## 3. The rehearsed merge: what will happen and what to do

### 3.1 Recommended order
1. Start from `role-c` (your latest).
2. `git merge origin/role-a-platform-plan` → **no conflicts** in the rehearsal.
3. `git merge origin/member-b-after-c1` → **8 conflicts** (§3.2).
4. Fix the tests in §3.4, then run §4.

### 3.2 Conflicts when merging B (all 8 resolve the same way)
B's branch still has A's **old** provisional copies of your shared files and A's **old** modules. For every one of these, **keep the side that is already in your branch** (C + A), e.g. `git checkout --ours <file>`:

| File | Why B's side is wrong |
|---|---|
| `boomerang/__init__.py`, `sovereign_sdk/__init__.py`, `boomerang/events.py`, `boomerang/cli.py` | B's side = A's old provisional scaffold (callable `Emitter`, no `.emit`; old CLI) |
| `boomerang/lab.py`, `provisioner_aws.py`, `verifier.py`, `datamover.py` | B's side = A's old API (`status()` dict, `run_pytest`/`laptop_env`/`freeze`, `move(aws_env, lw_env)`, nack-based queue code paths) |

Checked in the rehearsal after resolving: **no A file was silently reverted** by the auto-merge (the RabbitMQ `reject` fix, A's tests, ShopLite and the lab are intact), **`cli_platform.py` did not come back**, and **`sovereign_sdk/kv.py` (B) and `storage.py` (C3) are the real implementations.** Still worth a quick `git diff origin/role-a-platform-plan -- sovereign_sdk/queue.py boomerang/verifier.py tests/sovereign_sdk/test_queue.py` after your merge: it should be empty.

### 3.3 Issue found in the rehearsal and already fixed on A's branch
**Your rule 7 test failed** (`tests/test_rules.py::test_rule_7_adapter_calls[... MessageAttributes={} ...]`): rule 7 reads the adapter signatures with `inspect.signature`, and A's queue methods took `**kwargs`, so every argument looked supported. **Fixed in A's latest commit:** explicit signatures (exactly your stub's). With that, rule 7 passes. Make sure you merge A's **latest** commit.

### 3.4 Tests that fail after the merge: all stale "still a stub" assertions in C's tests
Rehearsal result: **unit `pytest` → 441 passed, 12 failed** (with A's latest queue fix). All 12 assert that **B's** code is still a C1 stub, which it no longer is:

| Test | Fix |
|---|---|
| `tests/test_stubs.py::test_stub_raises_not_built_with_owner[...]` × 9: `boomerang.scanner.scan`, `scanner.static.scan_static`, `assess.assess`, `lockin.score`, `generators.leaseweb.make_secrets`, `generators.leaseweb.render`, `lost.build`, `sovereign_sdk.kv.resource`, `sovereign_sdk.kv.ddl` | Remove these entries from `STUBS` (A's entries are already removed). |
| `tests/test_stubs.py::test_adapter_classes_are_stubbed_too` | `kv.Table()` is real now (it needs a connection argument → `TypeError`). Delete the test or keep only what's still a stub. |
| `tests/test_cli.py::test_unbuilt_command_exits_3_and_names_the_owner` | Uses a B command that is built now (exits 0). Point it at a command that is still a stub, or drop it. |
| `tests/test_delock.py::test_events_use_the_delock_stage_and_end_with_a_summary` | Last assertion expects the warning "scan_static is not built yet" (B's stub). Remove that assertion. |

**Integration `pytest -m integration` → 58 passed, 0 failed, 1 skipped.** The skip is your `tests/sovereign_sdk/test_storage.py` (needs `S3_TEST_ACCESS_KEY_ID` / `S3_TEST_SECRET_ACCESS_KEY` for an S3 endpoint, e.g. a running SeaweedFS). Also skipped by design in unit runs: `tests/test_datamover.py::test_leaseweb_side_without_kv_adapter_is_a_clear_error` ("B's real kv adapter is present").

B's kv tests need `DATABASE_URL`; without it they skip silently. With `DATABASE_URL=postgresql://test:test@127.0.0.1:55432/test` they pass.

### 3.5 Other things to keep
- `pyproject.toml`: A's additions to yours: `"moto[server]"` (lab process backend + tests), marker `slow`, `norecursedirs = ["fixtures", "data", ".venv", "runs"]` (tests/fixtures holds apps with their own tests; without it pytest collects them and errors).
- `.gitignore`: `!**/*.j2` (B's `.env.example.j2` was being ignored).

## 4. How to run the tests after the merge

```
pytest                                           # unit: no Docker, no model (~2 min after merge)
boomerang lab up
python -c "from boomerang import lab; lab.up(adapters=True)"   # test Postgres 55432 + RabbitMQ 55672
set DATABASE_URL=postgresql://test:test@127.0.0.1:55432/test   # bash: export DATABASE_URL=...
pytest -m integration                            # moto, real RabbitMQ, real Postgres, stack boot (~3 min)
```
Expected: unit all green after §3.4; integration 58 passed + 1 skipped (storage).
Windows/Git Bash: if `docker` isn't found, add `C:\Program Files\Docker\Docker\resources\bin` to PATH.

**Test discrepancies to be aware of:**
- **A bare `pytest` skips everything marked `integration`** (your `addopts`). A's lab/provisioner/ShopLite/moto data-mover tests, the real-RabbitMQ queue tests and the stack boot only run with `-m integration`. "All green" on `pytest` alone says nothing about Docker paths.
- **Counts differ by environment:** B's kv tests skip without `DATABASE_URL`; the storage test skips without S3 credentials; the real boot test skips without Docker.
- **Don't run two integration runs at once** (e.g. two checkouts): both use moto on 4566 and `lab.reset()` wipes the other's state. See G7.
- A's 1,030-object S3 test is marked `slow` + `integration` (~2½ min).

## 5. ShopLite (`sample-app/aws`)

- `app/db.py` (DynamoDB), `app/storage.py` (S3), `app/queue.py` (SQS); `main.py`/`worker.py` have no SDK calls. `infra/main.tf` has the red items (Lambda + S3 notification, IAM role + policy, CloudWatch alarm), the GSI and the redrive policy.
- **L13 shapes:** `list_orders_by_customer` uses `KeyConditionExpression="customer_id = :c"` + `ExpressionAttributeValues`; `get_order` uses `ConsistentRead=True`. **B's kv rejects both on purpose** (string condition → `UnsupportedFeature`, `ConsistentRead` → not in the signature, so rule 7 flags it): the de-lock AI must rewrite them.
- Also used, and **supported by B's kv** (checked in the rehearsal): `ConditionExpression="attribute_not_exists(order_id)"`, `"attribute_exists(order_id)"`, `ExpressionAttributeNames={"#s": "status"}` in `update_item`, boto3 `Key(...).eq(...)` in `query`, and `ConditionalCheckFailedException` is a real `botocore` `ClientError` (ShopLite's 409 handling works).
- **B fragility to know about:** B's kv only understands `attribute_(not_)exists(order_id)` / `(#pk)`: the key name `order_id` is hard-coded. Fine for ShopLite, wrong for other apps.
- 7 black-box spec tests (`sample-app/tests`, copied unchanged by the pipeline); 14 white-box tests in `tests/fixtures/shoplite_whitebox` (run via `verifier.run_pytest`). PAM test fixture: `tests/fixtures/pam_shoplite.json` (B's `fixtures/pam.aws.json` is the official one).

## 6. NOT done or NOT verified

| # | Gap | What's needed |
|---|---|---|
| G1 | ~~Integration of the adapted API~~ **Done**: A alone 57/57; rehearsed A+B+C 58 passed, 1 skipped. | Re-run after your merge. |
| G2 | **Full `verify()` on B's real generated stack** with C's de-locked ShopLite (api/worker images, schema, 7/7, health). | B6 + C3 output in `runs/<id>/leaseweb/`; first `api` build needs network (pip). |
| G3 | **`reset_data()` has no test** and never ran on a real stack. | After G2. |
| G4 | **Data mover vs the real Leaseweb side** (B's kv on Postgres, RabbitMQ, SeaweedFS). Tested with moto on both sides and with fakes. | After G2. |
| G5 | ~~B's kv vs ShopLite's calls~~ **Checked** (§5): compatible after the AI rewrite; note the hard-coded `order_id`. | — |
| G6 | **Round trip (C6)** never run end-to-end. Your L5 plan: `lab.reset()` → `provision(pam)` (no seed) → `move(pam, "leaseweb->aws", leaseweb_env(), aws_env())`. A's mover supports it. | The pipeline. |
| G7 | **Lab:** `lab.down()` only stops the moto *process* its own checkout started (pid file in `.cache/lab`). With two checkouts/worktrees an orphan `python -m moto.server` can hold port 4566 and the Docker moto fails to start. | `netstat -ano | findstr :4566` → `taskkill /PID <pid> /F`. |
| G8 | Not tested: macOS/Linux, `terraform validate` on `sample-app/aws/infra/main.tf`, real AWS (out of scope). | — |
| G9 | **A7** (golden run, backup video, submission) not started. | The whole pipeline. |
| G10 | pika `BlockingConnection` DeprecationWarning (pika 1.5). Harmless; use `-W ignore::DeprecationWarning`. | — |

## 7. Bugs found and fixed (context)
1. RabbitMQ 4: `nack+requeue` doesn't count towards the delivery limit → `reject` (the DLQ never filled).
2. Queue adapter `**kwargs` hid unsupported arguments from rule 7 → explicit signatures.
3. After installing WSL, `bash` on PATH is WSL's launcher → `find_bash()` prefers Git Bash.
4. A lab test stopped the adapter test databases → later Postgres tests hung.
5. The verifier passed only `.env.example` to `docker compose` → empty `${POSTGRES_PASSWORD}`; `secrets.env` added.
6. Test env was "os.environ minus a few keys" → allowlist (L8).
7. ShopLite: duplicate order → 409; unknown-order event no longer creates a ghost order.
8. Queue: missing queue → `QueueDoesNotExist`; `delete_message` after a dropped connection is harmless.
9. `.gitignore` ignored B's `.env.example.j2`.
