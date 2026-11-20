# 01 · Product & scope (v2)

## 1. Problem

Companies want to move workloads off AWS to European providers like Leaseweb (cost, data sovereignty, concentration risk), but:

1. **Code lock-in:** the app calls proprietary APIs (DynamoDB, SQS). Leaving looks like a rewrite.
2. **Unknown blast radius:** nobody can say up front what breaks and what gets lost.
3. **No trustworthy tooling:** hyperscaler AI migration agents only move workloads **in**. Generic coding agents rewrite files but don't generate the target setup, move the data, or prove anything.
4. **Fear of one-way doors:** if it goes wrong, can we go back?
5. **Fuzzy economics:** "self-hosting is cheaper" is often claimed and rarely calculated honestly.

## 2. Solution: Boomerang

An AI-assisted migration tool from AWS to **self-hosted infrastructure** (a company's own servers, or a provider such as Leaseweb) and back, that **shows what breaks, does the move, proves it, and prices it honestly**.

| Step | What the user sees |
|---|---|
| **Scan** | Every place the code and infrastructure depend on AWS, file and line |
| **Traffic lights** | Each service and feature labeled 🟢 config only · 🟠 adapter, behavior differences listed · 🔴 not migrated, needs a human |
| **De-lock** | AI maps the app's AWS calls onto small, pre-tested adapters (`sovereign_sdk`); S3 is a config change only. Human approves the plan first. |
| **Leaseweb files** | Ready-to-use `docker-compose.yml`, `.env` template, `cloud-init.yaml`, `deploy.sh`, backup job, and a deploy README |
| **Verify live** | Files are valid → the new setup boots on the laptop → the app's **original** tests pass → health check → ✅ |
| **Move data** | Items, objects and pending messages copied; counts and checksums compared |
| **Round trip** | Back to AWS: original code + data copied back (including data written on Leaseweb), tests again, **report of what got lost** |
| **Costs** | Real published prices, editable usage, self-hosting ops cost included, egress €0 for switching from 12 Jan 2027 (EU Data Act) |

> **Where can it migrate to? Anywhere you can run Docker.** The output is a standard self-hosted stack (Docker Compose with Postgres, RabbitMQ and S3-compatible storage) plus a deploy script that works with any server reachable over SSH: a company's own servers, an on-premises data center, or a rented server. **Leaseweb is our example target** (European provider, used for the deploy guide and the price table). In code, the self-hosted platform is still called `leaseweb` (`Platform = "leaseweb"`, `runs/<id>/leaseweb/`); it's an internal name, not a restriction.

**Principles**
- AI only where it reads unseen code or explains; everything else is plain, deterministic code.
- The app's existing tests are the spec. The AI never writes or edits tests.
- AWS stays untouched until all checks pass.
- We flag what we don't support. We never fake it.

## 3. Positioning

| | Hyperscaler agents | Terraform / Kubernetes | Generic coding agents | **Boomerang** |
|---|---|---|---|---|
| Direction | Into their cloud | n/a | Any | **AWS → Leaseweb → back** |
| Removes AWS from the code | ✗ | ✗ (infra only) | Manual | **✓** |
| Says what breaks, per service | Partial | ✗ | ✗ | **✓ traffic lights** |
| Generates target setup | Their own | You write it | Manual | **✓ Leaseweb files** |
| Moves data + checks counts | Their tools | ✗ | ✗ | **✓** |
| Proves it works | Partial | ✗ | Manual | **✓ boot + original tests** |
| Honest cost comparison | Their pricing | ✗ | ✗ | **✓ real prices + ops cost** |

## 4. Scope

### In scope (must work at T+4:30)
- Python apps (FastAPI + boto3), one sample app: **ShopLite**.
- S3 → S3-compatible (SeaweedFS locally; Leaseweb Object Storage in production), **🟢 config only**.
- DynamoDB → Postgres via `sovereign_sdk.kv`, **🟠**.
- SQS → RabbitMQ via `sovereign_sdk.queue` (ack, retries, visibility timeout, DLQ), **🟠**.
- Detected but not migrated: Lambda triggers, IAM roles, CloudWatch alarms, **🔴 manual review**.
- Leaseweb output files; live verification on the laptop.
- Data migration both ways with counts/checksums.
- Round trip with "what got lost" report.
- Cost model with a sourced price table.
- Streamlit UI + CLI; local LLM via Ollama (Claude fallback).

### Stretch
| ID | Stretch |
|---|---|
| S1 | Terraform for Leaseweb Public Cloud (official provider) to create the server |
| S2 | `deploy.sh` run against a real Leaseweb server (if organizers give access), recorded before the demo |
| S3 | Scoped S3 credentials in SeaweedFS (per-bucket identity) shown in the UI |
| S4 | Migrate any local path, not only the sample app |

### Out of scope (say "roadmap")
AI SRE / self-healing ops agent, ScyllaDB Alternator path, Lambda/IAM migration, other languages, zero-downtime cut-over, Kubernetes.

## 5. What we never claim
- Zero downtime. Full compatibility. "Migrates any app."
- That passing tests prove everything: they prove what the tests cover.
- That leaving AWS is automatically cheaper: the cost view includes backups, monitoring and staff time.

## 6. Success criteria for the demo
1. Scan shows ShopLite's AWS dependencies with **2 🟠, 1 🟢, at least 1 🔴**.
2. Generated Leaseweb setup **boots on the laptop**, original tests **7/7**, health ✅.
3. Data moved with **matching counts and checksums**.
4. Round trip back to AWS: tests green, counts match (including an order created on Leaseweb), **"what got lost" list shown**.
5. Cost view with sources, editable usage, ops cost and egress line.
6. Backup video by T+5:30.
