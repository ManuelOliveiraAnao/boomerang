# 00 · Start here (v2)

You have **6 hours**. Read this page (5 min), then your role's docs (15 min), then build.

## The one-paragraph version

Boomerang scans an AWS app (code **and** Terraform), labels every service and feature 🟢 config only / 🟠 adapter with listed changes / 🔴 needs a human, and, after a human approves, uses a **local AI model** to move the code onto small, pre-tested adapters. It generates **ready-to-use Leaseweb files**, boots them on the laptop, runs the app's **original** tests, moves the data with matching counts, then goes **back to AWS** (original code + data copied back) and shows **what got lost**. Costs come from a **real price table**, with self-hosting effort included.

**What changed from v1 and why:** [11 Decisions](11-decisions.md), last section.

## Reading order

| Everyone (10 min) | Then, by role |
|---|---|
| This page | **A, Platform & Data:** [04 Sample app](04-sample-app.md), [03](03-components.md) §3.8 §3.10 §3.11, [02 §5](02-architecture.md) (queue adapter), [14](14-leaseweb-target.md) §3 |
| [11 Decisions](11-decisions.md) | **B, Understand & Generate:** [02 §3–§5](02-architecture.md), [03](03-components.md) §3.3 §3.4 §3.7 §3.12, [14 Leaseweb files](14-leaseweb-target.md), [13](13-security.md) S4 |
| [02 Architecture](02-architecture.md) §1–§3 | **C, Lead & Transform:** all of [03](03-components.md), [13 Security](13-security.md), [06 Roadmap](06-roadmap-6h.md) |
| [07 Team & tasks](07-team-and-tasks.md), [06 Roadmap](06-roadmap-6h.md) | **D, Experience & Costs:** [05 UI](05-ui.md), [12 Costs](12-costs.md), [09 Demo & pitch](09-demo-deploy-pitch.md) |

## Golden rules
1. `boomerang/models.py` ([02 §3](02-architecture.md)) is the contract. Changes go through the lead.
2. Own your files ([07](07-team-and-tasks.md)). Build against `fixtures/`, not against each other.
3. Checkpoints at **2:00, 3:30, 4:30** are mandatory; apply the cut lines in [06](06-roadmap-6h.md).
4. **Don't fake AI work.** S3 → S3-compatible is a config change and is labeled that way.
5. **The AI never writes or edits tests**, never runs commands, never sees secrets.
6. **Never claim** zero downtime, full compatibility, "migrates any app", or "always cheaper".
7. The repo is public: never commit `.env`, `secrets.env` or keys.

## Before the clock starts
- [ ] Docker Desktop installed and running; images pulled ([08 §1](08-setup-and-workflow.md))
- [ ] Ollama installed; `qwen2.5-coder:7b` pulled; **Q1 benchmark run** on your laptop ([11](11-decisions.md))
- [ ] Python 3.12 + uv; repo cloned **outside OneDrive** (e.g. `C:\dev\boomerang`)
- [ ] D: `pricing/prices.yaml` filled from official pages, with URL + date per line ([12](12-costs.md))
- [ ] Someone asks the organizers about Leaseweb access (Q5)
- [ ] Optional: an Anthropic API key with a spend limit, only for the fallback (shared privately)
- [ ] Check the hackathon's rules on pre-written material (these docs are specs, not code)

## Glossary

| Term | Meaning |
|---|---|
| **PAM** | Portable App Model: what the app uses (tables, buckets, queues, triggers, roles) and where in code/infra |
| **Traffic light** | 🟢 config only · 🟠 moves via adapter, behavior differences listed · 🔴 not migrated, human needed |
| **sovereign_sdk** | Our small, tested adapters (`kv` → Postgres, `queue` → RabbitMQ, `storage` → S3-compatible) that mimic boto3 |
| **De-lock** | Rewriting functions that call AWS so they call `sovereign_sdk` instead |
| **Leaseweb files** | The generated, deployable setup in `runs/<id>/leaseweb/` |
| **Verify** | Config valid → boots → original tests pass → health OK |
| **Round trip** | AWS → Leaseweb → back to AWS (original code + data copied back) |
| **Lab** | Fake AWS (moto) on the laptop |
