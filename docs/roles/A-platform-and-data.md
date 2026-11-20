# Role A · Platform & Data: build plan

**Mission:** make things *run* and make things *move*. A owns the sample app everyone else works on, the fake AWS, the RabbitMQ adapter, the live verification of the Leaseweb setup, and the data moves in both directions.

**Tasks:** A1 lab · A2 ShopLite · A3 AWS provisioner + seed · A4 `sovereign_sdk.queue` · A5 verifier · A6 data mover · A7 golden run + video + submission ([07](../07-team-and-tasks.md)).

> ⚠️ **Top risk for this role: Docker.** Every A task needs Docker. The laptop this repo was created on has **no Docker installed**. Install Docker Desktop (with WSL2 on Windows) and pull the images **before** the event (§1).

---

## 0. What A delivers to the others (and when)

| Who needs it | What | By |
|---|---|---|
| B (scanner), C (de-lock) | ShopLite code on `main`: real AWS calls, Terraform with all red items | **T+1:15** |
| Everyone | `boomerang lab up` (moto) | T+0:50 |
| B (generator) | Agreed ports + `queue_arguments()` (§4.4) | T+0:15 (kickoff) |
| C (pipeline) | `provision()`, `run_pytest()`, `verify()`, `move()`, `freeze()`, `reset_stack()` with the signatures in §8 | stubs at T+1:00, real by CP2 |
| C (round trip) | data mover in both directions | T+4:00 |

Push **stub functions with the final signatures early** (they return fixture data or raise `NotImplementedError`), so C can wire the pipeline before the real ones exist.

---

## 1. Before the event (setup only, no code)

- [ ] Docker Desktop installed, running, WSL2 enabled; `docker compose version` ≥ 2.17 (needed for `--wait-timeout`).
- [ ] Images pulled:
  ```powershell
  docker pull motoserver/moto:latest
  docker pull postgres:16-alpine
  docker pull rabbitmq:4-management-alpine
  docker pull chrislusf/seaweedfs:latest
  docker pull python:3.12-slim
  ```
- [ ] Repo cloned to `C:\dev\boomerang` (outside OneDrive).
- [ ] Python 3.12 + uv; `pip download` not needed (uv installs fast), but run one `uv pip install boto3 pika psycopg[binary] fastapi httpx pytest` in a scratch venv so the wheel cache is warm.
- [ ] Read: moto server mode (`/moto-api/reset`), RabbitMQ quorum queues (`x-delivery-limit`, dead-lettering), pika `BlockingConnection` (`basic_get`, `basic_ack`, `basic_nack`, `confirm_delivery`).
- [ ] One person runs a full `docker build` of a FastAPI image once (pip downloads get cached in Docker's build cache, so the `api` container builds fast during the event).

---

## 2. Timeline for A (6 hours)

| Time | Task | Output | Check |
|---|---|---|---|
| 0:00–0:15 | Kickoff | Agree §4.4 and §8 contracts with B and C | written in the channel |
| 0:15–0:30 | **A1** lab compose | `infra/lab.compose.yml` (moto + `adapters` profile) | `docker compose ... up -d`, boto3 `list_buckets()` works |
| 0:30–1:15 | **A2** ShopLite | `sample-app/aws/**`, `sample-app/tests/**` | **push by 1:15** (B and C are waiting) |
| 0:45–0:50 | A1b `lab.py` once C1 scaffold lands | `boomerang lab up/down/status/reset` | `boomerang lab status` → ✅ |
| 1:15–1:40 | **A3** provisioner + seed | `boomerang/provisioner_aws.py` | ShopLite **7/7 on moto** |
| 1:40–2:35 | **A4** queue adapter | `sovereign_sdk/queue.py` + tests | by CP1 (2:00): send/receive/delete green; by 2:35: visibility + DLQ green |
| 2:35–3:20 | **A5** verifier | `boomerang/verifier.py` | runs against `fixtures/leaseweb-min/` → then B's real output |
| 3:20–3:30 | Integrate with B6 + C4 | — | CP2 command |
| 3:30–4:15 | **A6** data mover (both directions) | `boomerang/datamover.py` | 25 orders / 25 receipts / 3 msgs, checksums ✅ |
| 4:15–4:30 | Round-trip support for C | freeze + copy back | CP3 |
| 4:30–5:15 | **A7** golden run, backup video | `runs/golden/`, video | committed / uploaded |
| 5:45 | Submission | form | ✅ |

**This is ~20 minutes over budget.** If behind, cut in this order: (1) queue part of the data mover (report "drained before cut-over"), (2) visibility-timeout emulation (mark it 🔴 "not emulated"; keep ack + DLQ), (3) `api` container health check (health via `TestClient` instead), (4) ask C or B to take A7.

---

## 3. A1: Lab (fake AWS) — 15 min + 5 min CLI

### `infra/lab.compose.yml`
```yaml
name: boomerang-lab
services:
  moto:
    image: motoserver/moto:latest
    ports: ["127.0.0.1:4566:5000"]

  # Only for adapter unit tests (A4, B5). Separate ports so they never clash
  # with the generated Leaseweb stack (5432/5672) the verifier boots.
  test-postgres:
    profiles: ["adapters"]
    image: postgres:16-alpine
    environment: { POSTGRES_USER: test, POSTGRES_PASSWORD: test, POSTGRES_DB: test }
    ports: ["127.0.0.1:55432:5432"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U test"], interval: 2s, retries: 30 }
  test-rabbitmq:
    profiles: ["adapters"]
    image: rabbitmq:4-management-alpine
    ports: ["127.0.0.1:55672:5672"]
    healthcheck: { test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"], interval: 3s, retries: 40 }
```

### `boomerang/lab.py`
```python
COMPOSE = ["docker", "compose", "-f", "infra/lab.compose.yml", "-p", "boomerang-lab"]
AWS_ENDPOINT = "http://127.0.0.1:4566"

def up(adapters: bool = False) -> None        # up -d (+ --profile adapters --wait); then wait_for_moto()
def down() -> None                             # down -v
def status() -> dict[str, bool]                # {"moto": ..., "test-postgres": ..., "test-rabbitmq": ...}
def reset_moto() -> None                       # POST http://127.0.0.1:4566/moto-api/reset  (wipes all fake AWS state)
def wait_for_moto(timeout_s=30) -> None        # poll boto3 s3.list_buckets() until it works
def aws_env() -> dict[str, str]                # AWS_ENDPOINT_URL, AWS_REGION/DEFAULT_REGION=us-east-1, keys test/test
```
CLI: `boomerang lab up [--adapters]`, `down`, `status`, `reset`. All subprocess calls use list args, `check=True`, and turn a missing `docker` into a clear message ("Docker is not installed or not running").

**Done when:** `boomerang lab up` → `boomerang lab status` shows moto ✅ in < 10 s; `reset` empties it.

---

## 4. Contracts to agree at kickoff

### 4.1 Ports of the generated Leaseweb stack (B's compose, A's verifier)
`127.0.0.1:5432` postgres · `127.0.0.1:5672` + `127.0.0.1:15672` rabbitmq · `127.0.0.1:8333` seaweedfs S3 · `127.0.0.1:8080` api. Compose project name for verification: `bmr-verify`.

### 4.2 Laptop env file
Ask B to write **`laptop.env`** next to `secrets.env`: the same variables as the containers use, but with `127.0.0.1:<port>` instead of service names. The verifier and data mover read it. Fallback if B runs out of time: the verifier builds it from `secrets.env` + the port map above (§6.2).

### 4.3 Lazy connections
All adapters (A's queue, B's kv, C's storage) **connect on first use, not at import time**. Otherwise `import app.main` fails in pytest before the stack is up.

### 4.4 Queue arguments (shared with B's `rabbitmq/definitions.json`)
```python
def queue_arguments(name: str, max_receive_count: int | None) -> dict:
    args = {"x-queue-type": "quorum"}
    if max_receive_count:
        args |= {"x-delivery-limit": max_receive_count - 1,      # verify the off-by-one in test_dead_letter (§7.3)
                 "x-dead-letter-exchange": "",                    # default exchange routes by queue name…
                 "x-dead-letter-routing-key": f"{name}-dlq"}      # …to the DLQ queue
    return args
def declare(channel, name: str, max_receive_count: int | None) -> None   # declares DLQ (quorum) then the queue
```
B must generate `definitions.json` with **exactly** these arguments, or RabbitMQ answers `PRECONDITION_FAILED` when the adapter declares. Simplest: B's generator imports `sovereign_sdk.queue.queue_arguments`.

---

## 5. A2: ShopLite — 45 min

Spec: [04 Sample app](../04-sample-app.md). Build order and per-file notes:

| File | Notes |
|---|---|
| `app/db.py` | `dynamodb = boto3.resource("dynamodb")`, `_table()` reads `ORDERS_TABLE`. `put_order` uses `ConditionExpression="attribute_not_exists(order_id)"`. `get_order` → `.get("Item")`. `list_orders_by_customer` → `query(IndexName="customer_id-index", KeyConditionExpression=Key("customer_id").eq(cid))`. `set_status` → `update_item(UpdateExpression="SET #s = :s, processed_at = :p", ExpressionAttributeNames={"#s": "status"}, ...)`. `_from_ddb()` converts `Decimal` → `int` (recursively for `items`). |
| `app/storage.py` | `s3 = boto3.client("s3")`; `put_receipt` → `put_object(..., ContentType="application/json")`; `get_receipt` catches `s3.exceptions.NoSuchKey` → `None`. |
| `app/queue.py` | `sqs = boto3.client("sqs")`; cache `get_queue_url`; `publish_order_created` → `send_message(MessageBody=json.dumps({"order_id": ...}))`; `receive_one(wait)` → `receive_message(MaxNumberOfMessages=1, WaitTimeSeconds=wait, VisibilityTimeout=30)`, use `resp.get("Messages", [])`, return `(order_id, ReceiptHandle)` or `None`; `ack(handle)` → `delete_message`. |
| `app/main.py` | FastAPI, Pydantic request model with `items: list[Item]` (`min_length=1`), `qty: int = Field(gt=0)`. **Sync** route functions (`def`, not `async def`) because boto3 is blocking. 201 on create. |
| `app/worker.py` | `process_once(wait_seconds=1)`, `main()` loop. |
| `sample-app/tests/` | 7 tests from [04 §4](../04-sample-app.md); `conftest.py` only provides `client = TestClient(app)` fixture. No env reading, no setup of AWS resources inside tests. |
| `infra/main.tf` | Every resource in [04 §3](../04-sample-app.md), including the Lambda + bucket notification, IAM role + policy, CloudWatch alarm on the DLQ. It's never applied; it must be realistic for the scanner. |
| `Dockerfile` | `python:3.12-slim`, no AWS keys, `CMD uvicorn app.main:app`. |
| `.env.example`, `requirements.txt` | base names; `fastapi uvicorn boto3 httpx pytest`. |

Run locally (after A3):
```powershell
cd sample-app/aws
$env:PYTHONPATH = "."
python -m pytest -q ../tests
```

**Push to `main` at 1:15 even if the tests aren't green yet**, because B and C need the source. Write "tests pending A3" in the commit.

---

## 6. A3: AWS provisioner + seed — 25 min

### `boomerang/provisioner_aws.py`
```python
def provision(pam: AppModel, *, seed: int = 0, run_tag: str | None = None) -> dict[str, str]
    """Create every object_store / kv_table / queue component of the PAM in moto.
    Returns the env the AWS app needs (lab.aws_env() + name vars)."""
def seed_shoplite(env: dict[str, str], n_orders: int = 25, pending: int = 3, rng_seed: int = 7) -> dict
    """Deterministic demo data: n orders over 5 customers, receipts, n-pending processed,
    `pending` messages left in the queue. Returns counts for the UI."""
def counts(pam: AppModel, env: dict[str, str]) -> dict[str, int]   # used by A6 and the UI
```
- Read component data from the PAM (use `fixtures/pam.aws.json` until B's scanner works).
- **Table:** `create_table(BillingMode="PAY_PER_REQUEST", KeySchema=..., AttributeDefinitions=<pk + every GSI key>, GlobalSecondaryIndexes=[{..., "Projection": {"ProjectionType": "ALL"}}])` → `get_waiter("table_exists")`.
- **Queues:** create `<name>-dlq` first, get its ARN, then the main queue with `Attributes={"VisibilityTimeout": "30", "RedrivePolicy": json.dumps({"deadLetterTargetArn": arn, "maxReceiveCount": "3"})}`.
- **Bucket:** `create_bucket(Bucket=name)` (us-east-1, no LocationConstraint).
- **Seed directly with boto3** (not through the app): fixed `random.Random(7)`, ISO timestamps, integer cents. Same item shape the app writes, so the app can read seeded orders.
- Idempotent: ignore "already exists" errors, so rerunning is safe. `lab.reset_moto()` gives a clean slate.

CLI: `boomerang aws provision [--seed 25]` (prints env), `boomerang aws test sample-app/aws` (provision → `run_pytest` → table of results).

**Done when:** `boomerang lab reset; boomerang aws test sample-app/aws` → 7/7, and `--seed 25` leaves 25 orders, 25 receipts, 3 queued messages.

---

## 7. A4: `sovereign_sdk.queue` (RabbitMQ) — 55 min

Interface (binding): [02 §5](../02-architecture.md). Implementation plan:

### 7.1 Structure
```python
# sovereign_sdk/queue.py
import json, threading, time, uuid, hashlib, os
import pika

class QueueDoesNotExist(Exception): ...
class UnsupportedFeature(Exception): ...

class Queue:
    def __init__(self, url: str | None = None):
        self._url = url or os.environ["AMQP_URL"]
        self._lock = threading.RLock()          # FastAPI runs sync routes in a thread pool
        self._conn = self._ch = None            # lazy (contract §4.3)
        self._inflight: dict[str, tuple[int, float, float]] = {}  # handle -> (delivery_tag, received_at, visibility_s)

def client() -> Queue                          # module-level singleton
```

### 7.2 Behavior

| Method | Implementation |
|---|---|
| `_channel()` | Under the lock: if no open channel, `BlockingConnection(URLParameters(url))` with `heartbeat=0` (avoids idle disconnects with BlockingConnection; acceptable for the demo, noted in docs); `channel.confirm_delivery()`. On `AMQPConnectionError` / `StreamLostError` / `ChannelClosed*`: drop and reconnect once. Note: reconnecting loses unacked messages' handles, and RabbitMQ requeues them. That's at-least-once, as in SQS. |
| `get_queue_url(QueueName)` | `queue_declare(name, passive=True)`; on `ChannelClosedByBroker` 404 → raise `QueueDoesNotExist`; return `{"QueueUrl": f"amqp:///{name}"}`. `QueueUrl` args are parsed by taking the part after the last `/`. |
| `send_message(QueueUrl, MessageBody, **kw)` | Reject `DelaySeconds`, `MessageGroupId` → `UnsupportedFeature`. `basic_publish(exchange="", routing_key=name, body=..., properties=BasicProperties(delivery_mode=2, message_id=uuid4().hex), mandatory=True)`. Return `{"MessageId": id, "MD5OfMessageBody": md5(body)}`. |
| `receive_message(QueueUrl, MaxNumberOfMessages=1, WaitTimeSeconds=0, VisibilityTimeout=None)` | `_expire()` first. Loop until deadline (`WaitTimeSeconds`): `basic_get(name, auto_ack=False)`; collect up to `MaxNumberOfMessages`; sleep 0.1 s between empty polls. Handle = `f"{id(conn)}:{delivery_tag}"`, stored in `_inflight` with the visibility (default 30 s). Return `{"Messages": [{"MessageId", "ReceiptHandle", "Body", "MD5OfBody"}]}`, or `{}` when empty (same as SQS). |
| `delete_message(QueueUrl, ReceiptHandle)` | Pop from `_inflight` → `basic_ack(delivery_tag)`. Unknown/expired handle → return `{}` and do nothing (the message was already requeued). |
| `_expire()` | For in-flight entries older than their visibility: `basic_nack(tag, requeue=True)` → redelivered; the quorum queue increments the delivery count, which drives the DLQ. |
| `declare(...)`, `queue_arguments(...)` | §4.4. Used by tests, by B's generator, and by A6. |
| `purge(name)` / `depth(name)` | Helpers for tests and the data mover (`queue_declare(passive=True).method.message_count`). |

**Honest limits (become 🔴/🟠 findings, listed in the docstring):** FIFO queues, delay queues, message attributes, `ChangeMessageVisibility`, and expiry only happens when the client is called again (or the connection closes).

### 7.3 Tests: `tests/sovereign_sdk/test_queue.py`
Fixture: `AMQP_URL=amqp://guest:guest@127.0.0.1:55672/` from `boomerang lab up --adapters`; unique queue name per test (`f"t-{uuid4().hex[:8]}"`); `pytest.skip` with a clear message if the broker isn't reachable.

| Test | Asserts |
|---|---|
| `test_send_receive_delete` | body round-trips; after delete, queue depth 0 |
| `test_empty_receive_is_fast` | `WaitTimeSeconds=0` → `{}` in < 0.5 s |
| `test_long_poll_waits` | `WaitTimeSeconds=1` on empty queue returns after ≈ 1 s |
| `test_redelivery_after_visibility_timeout` | receive with `VisibilityTimeout=1`, don't delete, sleep 1.2 s, receive → same `MessageId` |
| `test_dead_letter_after_max_receives` | `maxReceiveCount=3`: receive+expire 3×; next receive → empty; message is in `<name>-dlq`. **Use this test to settle the off-by-one** in `x-delivery-limit`. |
| `test_max_number_of_messages` | 5 sent, `MaxNumberOfMessages=3` → 3 |
| `test_thread_safety` | 4 threads × 25 sends → depth 100 |
| `test_unknown_queue` | `get_queue_url("nope")` → `QueueDoesNotExist` |
| `test_unsupported` | `DelaySeconds=5` → `UnsupportedFeature` |
| `test_lazy_connect` | `import sovereign_sdk.queue` with a bad `AMQP_URL` does not raise until first call |

**Done when:** all green against `test-rabbitmq`. By CP1 at least the first three.

---

## 8. A5: Verifier — 45 min

### 8.1 Signatures (agreed with C)
```python
def verify(leaseweb_dir: Path, attempt: int, emit) -> Verification
def run_pytest(app_dir: Path, tests_dir: Path, env: dict[str, str], platform: Platform,
               attempt: int, timeout_s: int = 180) -> TestReport      # also used for the AWS baseline and rollback
def reset_stack(leaseweb_dir: Path, emit) -> None    # down -v, then up --wait (clean data before A6)
def freeze(leaseweb_dir: Path, emit) -> None         # stop api + worker (write freeze before copying back)
def teardown(leaseweb_dir: Path) -> None             # down -v
```

### 8.2 Checks (each one is a `Check` with duration; emit an event per check)

| # | Check | How |
|---|---|---|
| 1 | `config` | `docker compose -f docker-compose.yml --env-file .env.example config -q`; `yaml.safe_load` every `*.yml`; `json.load` every `*.json`; `bash -n deploy.sh` if `shutil.which("bash")`, else "skipped (no bash)"; `cloud-init schema --config-file cloud-init.yaml` only if available, else "skipped" |
| 2 | `boot` | `docker compose -p bmr-verify up -d --build --wait --wait-timeout 180` (subprocess timeout 240 s). On failure: `docker compose ps` + last 40 log lines into `detail` |
| 3 | `schema` | psycopg → `DATABASE_URL` from `laptop.env` → every kv table exists in `information_schema.tables` |
| 4 | `tests` | `run_pytest(leaseweb_dir/"app", leaseweb_dir/"app"/"tests", laptop_env, "leaseweb", attempt)` |
| 5 | `health` | `GET http://127.0.0.1:8080/health`, retry 30 s → 200 and `{"status":"ok"}` |

### 8.3 `run_pytest` details
- Command: `[sys.executable, "-m", "pytest", "-q", str(tests_dir), "-p", "no:cacheprovider", "--junitxml", junit]`, `cwd=app_dir`.
- Env: start from `os.environ`; **remove** `AWS_*`, `DATABASE_URL`, `AMQP_URL`, `S3_*`, `ORDERS_TABLE`, `RECEIPTS_BUCKET`, `ORDER_EVENTS_QUEUE`; add `env`; `PYTHONPATH=app_dir`. Keep `PATH`/`SYSTEMROOT` (Windows needs them).
- Parse JUnit with `xml.etree.ElementTree`: `testcase` → `failure`/`error` children → `TestFailure` (traceback trimmed to 60 lines). No JUnit file → one `<session>` error with the stdout tail.

### 8.4 `laptop.env` fallback (if B doesn't write it)
Read `secrets.env` + `.env.example`; replace hosts: `postgres:5432` → `127.0.0.1:5432`, `rabbitmq:5672` → `127.0.0.1:5672`, `seaweedfs:8333` → `127.0.0.1:8333`.

### 8.5 Working before B6 exists
Create `fixtures/leaseweb-min/` by hand: a compose with `postgres`, `rabbitmq`, `seaweedfs` only (no api), a dummy `schema.sql`, `laptop.env`. Develop checks 1–3 against it; checks 4–5 when C's de-locked app and B's generator land (~3:00).

**Gotchas:** the first `--build` of `api` downloads pip packages (needs network once; afterwards cached). The tests create orders in the stack, which is why **A6 starts with `reset_stack()`**.

**Done when:** a run dir from C's pipeline goes config ✅ boot ✅ schema ✅ tests 7/7 ✅ health ✅, and a deliberately broken compose file gives a clear ❌ with logs.

---

## 9. A6: Data mover — 45 min

### 9.1 Signature
```python
def move(pam: AppModel, direction: Literal["aws->leaseweb", "leaseweb->aws"],
         aws_env: dict[str, str], lw_env: dict[str, str], emit) -> DataMove
```

### 9.2 Per component

| Kind | aws → leaseweb | leaseweb → aws | Count / checksum |
|---|---|---|---|
| kv_table | boto3 `scan` with `LastEvaluatedKey` pagination → `kv.resource().Table(name).put_item(Item=item)` | `kv.Table.scan()` → boto3 `Table.batch_writer()` `put_item` | count both sides; sha256 over sorted canonical lines (below) |
| object_store | `list_objects_v2` paginator → `get_object` → `put_object(Body, ContentType, Metadata)` via `sovereign_sdk.storage.s3_client()` | reverse | count; MD5 of each body, compared per key |
| queue | `receive_message(MaxNumberOfMessages=10, WaitTimeSeconds=1)` → `send_message` (adapter) → `delete_message` (SQS), until two empty polls | adapter receive → SQS send → adapter delete | number moved; depth after = 0 on source |

Canonical line per item: `json.dumps(normalize(item), sort_keys=True, separators=(",", ":"))`, where `normalize` turns `Decimal` into `int` if integral, else `float`; lists and dicts recursively. Sort lines by primary key and hash all of them.

**Notes for the "what got lost" report** (added to `DataCount.notes`): numbers normalized Decimal ↔ JSON; object ACLs/tags/versions not copied; message IDs and receive counts not preserved; messages in flight at cut-over are moved as new messages.

### 9.3 Sequence the pipeline should use (proposal to agree with C, refines [03 §3.12](../03-components.md))
1. **Forward:** `verify()` → `reset_stack()` (tests polluted the DB) → `move(aws->leaseweb)` → counts equal (25/25/3).
2. **Back:** create 1 probe order on Leaseweb (`POST http://127.0.0.1:8080/orders`) → `freeze()` (stop api + worker: write freeze, and the worker would otherwise consume messages mid-copy) → `move(leaseweb->aws)` **into the existing AWS resources** (a real rollback upserts into the old store: 25 + 1 = 26) → compare counts → **then** run the original tests on AWS (they add orders, so they go after the count).

**Done when:** forward and back both report matching counts and checksums, and the probe order exists on AWS afterwards.

---

## 10. A7: Golden run, backup video, submission — 45 min

1. `boomerang lab reset` → full `migrate` + `roundtrip` with `BOOMERANG_LLM_MODE=record`.
2. Copy that run to `runs/golden/<run_id>/` (check: **no `secrets.env`, no `laptop.env`** in it; add both to an explicit exclude list in the copy).
3. Record a ≤ 3-min screen capture of the UI following the run-of-show in [09](../09-demo-deploy-pitch.md). Upload, put the link in the README.
4. Submission checklist in [09 §6](../09-demo-deploy-pitch.md).

---

## 11. Files A owns

```
infra/lab.compose.yml
boomerang/lab.py  boomerang/provisioner_aws.py  boomerang/verifier.py  boomerang/datamover.py
sovereign_sdk/queue.py
tests/sovereign_sdk/test_queue.py  tests/test_verifier.py (junit parsing, env sanitizing)
sample-app/aws/**  sample-app/tests/**
fixtures/leaseweb-min/**
runs/golden/**
```
CLI commands A adds to `boomerang/cli.py` (in a small, separate block to avoid conflicts with C): `lab`, `aws provision`, `aws test`, `verify`, `data`.

## 12. Definition of done for role A
- [ ] ShopLite 7/7 on moto, seeded data on demand
- [ ] Queue adapter tests green (incl. visibility + DLQ), or the cut is documented as 🔴
- [ ] Generated Leaseweb stack: config → boot → schema → tests → health all ✅
- [ ] Data forward and back with matching counts/checksums; probe order comes back
- [ ] Golden run committed without secrets; video uploaded; submission sent
