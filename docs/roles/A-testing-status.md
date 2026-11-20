# Role A · Testing status

> **Update (after merging C's scaffold, `role-c`):** A's modules now follow C's contract (docs/15 L4/L5/L8/L13/L15/L17):
> `lab.up/down/status() -> bool/reset()`, `provisioner_aws.provision(pam, emit)` / `seed(pam, count, emit)`,
> `verifier.verify / run_tests / aws_env / leaseweb_env / reset_data / stop_service / start_service`,
> `datamover.move(pam, direction, src_env, dst_env, emit)`. Commands now come from C's CLI (`boomerang lab …`, `verify <run_id>`, `data <run_id>`).
>
> **How to run tests now** (C's convention: a bare `pytest` never needs Docker or a model):
> - `pytest` → unit tests only (~35 s, no Docker).
> - `boomerang lab up` + `python -c "from boomerang import lab; lab.up(adapters=True)"`, then `pytest -m integration` → fake AWS, real RabbitMQ, real Postgres, real stack boot.
>
> **Docker is installed now.** Section 2 below was written before that; N1–N4 and N7 have since been run: real RabbitMQ found the `nack` vs `reject` dead-letter bug (fixed), and the minimal stack boots.

Last full run before Docker: **102 passed, 20 skipped, 0 failed** (`pytest -q`, about 4½ minutes; `pytest -q -m "not slow"` skips the 1,000-object copy test and takes about 2 minutes).
Machine: Windows 11, Python 3.12, no Docker at the time. Fake AWS ran as a local moto process.

## 1. What is tested

| Component | Tests | What they prove |
|---|---|---|
| **A1 Lab** (`boomerang/lab.py`) | `tests/test_lab.py` (11) | moto starts/stops/restarts as a local process; reset wipes state; `up` is idempotent; clear errors when Docker is requested but missing; compose file is valid and has the expected services/ports/profiles |
| **A2 ShopLite** (`sample-app/aws`) | 7 spec tests (`sample-app/tests`) + 14 white-box (`tests/fixtures/shoplite_whitebox`) via `tests/test_shoplite.py` (5) | all endpoints; validation (8 bad inputs → 422); receipt 404; duplicate order → **409** (not 500); the worker drops events for unknown orders **without creating ghost orders**; numbers come back as `int`, not `Decimal`; receipts stored as JSON with content type; an unacked message is redelivered after its visibility timeout; the harness itself fails when resources are missing; Terraform declares every red item; `main.py`/`worker.py` contain no AWS SDK calls |
| **A3 Provisioner** (`boomerang/provisioner_aws.py`) | `tests/test_provisioner_aws.py` (10) | bucket, table + GSI, queue + DLQ + visibility timeout created from the PAM; idempotent; run tags isolate names; seed is deterministic (same IDs on re-run, different with another seed); pending orders match queued messages; other table shapes (sort key, two GSIs, numeric keys); FIFO queues + FIFO DLQ; bad FIFO names rejected; Lambda/IAM never created |
| **A4 Queue adapter** (`sovereign_sdk/queue.py`) | `tests/sovereign_sdk/test_queue.py`: 24 against an in-memory broker model | send/receive/delete; empty receive shape (`{}`); long polling (waits, returns early, picks up a message whose visibility expired); redelivery after visibility timeout; stale-handle delete is harmless; DLQ after exactly `maxReceiveCount` receives, with the original body; `MaxNumberOfMessages`; 4 parallel senders; 4 parallel consumers each message exactly once; FIFO order for one consumer; 200 KB unicode bodies; missing queue on get/send/receive → `QueueDoesNotExist`; mismatched declare arguments rejected; reconnect after a dropped connection (in-flight message redelivered, old handle harmless); lazy connect; `queue_arguments()` contract |
| **A5 Verifier** (`boomerang/verifier.py`) | `tests/test_verifier.py` (28) | pytest runner: pass/fail/error/skip counts, collection errors, no-tests = not OK, timeout, trimmed tracebacks, wrong-platform env vars stripped, app tests isolated from Boomerang's pytest config; env files (quotes, comments, `laptop.env` preferred, container hosts rewritten to 127.0.0.1); config check (bad YAML, bad JSON, missing compose, compose errors, `deploy.sh` syntax via bash, cloud-init header); schema parsing + unreachable Postgres reported cleanly; full `verify()` flow with a simulated Docker (all green; stops at a failed boot with logs; failing app tests reported; one event per check); health check pass/fail; freeze/reset issue the right compose commands |
| **A6 Data mover** (`boomerang/datamover.py`) | `tests/test_datamover.py` (15) | forward copy 25/25/3 with checksums; round trip brings back an order created after migration (26/26); extra target rows detected; one failing component reported without stopping the others; content type + user metadata copied; empty source; moving twice is idempotent; floats → `Decimal` for DynamoDB; pagination for tables and objects (fakes) **and** a real 1,030-object copy past S3's 1,000-key page (`slow`); content corruption detected for objects and table rows; clear error when B's kv adapter is missing |
| **CLI** (`boomerang/cli_platform.py`) | `tests/test_cli_platform.py` (9) | `lab up/status/reset`, `aws provision --seed`, `aws test` (pass → exit 0, broken app → exit 1), `verify` without Docker stops at boot, `data` rejects bad directions and explains the missing kv adapter |

## 2. What is NOT tested yet, and what we need

| # | Not tested | Why not | What we need | How to run it |
|---|---|---|---|---|
| N1 | **Queue adapter against real RabbitMQ** (19 tests skipped). Most important: that RabbitMQ dead-letters after exactly `maxReceiveCount` receives with `x-delivery-limit = maxReceiveCount − 1`, as the fake broker assumes | No Docker on this laptop | Docker Desktop + WSL2 (`wsl --install`, reboot, install Docker Desktop); image `rabbitmq:4-management-alpine` | `boomerang lab up --adapters` then `pytest tests/sovereign_sdk` |
| N2 | Real boot of a compose stack (`test_real_boot_of_minimal_stack`, skipped); real `docker compose config` | No Docker | Docker; images `postgres:16-alpine`, `rabbitmq:4-management-alpine`, `chrislusf/seaweedfs` | `pytest tests/test_verifier.py` |
| N3 | Schema check against real Postgres | No Postgres | Docker (comes with N2) | covered by `verify` on a real stack |
| N4 | Lab with the **Docker backend** (moto container) | No Docker | Docker; image `motoserver/moto` | `boomerang lab up --backend docker`, then `pytest tests/test_lab.py` |
| N5 | **Full `verify()` on a real generated Leaseweb setup** (boot → schema → tests → health of the `api` container) | B's generator (B6) and C's de-locked app (C3) don't exist yet | B6 + C3 output in `runs/<id>/leaseweb/`, Docker, network once for the first `api` image build | `boomerang verify runs/<id>/leaseweb` |
| N6 | **Data mover against the real Leaseweb side** (Postgres + RabbitMQ + SeaweedFS) | Needs B's `sovereign_sdk.kv` (B5) and C's `sovereign_sdk.storage`; Docker | B5 + C's storage adapter + a booted stack | `boomerang data runs/<id>/leaseweb` both directions |
| N7 | ShopLite's own Docker image (`sample-app/aws/Dockerfile`) builds and serves `/health` | No Docker | Docker | `docker build -t shoplite sample-app/aws && docker run -p 8080:8080 shoplite` |
| N8 | Terraform validity of `sample-app/aws/infra/main.tf` (`terraform validate`) | Terraform not installed; the file is only read by the scanner, never applied | Terraform CLI (optional) | `cd sample-app/aws/infra && terraform init -backend=false && terraform validate` |
| N9 | Behavior on **real AWS** (not moto) | Out of scope: no AWS account, by design | an AWS account (not planned) | — |
| N10 | macOS/Linux | Only Windows available here | a teammate's machine | `pytest -q` |
| N11 | A7 (golden run, video) | Needs the whole pipeline (C4/C6) | everyone's parts merged | see `docs/roles/A-platform-and-data.md` §10 |

## 3. Known limits that tests can't remove
- The 7 ShopLite spec tests don't cover retries or the dead-letter queue. Passing them proves only what they cover; the queue adapter's own tests cover the queue semantics separately.
- The fake broker models RabbitMQ the way the docs describe it. N1 is what confirms the model; until then, treat the DLQ off-by-one as **unverified**.
- moto is an emulator. The app uses only basic S3/DynamoDB/SQS operations, which moto supports well, but it isn't real AWS (N9).

## 4. Bugs the tests found and fixed
1. The Windows console couldn't print ✓/✗, so the CLI crashed → plain `OK`/`FAIL`.
2. App tests inherited Boomerang's own pytest config → `run_pytest` now uses an empty config.
3. Sending to a queue that doesn't exist surfaced a raw pika error → now `QueueDoesNotExist`.
4. `delete_message` crashed after a dropped connection → now harmless (the message is redelivered).
5. ShopLite: a duplicate order ID returned 500 → now 409.
6. ShopLite: processing an event for an unknown order silently **created a ghost order** (DynamoDB `update_item` upserts) → now `attribute_exists` guard; the event is dropped.
7. Provisioner didn't support FIFO queues → added (with FIFO DLQ naming).
8. `data` command crashed with a stack trace when B's adapter is missing → clear message.
9. The seed-determinism test could never fail (compared a list with itself) → rewritten.

## 5. First thing to do once Docker is installed
```powershell
wsl --install            # then reboot, then install Docker Desktop and start it
docker pull motoserver/moto:latest
docker pull postgres:16-alpine
docker pull rabbitmq:4-management-alpine
docker pull chrislusf/seaweedfs:latest
boomerang lab up --adapters
python -m pytest -q
```
Expected: the 20 skipped tests run. If `test_dead_letter_after_max_receives[rabbitmq]` fails, change the `x-delivery-limit` formula in `sovereign_sdk/queue.py` (`queue_arguments`) and tell B, because B's `definitions.json` must use the same arguments.
