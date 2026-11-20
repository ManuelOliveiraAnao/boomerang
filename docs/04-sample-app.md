# 04 · Sample app: ShopLite (owner: A) (v2)

A small, realistic, AWS-locked order service. Small enough for a 7B model working function by function; realistic enough to produce all three traffic lights.

## 1. Behavior

| Endpoint | Behavior |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `POST /orders` | Body `{"customer_id": str, "items": [{"sku": str, "qty": int, "price_cents": int}]}`. 422 if items empty or `qty <= 0`. Creates `order_id` (uuid4 hex), `total_cents`, `status="pending"`, `created_at` (ISO). Saves with `ConditionExpression="attribute_not_exists(order_id)"`, uploads receipt JSON to `receipts/<order_id>.json`, sends `{"order_id"}` to the queue. 201 + order. |
| `GET /orders/{order_id}` | Order or 404 |
| `GET /customers/{customer_id}/orders` | Orders via GSI `customer_id-index` |
| `GET /orders/{order_id}/receipt` | Receipt JSON from S3 or 404 |

Worker `app/worker.py`: `process_once(wait_seconds=1) -> str | None` receives one message (`VisibilityTimeout=30`), sets `status="processed"` + `processed_at`, deletes the message, returns the id; `None` if empty. `main()` loops.

Money is **integer cents** everywhere.

## 2. Layout

```
sample-app/aws/
  app/main.py       # routes only, no SDK calls
  app/db.py         # put_order, get_order, list_orders_by_customer, set_status   (DynamoDB)
  app/storage.py    # put_receipt, get_receipt                                    (S3)
  app/queue.py      # publish_order_created, receive_one, ack                     (SQS)
  app/worker.py
  infra/main.tf     # see §3
  Dockerfile        # python:3.12-slim, no AWS keys → relies on an IAM role (🔴 finding)
  .env.example  requirements.txt
sample-app/tests/   # shared, black-box (see §4)
```
Code style: module-level clients (`dynamodb = boto3.resource("dynamodb")`, `s3 = boto3.client("s3")`, `sqs = boto3.client("sqs")`), each SDK call inside a small function. No `endpoint_url` in code (moto is reached via `AWS_ENDPOINT_URL`). `status` is a DynamoDB reserved word → `ExpressionAttributeNames`. Convert `Decimal` → `int` on read.

## 3. `infra/main.tf` (gives the scanner real infrastructure to read)

| Resource | Purpose | Expected light |
|---|---|---|
| `aws_s3_bucket.receipts` | receipts | 🟢 |
| `aws_dynamodb_table.orders` (hash `order_id`, GSI `customer_id-index`, PAY_PER_REQUEST) | orders | 🟠 |
| `aws_sqs_queue.order_events` (`visibility_timeout_seconds = 30`, `redrive_policy` → `order_events_dlq`, `maxReceiveCount = 3`) + `aws_sqs_queue.order_events_dlq` | events | 🟠 |
| `aws_lambda_function.receipt_thumbnailer` + `aws_s3_bucket_notification.receipts` (ObjectCreated → Lambda) | side effect | 🔴 |
| `aws_iam_role.shoplite_task` + policy | credentials model | 🔴 |
| `aws_cloudwatch_metric_alarm.dlq_not_empty` | monitoring | 🔴 |

The Lambda's code is not needed (a `filename = "thumbnailer.zip"` reference is enough); it never runs in the demo. It exists so the scanner finds a **real** red item.

## 4. Shared tests (`sample-app/tests/`)

Black-box: HTTP via FastAPI `TestClient` + `app.worker.process_once`. Copied unchanged into every migrated app. The AI never sees them as editable.

| Test | Asserts |
|---|---|
| `test_health` | 200, `status == "ok"` |
| `test_create_order` | 201, `total_cents`, `status == "pending"` |
| `test_get_order` | round-trip fields; unknown → 404 |
| `test_validation` | empty items → 422; qty 0 → 422 |
| `test_receipt` | receipt matches `order_id`, `total_cents` |
| `test_list_by_customer` | exactly the 2 orders of a unique customer |
| `test_worker_processes_order` | `process_once` until `None` (max 10) → `processed` |

Unique `customer_id` per test (uuid4). Tests don't read env vars.

**Known gap (say it in the pitch):** these tests don't cover retries/DLQ behavior, so passing them doesn't prove queue semantics. The adapter's own unit tests (`tests/sovereign_sdk/test_queue.py`) cover redelivery after visibility timeout and dead-lettering after 3 receives.

## 5. Demo data

`provisioner_aws --seed 25` creates 25 orders (some processed, some pending) with receipts, and leaves 3 messages in the queue, so the data move has something real to count.

**Done when:** `boomerang lab up` + provision + `pytest` → 7/7 against moto; `boomerang scan` finds every item in §3.
