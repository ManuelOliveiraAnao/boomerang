# 00 · Start here

You have **6 hours**. Read this page (5 min), then the docs for your role (15 min max), then build.

## The one-paragraph version

Boomerang takes a small Python app that uses AWS (S3, DynamoDB, SQS), and an AI rewrites it to use MinIO, Postgres and Redis running locally, then proves it works by running the app's original tests. Then it does the reverse (local → AWS). The demo is the round trip, AWS → local → AWS, with green tests at each step. AWS is emulated locally with **moto**, so nobody needs an AWS account.

## Reading order by role

| Everyone (10 min) | Then, by role |
|---|---|
| This page | **A, Platform:** [04 Sample app](04-sample-app.md), [03 Components](03-components.md) §Provisioner, §Validator, [08 Setup](08-setup-and-workflow.md) §Lab |
| [02 Architecture](02-architecture.md) (the PAM contract is critical) | **B, Understand:** [03 Components](03-components.md) §Scanner, §Lock-in, §Mappings, §Planner, §Generators |
| [07 Team & tasks](07-team-and-tasks.md), your section | **C, Transform / lead:** all of [03 Components](03-components.md), [06 Roadmap](06-roadmap-6h.md) |
| [06 Roadmap](06-roadmap-6h.md), the checkpoints | **D, Experience:** [05 UI](05-ui.md), [09 Demo & pitch](09-demo-deploy-pitch.md), [01 Product](01-product-and-scope.md) |

## Golden rules for the 6 hours

1. **The contract is [02 Architecture](02-architecture.md) §3–§5.** If you need to change a model in `boomerang/models.py`, tell the tech lead first. Everyone codes against it.
2. **Own your files.** Each task in [07](07-team-and-tasks.md) lists the files it owns. Don't edit someone else's files without asking. This is how we avoid merge conflicts.
3. **Build against fixtures, not each other.** `fixtures/` holds example PAM, plan, events and test reports. The UI and later pipeline stages are built against them from minute one.
4. **Integrate early.** Checkpoints at **T+2:00** and **T+3:30** are mandatory (see [06](06-roadmap-6h.md)).
5. **Cut, don't slip.** If something is behind at a checkpoint, apply the cut line in [06](06-roadmap-6h.md). The demo must work at T+5:00.
6. **Push small and often** to `main` via short-lived branches (see [08](08-setup-and-workflow.md)).
7. **Never commit secrets.** The repo is public. `.env` is git-ignored; keep it that way.

## Before the clock starts (do this at home)

Everything here is setup, not code, so it doesn't eat build time.

- [ ] Install **Docker Desktop** and start it once (on Windows it needs WSL2, which can take 30+ minutes).
- [ ] Pull images: `docker pull postgres:16-alpine redis:7-alpine quay.io/minio/minio:latest motoserver/moto:latest`
- [ ] Install **Python 3.12** and **uv** (`pip install uv`).
- [ ] Install **git**, and an editor.
- [ ] Clone the repo and read your docs.
- [ ] Have a GitHub account and accept the repo invite / have push access.
- [ ] **One person:** create an Anthropic API key and set a spend limit in the Anthropic Console. Share it privately (not in the repo or a public chat).
- [ ] **Check the hackathon rules** on pre-written code. These docs are a plan and specs, which most events allow. If code must be written during the event, treat the code blocks here as specs to type in, not files to copy.

## Glossary

| Term | Meaning |
|---|---|
| **PAM** | Portable App Model: a platform-neutral JSON description of what the app needs (an object store, a key-value table, a queue) and where in the code it uses them |
| **Platform** | `aws` or `local` |
| **Provider** | A concrete service: `aws.s3`, `aws.dynamodb`, `aws.sqs`, `local.minio`, `local.postgres`, `local.redis` |
| **Component** | One infrastructure dependency of the app, e.g. the `orders` table |
| **Lab** | The Docker Compose stack every developer runs: moto (fake AWS), Postgres, MinIO, Redis |
| **Run** | One migration execution; its artifacts live in `runs/<run_id>/` |
| **Leg** | One direction of a round trip |
| **Flavor** | A version of the sample app: `aws` (original, locked in) or `local` (hand-written reference) |
