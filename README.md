# 🪃 Boomerang

**Leave AWS for your own servers. Know what breaks. Come back if you want.**

Boomerang scans an application built on AWS (code and Terraform), shows what will break, moves it to a ready-to-run self-hosted setup (your own servers, or a provider such as Leaseweb) with help from a **local AI model**, proves it works with the app's own tests, moves the data, takes it all back to AWS if needed, and prices both sides honestly.

> **Where can it migrate to? Anywhere you can run Docker.** The output is a standard self-hosted stack (Docker Compose with Postgres, RabbitMQ and S3-compatible storage) plus a deploy script that works with any server reachable over SSH: a company's own servers, an on-premises data center, or a rented server. **Leaseweb is our example target** (European provider, used for the deploy guide and the price table). In code, the self-hosted platform is still called `leaseweb` (`Platform = "leaseweb"`, `runs/<id>/leaseweb/`); it's an internal name, not a restriction.

| Step | What happens | AI? |
|---|---|---|
| **Scan** | Every AWS dependency in code, Terraform, Dockerfile and env, with file and line | AI + static analysis |
| **Traffic lights** | Each service/feature: 🟢 config only · 🟠 moves via adapter, changes listed · 🔴 not migrated, needs a human | Rules decide, AI explains |
| **Approve** | A human approves the plan before anything is written | – |
| **De-lock** | Each function that calls AWS is rewritten onto small, pre-tested adapters (`sovereign_sdk`); S3 is a config change | AI per function |
| **Self-hosted files** (Leaseweb as example) | `docker-compose.yml`, `cloud-init.yaml`, `deploy.sh`, backups, scoped credentials, `DEPLOY.md` | Templates |
| **Verify live** | Files valid → stack boots on the laptop → original tests pass → health check ✅ | – |
| **Move data** | Items, objects, pending messages; counts and checksums compared | – |
| **Round trip** | Back to AWS: original code + data copied back, tests again, **what got lost** | – |
| **Costs** | Published prices with sources, editable usage, self-hosting ops time, migration egress €0 from 12 Jan 2027 (EU Data Act) | – |

AWS is emulated locally with **moto**, so no AWS account is needed. The AI never writes tests, never runs commands and never sees secrets.

## Documentation

Start with **[docs/00-start-here.md](docs/00-start-here.md)**.

| Doc | Contents |
|---|---|
| [00 Start here](docs/00-start-here.md) | Onboarding, reading order, pre-event checklist |
| [01 Product & scope](docs/01-product-and-scope.md) | Problem, solution, positioning, scope, what we never claim |
| [02 Architecture](docs/02-architecture.md) | Design, models (the contract), traffic-light rules, adapters, env, artifacts |
| [03 Components](docs/03-components.md) | Spec for every module |
| [04 Sample app](docs/04-sample-app.md) | ShopLite, the AWS-locked demo app |
| [05 UI](docs/05-ui.md) | Streamlit dashboard |
| [06 Roadmap (6 hours)](docs/06-roadmap-6h.md) | Timeline, checkpoints, cut lines |
| [07 Team & tasks](docs/07-team-and-tasks.md) | Roles and task backlog |
| [08 Setup & workflow](docs/08-setup-and-workflow.md) | Install, commands, git, troubleshooting |
| [09 Demo, deploy & pitch](docs/09-demo-deploy-pitch.md) | Run-of-show, judge Q&A, submission |
| [10 Risks & future](docs/10-risks-and-future.md) | Risk register, AI SRE design, roadmap |
| [11 Decisions](docs/11-decisions.md) | Answers to the open questions; v1 → v2 changes |
| [12 Costs](docs/12-costs.md) | Price table, usage, ops costs, egress rules |
| [13 Security](docs/13-security.md) | Redaction, allowlists, scoped keys, approvals |
| [14 Self-hosted target (Leaseweb example)](docs/14-leaseweb-target.md) | Generated files and how they're verified |

## Quickstart (once the code exists)

```powershell
uv venv; .venv\Scripts\activate
uv pip install -e ".[dev]"
copy .env.example .env
ollama pull qwen2.5-coder:7b
boomerang lab up
boomerang migrate sample-app/aws
streamlit run ui/app.py
```

## Team

| Role | Person | Owns |
|---|---|---|
| A: Platform & Data | _name_ | ShopLite, fake AWS, queue adapter, verifier, data mover |
| B: Understand & Generate | _name_ | Scanner, traffic lights, kv adapter, Leaseweb files, lost report |
| C: Lead & Transform | _name_ | Scaffold, LLM layer, de-lock, fixer, pipeline, round trip |
| D: Experience & Costs | _name_ | UI, cost model, slides, demo |

## Tech stack
Python 3.12 · Ollama (`qwen2.5-coder:7b`, Claude as fallback) · Pydantic v2 · Typer · Streamlit · boto3 · psycopg 3 · pika · Jinja2 · pytest · Docker Compose · moto · Postgres · RabbitMQ · SeaweedFS / Leaseweb Object Storage
