# Role A → integration handoff (for Joao / C and the big merge)

Branch: `role-a-platform-plan`. This file describes the state of role A (Platform & Data) at the commit that adds it.
Read it before merging A + B + C. Everything below was checked on Windows 11, Python 3.12, Docker Desktop 29.9.3.

---

## 1. What is on `role-a-platform-plan`

- **All of role A, tasks A1–A6** (A7, the golden run and video, is not done; it needs the full pipeline).
- **C's branch `role-c` merged in at `845563e` (C2: LLM layer).** **Not included:** C3 `2878155` (slicer, rules, de-lock, real `sovereign_sdk/storage.py`). It comes in with your merge.
- **B's work is NOT merged into this branch.** B's branches (`integration-member-a-b`, `member-b-after-c1`) contain an **older** copy of role A (up to `483afdb`). See §4.

Conflict resolution used when merging `role-c`:
- **C's version kept** for `boomerang/models.py`, `boomerang/events.py`, `boomerang/cli.py`, `pyproject.toml`, `boomerang/__init__.py`, `sovereign_sdk/__init__.py`. A's provisional copies of these are gone.
- **A's implementation kept** for `boomerang/lab.py`, `boomerang/provisioner_aws.py`, `boomerang/verifier.py`, `boomerang/datamover.py`, `sovereign_sdk/queue.py` (C's stubs replaced), then **adapted to C's contract** (§2).
- `boomerang/cli_platform.py` and `tests/test_cli_platform.py` were **deleted**: C's CLI owns all commands and calls A's functions.

## 2. A's public API (matches docs/15 L4, L5, L8, L13, L15, L17)

| Module | Function | Notes |
|---|---|---|
| `boomerang.lab` | `up()`, `down()`, `status() -> bool`, `reset()` | `up(adapters=False, backend="auto")` also accepted. Backend `docker` (infra/lab.compose.yml, project `boomerang-lab`) or `process` (a local `python -m moto.server`) when Docker is missing. `reset()` = `POST /moto-api/reset`. Extra: `services() -> dict` (moto, test-postgres, test-rabbitmq, docker), `aws_env()`, `TEST_POSTGRES_URL`, `TEST_AMQP_URL`. |
| `boomerang.provisioner_aws` | `provision(pam, emit=None) -> dict[str,str]` | Creates bucket, table (+GSIs, projection ALL), DLQ + queue (VisibilityTimeout, RedrivePolicy, FIFO supported) in moto from the PAM. Idempotent. **Also writes `.cache/lab/aws_names.json`**, which `verifier.aws_env()` reads. Lambda/IAM/alarms are never created. Returns the env (C's contract only needs the side effect). Optional `run_tag=` isolates names. |
| | `seed(pam, count=25, emit=None, *, pending=3) -> dict` | Plain boto3 (L17), deterministic (`rng_seed=7`): `count` orders + receipts, `pending` messages left in the queue. Returns `{"orders","receipts","order_events"}` counts. |
| `boomerang.verifier` | `verify(leaseweb_dir, attempt, emit) -> Verification` | Checks: `config` (YAML/JSON parse, `docker compose config`, `bash -n deploy.sh` via Git Bash, `#cloud-config` header) → `boot` (`docker compose -p bmr-verify up -d --build --wait`) → `schema` (tables from `schema.sql` exist) → `tests` (worker **stopped** during tests, restarted after, L4) → `health` (`GET 127.0.0.1:8080/health`). Stops at the first failing infra check. |
| | `run_tests(app_dir, env, platform, attempt, emit=None) -> TestReport` | Runs `app_dir/tests` in a **subprocess**: `-c <empty pytest.ini> --rootdir <app_dir> -p no:cacheprovider --junitxml`. **Env is an allowlist** (PATH, SYSTEMROOT, TEMP/TMP, USERPROFILE, HOME, ...) + `env` + `PYTHONPATH`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`. No `ANTHROPIC_API_KEY`, no developer AWS keys. Timeout 180 s → reported as an error, not an exception. |
| | `aws_env() -> dict` | `lab.aws_env()` + names from the last `provision()`. |
| | `leaseweb_env(leaseweb_dir) -> dict` | B's `laptop.env` if present; otherwise derived from `.env.example` + `secrets.env` with container hosts rewritten to `127.0.0.1:<port>`. |
| | `reset_data(leaseweb_dir, emit=None) -> dict` | Empties tables (`DELETE`, schema kept), purges the queues listed in `rabbitmq/definitions.json`, deletes all objects in all buckets. Needs the stack running. |
| | `stop_service(dir, name)`, `start_service(dir, name)` | `docker compose stop/start <name>`. |
| | extras | `run_pytest(app_dir, tests_dir, env, platform, attempt)` (tests outside the app, e.g. `sample-app/tests`), `reset_stack()` (down -v + boot), `teardown()`, `find_bash()`. |
| `boomerang.datamover` | `move(pam, direction, src_env, dst_env, emit=None) -> DataMove` | **src/dst follow the direction** (as C's CLI passes them). kv: scan (paginated) → put_item, checksum over canonical JSON (Decimal vs int neutral); objects: list (paginated) → get → put with ContentType + Metadata, MD5 per object; queue: drain receive → send → delete. A failing component is reported (`checksum_match=False`, note), never raised. Never modifies `os.environ`: the Leaseweb side is built from `src_env`/`dst_env` (`kv.resource(env.get("DATABASE_URL"))`, `queue.Queue(url=env["AMQP_URL"])`, an S3 client from `S3_*`). Raises `MissingAdapter` if B's kv is still C's stub. |
| `sovereign_sdk.queue` | `client()`, `Queue(url=None)` with `get_queue_url`, `send_message`, `receive_message`, `delete_message`; `queue_arguments(name, max_receive_count)`, `declare()`, `dlq_name()` | RabbitMQ (pika). Lazy connect, one reconnect, RLock for FastAPI threads, `heartbeat=0`. **Expired messages are returned with `basic_reject`, not `basic_nack`:** verified on real RabbitMQ 4, `nack+requeue` never counts towards `x-delivery-limit`. `x-delivery-limit = maxReceiveCount - 1` → exactly `maxReceiveCount` receives, then DLQ (verified). Dead-lettering is **asynchronous** (poll briefly). Unsupported (raise `UnsupportedFeature`): DelaySeconds, MessageAttributes, MessageGroupId, dedup id. Missing queue → `QueueDoesNotExist`. B's generator already uses `queue_arguments()` for `rabbitmq/definitions.json`. |

Emitting: A's modules call `emit.emit(stage, message, level, **data)` (C's `Emitter`) and accept `emit=None`. Stages used: `verify` (with `step=provision|tests` or `check=`), `data` (with `direction=`, `count=`), `data` with `step=reset_data`.

## 3. ShopLite (`sample-app/aws`) and its tests

- AWS-locked FastAPI app: `app/db.py` (DynamoDB), `app/storage.py` (S3), `app/queue.py` (SQS), `app/main.py` + `app/worker.py` have **no SDK calls**.
- `infra/main.tf` has the red items: Lambda + `aws_s3_bucket_notification`, IAM role + policy, CloudWatch alarm; plus the GSI and the SQS redrive policy.
- **L13 (non-1:1 shapes, added in this commit):** `list_orders_by_customer` uses `KeyConditionExpression="customer_id = :c"` + `ExpressionAttributeValues` (not `Key(...).eq`), and `get_order` uses `get_item(..., ConsistentRead=True)`. **B's kv adapter does not accept either as-is**; that is intentional: the de-lock AI must rewrite them. Also used: `ConditionExpression="attribute_not_exists(order_id)"` (put) and `"attribute_exists(order_id)"` (set_status), `ExpressionAttributeNames={"#s": "status"}`.
- Domain errors: `db.OrderExists` → HTTP 409, `db.OrderNotFound` → the worker drops the event (no ghost order).
- **7 black-box spec tests** in `sample-app/tests` (HTTP + `app.worker.process_once` only); the pipeline copies them unchanged. 14 white-box tests in `tests/fixtures/shoplite_whitebox` (A-only, run through `verifier.run_pytest`).
- PAM fixture for tests: `tests/fixtures/pam_shoplite.json` (B's top-level `fixtures/pam.aws.json` is the official one).

## 4. Merge guidance (A + B + C)

1. **Take A's files from `role-a-platform-plan`, not from B's branches.** B's branches carry an older role A. If a conflict touches `boomerang/lab.py`, `provisioner_aws.py`, `verifier.py`, `datamover.py`, `sovereign_sdk/queue.py`, `sample-app/**`, `infra/lab.compose.yml`, or A's tests (`tests/test_lab.py`, `test_provisioner_aws.py`, `test_verifier.py`, `test_datamover.py`, `test_shoplite.py`, `tests/sovereign_sdk/test_queue.py`, `fake_amqp.py`), keep A's version.
2. **`sovereign_sdk/kv.py`:** on A's branch it is C's stub; **take B's real adapter.**
3. **`sovereign_sdk/storage.py`:** on A's branch it is C's stub; **take C3's real one.**
4. **`tests/test_stubs.py`:** A's entries are already removed. After merging B and C3, also remove the entries for anything B and C3 built, or the test fails.
5. **`pyproject.toml`:** A added to C's version: `"moto[server]"` in dependencies (the lab's process backend and the tests need it), marker `slow`, and `norecursedirs = ["fixtures", "data", ".venv", "runs"]` (tests/fixtures holds apps with their own tests). Keep these.
6. **`.gitignore`:** contains `!**/*.j2` (B's `.env.example.j2` template was being ignored). Keep it.
7. A's old `boomerang/cli_platform.py` must **not** come back from B's branches (C's CLI replaces it).

## 5. Tests and how to run them

Following C's convention, a bare `pytest` never needs Docker or a model:
- `pytest` → unit tests. **189 passed in ~35 s** on this branch (before B/C3 are merged).
- Integration: `boomerang lab up`, then `python -c "from boomerang import lab; lab.up(adapters=True)"` (adds test Postgres on `127.0.0.1:55432` and RabbitMQ on `127.0.0.1:55672`), then `pytest -m integration`. Marked `integration`: lab, provisioner, ShopLite, data mover (moto), the real-RabbitMQ variant of the queue tests, the real stack boot.
- B's kv tests need `DATABASE_URL=postgresql://test:test@127.0.0.1:55432/test`; they passed 14/14 on real Postgres.
- Docker on Windows: add `C:\Program Files\Docker\Docker\resources\bin` to PATH in Git Bash if `docker` isn't found.

**Verified for real (Docker):**
- Queue adapter vs real RabbitMQ 4: 43/43, three runs in a row (after the `reject` fix).
- Minimal stack (Postgres, RabbitMQ, SeaweedFS) boots healthy via the verifier.
- B's kv adapter vs real Postgres: 14/14.
- A + B combined (old A API + B's `integration-member-a-b`): 246 passed, 0 failed.
- ShopLite: 7/7 spec + 14 white-box on moto.

## 6. NOT done or NOT verified (be careful here)

| # | Gap | Why / what is needed |
|---|---|---|
| G1 | **The integration run of this exact commit** (adapted API) was still running when this was pushed. Unit tests are green. | Run `pytest -m integration` after the merge. |
| G2 | **Full `verify()` on B's real generated stack** with C's de-locked ShopLite (boot of `api`/`worker` images, schema, 7/7, health). | Needs B6 + C3 output in `runs/<id>/leaseweb/`; first `api` build needs network (pip). |
| G3 | **`reset_data()` has no automated test yet**, and has never run against a real stack. | Needs a booted generated stack; test after G2. |
| G4 | **Data mover against the real Leaseweb side** (B's kv on Postgres, RabbitMQ, SeaweedFS). Tested only with fake AWS on both sides (same boto3-shaped interface) and with fakes. | Needs G2. |
| G5 | **B's kv vs ShopLite's calls:** whether B's adapter supports both `attribute_not_exists` and `attribute_exists` conditions and `ExpressionAttributeNames` in `update_item` was not checked end-to-end. | Check after merging B. |
| G6 | **Round trip (C6)** never run end-to-end. C's plan rolls back into a **fresh** moto (L5): `lab.reset()` → `provision(pam)` (no seed) → `move(pam, "leaseweb->aws", leaseweb_env(), aws_env())`. A's mover supports this (and also upserting into existing tables). | Needs the pipeline. |
| G7 | **Lab weakness:** `lab.down()` only stops the moto *process* started by the same checkout (pid file in `.cache/lab`). With two checkouts or worktrees, an orphan `python -m moto.server` can keep port 4566 busy, and the Docker moto then fails to start. | Workaround: stop the process holding 4566 (`netstat -ano | findstr :4566`, `taskkill /PID <pid> /F`). |
| G8 | Not tested: macOS/Linux, `terraform validate` on `sample-app/aws/infra/main.tf`, real AWS (out of scope by design). | — |
| G9 | **A7 (golden run, backup video, submission)** not started. | Needs the whole pipeline. |
| G10 | pika prints a DeprecationWarning for `BlockingConnection` (pika 1.5). Harmless; tests run with `-W ignore::DeprecationWarning`. | — |

## 7. Bugs found and fixed along the way (useful context)
1. RabbitMQ 4: `nack+requeue` doesn't count towards the delivery limit → `reject` (the DLQ never filled).
2. After installing WSL, `bash` on PATH is WSL's launcher (fails inside docker-desktop) → `find_bash()` prefers Git Bash.
3. A lab test stopped the adapter test databases and never restarted them → later Postgres tests hung.
4. The verifier passed only `.env.example` to `docker compose` → B's `${POSTGRES_PASSWORD}` would have been empty; now `secrets.env` is passed too.
5. The test env was "os.environ minus a few keys" → now an allowlist (C's L8; no API key leaks).
6. ShopLite: duplicate order → 409; event for an unknown order no longer creates a ghost order.
7. Queue adapter: sending to a missing queue → `QueueDoesNotExist`; `delete_message` after a dropped connection is harmless.
8. `.gitignore` ignored B's `.env.example.j2` template.
