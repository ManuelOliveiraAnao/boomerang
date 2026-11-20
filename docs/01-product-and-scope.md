# 01 · Product & scope

## 1. Problem

Companies run on AWS, Azure and GCP, and many want to move some workloads off them because of cost, data sovereignty, or concentration risk. Moving is hard because:

1. **Code lock-in:** application code calls proprietary APIs (DynamoDB, SQS). Leaving looks like a rewrite.
2. **No trustworthy tooling:** hyperscalers' AI migration agents (AWS Transform, Azure Copilot Migration Agent) only move workloads **into** their cloud. Generic coding agents rewrite files but don't map services, generate the target infrastructure, or prove the result works.
3. **Fear of one-way doors:** a migration feels irreversible, so teams don't start.

Evidence: 37signals left AWS by hand and reported saving millions per year. The repatriation trend is growing, but the work is still manual.

## 2. Solution

**Boomerang** is a two-way migration engine built on one idea: describe the app in a **platform-neutral model (PAM)**, then generate any target from it.

```
AWS app ──scan──►               ──generate──► local (Docker Compose)
                  Portable App
local app ─scan──►    Model      ──generate──► AWS (Terraform)
```

- **AI** does what needs understanding: reading code, rewriting API calls, fixing failures.
- **Deterministic code** does what must be exactly right: infrastructure templates, provisioning, running tests.
- **Tests are the spec.** The app's original test suite must pass on the new platform. The AI may never edit tests.

## 3. Positioning (for judges)

| | Hyperscaler agents | Code-upgrade tools | Generic coding agents | **Boomerang** |
|---|---|---|---|---|
| Direction | Into their cloud | Within one stack | Any | **Out, in, and round trip** |
| Proprietary service → open-source equivalent | ✗ | Partial | Manual | **✓ core** |
| Generates target infrastructure | Their own | ✗ | Manual | **✓** |
| Proves the result works | Partial | Tests only | Manual | **✓ provision + test + auto-fix** |
| Lock-in score | ✗ | ✗ | ✗ | **✓** |

One-liner: **"No one-way doors. Boomerang makes cloud moves reversible and verified."**

## 4. Scope

### In scope (MVP, must work at T+5:00)

- Python apps only (FastAPI + boto3 style).
- Three service pairs, both directions:
  - S3 ⇄ MinIO (config-level change)
  - DynamoDB ⇄ Postgres (real rewrite: data model + queries)
  - SQS ⇄ Redis (real rewrite + semantic difference flagged)
- One sample app, **ShopLite**, in two flavors (AWS and local).
- Pipeline: scan → plan → rewrite → generate → validate → fix loop → report.
- Round trip AWS → local → AWS.
- Lock-in score before/after.
- Streamlit UI, plus a CLI.
- LLM record/replay cache, so the demo is fast and repeatable.

### Stretch (only if a checkpoint is green early)

| ID | Stretch | Value |
|---|---|---|
| S1 | Data migration: copy DynamoDB items → Postgres rows, S3 objects → MinIO | "It moved my data too" |
| S2 | Boot the generated `docker-compose.yml` itself during validation (offset ports) | Stronger proof |
| S3 | `terraform validate` on generated Terraform | Proof for the AWS side |
| S4 | Cost comparison panel with stated assumptions | Business story |
| S5 | Start a migration from any local path, not only the sample app | Generality |

### Out of scope (say "roadmap" if asked)

Real AWS accounts, Lambda/IAM/VPC migration, languages other than Python, Kubernetes targets, multi-user auth, the "AI ops agent" that runs the infrastructure after migration (see [10](10-risks-and-future.md)).

## 5. Success criteria for the demo

1. `boomerang roundtrip sample-app/aws` completes with **all tests green on all three stages** (original, local, back on AWS).
2. Lock-in score visibly drops (≈70 → ≈8) and returns.
3. At least one **real** fix-loop iteration is shown (the AI fixes its own failing test), from a live run or a recorded one.
4. The UI tells the story in under 3 minutes.
5. A backup video exists by T+5:30.
