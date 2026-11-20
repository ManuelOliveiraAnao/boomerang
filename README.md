# 🪃 Boomerang

**AI-powered two-way cloud migration. Leave AWS. Come back. Nothing breaks.**

Boomerang reads an application that is locked into AWS (S3, DynamoDB, SQS), understands what it needs, rewrites it to run on self-hosted open-source infrastructure (MinIO, Postgres, Redis), and **proves it still works** by running the app's own tests against the new stack. It also goes the other way, from self-hosted to AWS, on the same engine.

The headline demo is a **round trip**: AWS → local → AWS, with the test suite passing at every stop.

---

## Why

- Hyperscalers build AI migration tools that move workloads **in** (AWS Transform, Azure Copilot Migration Agent). Nobody has an incentive to build the tool that moves you **out** or lets you go both ways.
- Companies that leave (e.g. 37signals) do it by hand, over months.
- Lock-in comes from proprietary APIs (DynamoDB, SQS) baked into code. Rewriting that code reliably needs AI. Proving the rewrite is correct needs tests.

## What it does (hackathon scope)

| Step | What happens | AI or deterministic |
|---|---|---|
| **Scan** | Reads code + Terraform, finds every cloud dependency, builds a **Portable App Model (PAM)** and a lock-in score | AI + static analysis |
| **Plan** | Maps each component to its counterpart on the target and flags behavior changes (e.g. SQS at-least-once → Redis at-most-once) | Deterministic + AI notes |
| **Rewrite** | Rewrites each affected file, keeping function signatures identical | AI |
| **Generate** | Writes target infrastructure: `docker-compose.yml` + `schema.sql` (local) or `main.tf` (AWS) | Deterministic templates |
| **Validate** | Provisions resources, runs the app's original tests on the new stack | Deterministic |
| **Fix loop** | On failure, feeds the test errors back to the AI to patch and re-run (max 3 attempts) | AI |
| **Report** | Diffs, test results, lock-in before → after | Deterministic |

## Documentation

Start with **[docs/00-start-here.md](docs/00-start-here.md)**. It tells each team member what to read, in what order.

| Doc | Contents |
|---|---|
| [00 Start here](docs/00-start-here.md) | Onboarding, reading order per role, pre-hackathon checklist |
| [01 Product & scope](docs/01-product-and-scope.md) | Problem, solution, competition, what's in and out of scope |
| [02 Architecture](docs/02-architecture.md) | System design, the PAM contract, mappings, run artifacts, repo layout |
| [03 Components](docs/03-components.md) | Spec for every module, including the LLM layer and prompts |
| [04 Sample app](docs/04-sample-app.md) | The locked-in demo app ("ShopLite"), both flavors, the shared tests |
| [05 UI](docs/05-ui.md) | Streamlit dashboard spec |
| [06 Roadmap (6 hours)](docs/06-roadmap-6h.md) | Hour-by-hour plan, checkpoints, cut lines |
| [07 Team & tasks](docs/07-team-and-tasks.md) | Roles, task backlog with IDs, owners, definitions of done |
| [08 Setup & workflow](docs/08-setup-and-workflow.md) | Install, run, git workflow, conventions |
| [09 Demo, deploy & pitch](docs/09-demo-deploy-pitch.md) | Demo run-of-show, backup plan, slides, judge Q&A, submission |
| [10 Risks & future](docs/10-risks-and-future.md) | Risk register, fallback plans, post-hackathon roadmap |

## Quickstart (once the code exists)

```powershell
# 1. Python env (uv recommended)
uv venv; .venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Secrets
copy .env.example .env                  # then put your ANTHROPIC_API_KEY in .env

# 3. Lab infrastructure (Docker Desktop must be running)
boomerang lab up

# 4. Run things
boomerang scan sample-app/aws
boomerang migrate sample-app/aws --to local
boomerang roundtrip sample-app/aws
streamlit run ui/app.py
```

## Team

| Role | Person | Owns |
|---|---|---|
| A: Platform | _name_ | Lab infra, sample app, provisioner, test runner |
| B: Understand | _name_ | Scanner, lock-in score, mappings, planner, generators |
| C: Transform (tech lead) | _name_ | Scaffold, LLM layer, rewriter, fixer, pipeline, integration |
| D: Experience | _name_ | Streamlit UI, replay mode, pitch deck, demo script |

## Tech stack

Python 3.12 · Claude API (`anthropic` SDK, structured outputs) · Pydantic v2 · Typer · Streamlit · boto3 · psycopg 3 · redis-py · Jinja2 · pytest · Docker Compose · moto (AWS emulator) · Postgres · MinIO · Redis
