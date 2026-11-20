# 12 · Honest costs (owner: D)

Rule: **no number without a source.** The AI never produces prices. The cost engine is a pure function over three YAML files the user can see and edit.

## 1. `pricing/prices.yaml`: real published prices

Collected **before the event** from official price pages. Every line has a source URL and the date it was read.

```yaml
fx:
  usd_to_eur: 0.00          # fill in, with source + date
  source: ""
  retrieved: ""
prices:
  - id: lw.object_storage.gb_month
    provider: leaseweb
    item: Object Storage, on-demand, 0–50 TB
    unit: GB-month
    price: 0.01733
    currency: EUR
    source: https://www.leaseweb.com/object-storage
    retrieved: 2026-11-20   # re-check before the event
  - id: lw.instance.month          # the server that runs Postgres/RabbitMQ/app (pick a size, e.g. 4 vCPU / 8 GB)
    provider: leaseweb
    item: ""
    unit: month
    price: null
    currency: EUR
    source: https://www.leaseweb.com/en/products-services/cloud/public-cloud/pricing
    retrieved: ""
  - id: lw.egress.gb               # outgoing traffic beyond what's included
    provider: leaseweb
    unit: GB
    price: null
    currency: EUR
    source: https://www.leaseweb.com/en/products-services/cloud/public-cloud/pricing
    retrieved: ""
  # AWS (region eu-central-1 or eu-west-1; pick one and say which)
  - { id: aws.dynamodb.write_million, provider: aws, unit: million write request units, price: null, currency: USD, source: "https://aws.amazon.com/dynamodb/pricing/on-demand/", retrieved: "" }
  - { id: aws.dynamodb.read_million,  provider: aws, unit: million read request units,  price: null, currency: USD, source: "https://aws.amazon.com/dynamodb/pricing/on-demand/", retrieved: "" }
  - { id: aws.dynamodb.gb_month,      provider: aws, unit: GB-month, price: null, currency: USD, source: "https://aws.amazon.com/dynamodb/pricing/on-demand/", retrieved: "" }
  - { id: aws.sqs.million,            provider: aws, unit: million requests, price: null, currency: USD, source: "https://aws.amazon.com/sqs/pricing/", retrieved: "" }
  - { id: aws.s3.gb_month,            provider: aws, unit: GB-month, price: null, currency: USD, source: "https://aws.amazon.com/s3/pricing/", retrieved: "" }
  - { id: aws.s3.put_thousand,        provider: aws, unit: 1,000 PUT requests, price: null, currency: USD, source: "https://aws.amazon.com/s3/pricing/", retrieved: "" }
  - { id: aws.s3.get_thousand,        provider: aws, unit: 1,000 GET requests, price: null, currency: USD, source: "https://aws.amazon.com/s3/pricing/", retrieved: "" }
  - { id: aws.compute.month,          provider: aws, item: "compute for api + worker (e.g. Fargate or EC2 size)", unit: month, price: null, currency: USD, source: "", retrieved: "" }
  - { id: aws.egress.gb,              provider: aws, unit: GB (internet egress, first tier), price: null, currency: USD, source: "https://aws.amazon.com/ec2/pricing/on-demand/", retrieved: "" }
```
The engine refuses to show a total if any used price is `null` and lists which ones are missing. That is the honest failure mode.

## 2. `pricing/usage.default.yaml`: editable in the UI

```yaml
orders_per_month: 100000
reads_per_order: 3            # → DynamoDB reads, S3 GETs
writes_per_order: 2           # → DynamoDB writes
messages_per_order: 1         # → SQS: send + receive + delete = 3 requests per message
receipt_kb: 2
stored_gb_db: 5
stored_gb_objects: 20
egress_gb_month: 50
migration_data_gb: 25         # one-off move
```

## 3. `pricing/ops.yaml`: the cost of running it yourself

```yaml
hourly_rate_eur: 60           # editable; use your team's real rate
hours_per_month:
  patching_and_upgrades: 2
  backups_and_restore_tests: 1
  monitoring_and_on_call: 3
backup_storage_gb: 25         # priced with lw.object_storage.gb_month
monitoring_tool_month_eur: 0  # e.g. self-hosted; fill if you pay for one
```
AWS-side ops are not zero either; add an `aws_ops_hours` line (default 1) so the comparison is fair.

## 4. Formulas (`boomerang/cost.py`, pure + unit-tested)

```
AWS monthly = DynamoDB(reads, writes, storage) + SQS(3 × messages) + S3(storage, PUT, GET)
            + compute + egress + aws_ops_hours × rate
Leaseweb monthly = instance + object storage(objects + backups) + egress beyond included
                 + Σ ops hours × rate + monitoring tool
One-off migration egress (out of AWS) = migration_data_gb × aws.egress.gb   if date < 2027-01-12
                                      = €0, "EU Data Act: switching charges incl. egress banned" if date ≥ 2027-01-12
Break-even months = one-off costs / (AWS monthly − Leaseweb monthly)   (shown only if positive)
```
Show every line with its source link. Convert USD → EUR with the `fx` entry and show the rate used.

## 5. Egress: say it precisely

- **From 12 January 2027**, the EU Data Act bans switching charges, including data egress fees, for customers switching providers. Before that date, providers may charge only their direct switching costs.
- It covers **switching**, not normal day-to-day egress. Ongoing egress stays in the monthly line.
- Early-termination penalties in contracts are not covered by the ban.
- Lawyers flag that the EU "Digital Omnibus" proposals may affect parts of this regime: re-check before the demo.
- The UI line reads: *"Migration egress: €0 (EU Data Act switching rules, from 12 Jan 2027)"*, with the date logic above.

## 6. What we never say
"Leaving AWS is cheaper." We say: "Here is the comparison with published prices and your own usage; self-hosting adds these ops hours." If AWS comes out cheaper for the user's numbers, the UI shows that.
