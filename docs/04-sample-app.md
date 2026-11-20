# 04 · Sample app: ShopLite (owner: A)

A deliberately small, deliberately locked-in order service. It is the "victim" of the demo, so it must be **small enough for the AI to rewrite reliably** and **realistic enough to be convincing**.

## 1. Behavior (identical in both flavors)

| Endpoint | Behavior |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `POST /orders` | Body `{"customer_id": str, "items": [{"sku": str, "qty": int, "price_cents": int}]}`. Validates `items` non-empty and `qty > 0` (422 otherwise). Creates `order_id` (uuid4 hex), `total_cents = Σ qty×price_cents`, `status = "pending"`, `created_at` (ISO string). Saves the order, uploads a JSON receipt to `receipts/<order_id>.json`, enqueues `{"order_id": ...}`. Returns 201 + the order. |
| `GET /orders/{order_id}` | The order, or 404 |
| `GET /customers/{customer_id}/orders` | List of that customer's orders (uses the index) |
| `GET /orders/{order_id}/receipt` | The receipt JSON from object storage, or 404 |

Worker (`app/worker.py`):
- `process_once(wait_seconds: int = 1) -> str | None`: receive one message; if there is one, set the order's `status = "processed"` and `processed_at`, delete the message, return the `order_id`; else return `None`.
- `main()`: loop `process_once(wait_seconds=10)` forever.

Money is **integer cents** everywhere (no floats, which avoids Decimal/float trouble between DynamoDB and Postgres).

## 2. Module layout (both flavors use the same files and function names)

```
sample-app/<flavor>/
  app/__init__.py
  app/main.py        # FastAPI routes only; calls db/storage/queue functions
  app/db.py          # put_order(order) / get_order(order_id) -> dict|None
                     # list_orders_by_customer(customer_id) -> list[dict]
                     # set_status(order_id, status, processed_at) -> None
  app/storage.py     # put_receipt(order_id, receipt: dict) -> str (key) / get_receipt(order_id) -> dict|None
  app/queue.py       # publish_order_created(order_id) -> None
                     # receive_one(wait_seconds) -> tuple[str, str] | None   (order_id, receipt_handle)
                     # ack(receipt_handle) -> None
  app/worker.py      # process_once(), main()
  .env.example
  requirements.txt
  infra/             # aws flavor: main.tf   | local flavor: schema.sql + docker-compose.yml
```
`main.py` and `worker.py` contain **no** cloud SDK calls. All cloud code sits in `db.py`, `storage.py`, `queue.py`. That's realistic (a thin data layer) and makes per-file rewrites safe.

## 3. AWS flavor (the original), `sample-app/aws/`

- `db.py`: `boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])`; `put_item`, `get_item`, `query(IndexName="customer_id-index", KeyConditionExpression=Key("customer_id").eq(...))`, `update_item(UpdateExpression="SET #s = :s, processed_at = :p", ExpressionAttributeNames={"#s": "status"}, ...)` (`status` is a reserved word in DynamoDB). Convert `Decimal` → `int` on read.
- `storage.py`: `boto3.client("s3")`; `put_object(Bucket=os.environ["RECEIPTS_BUCKET"], Key=f"receipts/{order_id}.json", Body=json.dumps(receipt), ContentType="application/json")`; `get_object` (catch `NoSuchKey` → None).
- `queue.py`: `boto3.client("sqs")`; `get_queue_url(QueueName=os.environ["ORDER_EVENTS_QUEUE"])` (cache it); `send_message`; `receive_message(MaxNumberOfMessages=1, WaitTimeSeconds=wait_seconds)`; `delete_message`.
- No `endpoint_url` in code: boto3 picks up `AWS_ENDPOINT_URL` from the env (that's how it talks to moto).
- `infra/main.tf`: bucket `receipts`, table `orders` (hash key `order_id` S, attribute `customer_id` S, GSI `customer_id-index` projection ALL, PAY_PER_REQUEST), queue `order-events`.
- `.env.example`: the aws column of the env contract ([02 §5](02-architecture.md)) with base names.

## 4. Local flavor (hand-written reference), `sample-app/local/`

Written by hand **following the conventions in [02 §4.1](02-architecture.md) exactly**. It serves three purposes:
1. **Unblocks the reverse direction** (local → AWS) from minute one, without waiting for the forward pipeline.
2. A **reference answer** for prompt tuning: compare the AI's output against it.
3. A **demo fallback** if the forward rewrite misbehaves.

- `db.py`: psycopg 3, table `orders(order_id TEXT PRIMARY KEY, customer_id TEXT, data JSONB NOT NULL)` + index on `customer_id`.
- `storage.py`: boto3 S3 client with `S3_*` env vars and path-style addressing (MinIO).
- `queue.py`: Redis list; `receive_one` uses `blpop` (returns the order_id and a dummy handle); `ack` is a no-op.
- `infra/schema.sql` (must equal `mappings.postgres_ddl()` output) and `infra/docker-compose.yml`.

## 5. Shared tests, `sample-app/tests/`

Black-box: they only use HTTP (FastAPI `TestClient`) and `app.worker.process_once`. **The same files run against every flavor and every migrated app.** The pipeline copies them into each run unchanged.

| Test | Asserts |
|---|---|
| `test_health` | 200, `status == "ok"` |
| `test_create_order` | 201; `total_cents` correct; `status == "pending"`; `order_id` present |
| `test_get_order` | created order round-trips with identical fields; unknown id → 404 |
| `test_validation` | empty items → 422; `qty: 0` → 422 |
| `test_receipt` | `GET /orders/{id}/receipt` returns the receipt with matching `order_id` and `total_cents` |
| `test_list_by_customer` | 2 orders for a unique customer id → list has exactly those 2 |
| `test_worker_processes_order` | create order → call `process_once` until it returns `None` (max 10 calls) → order `status == "processed"` |

Use a unique `customer_id` per test (`uuid4().hex`), so tests don't depend on each other or on clean state. Tests read nothing from env themselves.

## 6. How A tests the flavors before the engine exists

Until `provisioner.py` is ready, write a throwaway `sample-app/dev_bootstrap.py` that creates the bucket/table/queue in moto (and DB/bucket in local). Delete it once `boomerang test` works.

```powershell
boomerang lab up
boomerang test sample-app/aws --platform aws       # after provisioner + validator exist
boomerang test sample-app/local --platform local
```
**Done when:** 7/7 tests pass on both flavors.

## 7. Optional: a fault for the fix-loop demo

The fix loop must be visible in the demo. Usually the AI's first rewrite has a small bug on its own (e.g. a missing cast in `jsonb_set`, `Decimal` handling, or the `status` reserved word in the reverse direction). If the first attempt passes every time, record a run where it didn't, or make the source harder in a realistic way: e.g. `db.py` also stores `items` as a nested list and `list_orders_by_customer` sorts by `created_at`. **Never** inject a fake failure; judges respect a real fix.
