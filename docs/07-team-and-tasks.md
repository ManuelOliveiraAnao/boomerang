# 07 · Team & tasks

Each task: **ID · owner · files owned · depends on · done when · estimate**. Copy them into GitHub Issues/Project at kickoff (one issue per ID).

## Roles

| Role | Mission | Skills that help |
|---|---|---|
| **A: Platform** | Make things run: lab, sample app, provisioning, tests | Docker, AWS/boto3, SQL |
| **B: Understand** | Make the engine understand apps: scan, score, plan, generate infra | Python, regex/AST, prompt writing, Terraform |
| **C: Transform (tech lead)** | Make the AI change code safely; own integration and the critical path | Python, LLM APIs, debugging |
| **D: Experience** | Make it visible and convincing: UI, demo, pitch | Streamlit/UI, storytelling |

---

## A: Platform

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| A1 | Lab compose + `lab` CLI | `infra/lab.compose.yml`, `boomerang/lab.py` | C1 (for CLI) | `boomerang lab up` starts moto, Postgres, MinIO, Redis; `status` shows healthy | 20m |
| A2 | ShopLite AWS flavor | `sample-app/aws/**` | – | runs against moto with dev bootstrap | 60m |
| A3 | ShopLite local flavor | `sample-app/local/**` | B2 (conventions) | runs against lab Postgres/MinIO/Redis | 40m |
| A4 | Shared tests | `sample-app/tests/**` | A2 | 7 tests, pass on AWS flavor | (in A2) |
| A5 | Provisioner | `boomerang/provisioner.py` | C1, B2 (`postgres_ddl`) | both platforms provision + teardown | 40m |
| A6 | Validator | `boomerang/validator.py`, `boomerang test` cmd | A5 | returns correct `TestReport` for pass and fail | 40m |
| A7 | Golden run + backup video + submission | `runs/golden/`, video | CP3 | committed; video uploaded | 45m |

Lab compose (`infra/lab.compose.yml`):
```yaml
name: boomerang-lab
services:
  moto:
    image: motoserver/moto:latest
    ports: ["4566:5000"]
  postgres:
    image: postgres:16-alpine
    environment: { POSTGRES_USER: boomerang, POSTGRES_PASSWORD: boomerang, POSTGRES_DB: boomerang }
    ports: ["5432:5432"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U boomerang"], interval: 2s, retries: 30 }
  minio:
    image: quay.io/minio/minio:latest        # Docker Hub minio/minio images were removed; fallback: cgr.dev/chainguard/minio
    command: server /data --console-address :9001
    environment: { MINIO_ROOT_USER: boomerang, MINIO_ROOT_PASSWORD: boomerang-secret }
    ports: ["9000:9000", "9001:9001"]
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
```
If port 5432 or 6379 is taken on your machine (a local Postgres/Redis), stop that service or change the host port in **your** `.env` only.

## B: Understand

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| B1 | Fixtures | `fixtures/*.json`, `fixtures/*.jsonl`, `fixtures/*.diff` | models (docs §3) | D and C can load them with the Pydantic models | 20m |
| B2 | Mappings & conventions | `boomerang/mappings.py` | C1 | `COUNTERPART`, `STRATEGY`, `CONVENTIONS`, `ENV_CONTRACT`, `postgres_ddl()` | 30m |
| B3 | Static hints | `boomerang/scanner/static.py` | – | finds every SDK call site in both flavors | 30m |
| B4 | AI scanner | `boomerang/scanner/ai.py`, `__init__.py`, `prompts/scan.md` | C2, B3 | `boomerang scan` matches fixtures for both flavors | 60m |
| B5 | Lock-in score | `boomerang/lockin.py` | – | aws ≈ 70, local ≈ 8 | 15m |
| B6 | Planner | `boomerang/planner.py`, `prompts/plan.md` | B2, B4, C2 | plan for both directions with sensible notes; flags SQS semantics | 40m |
| B7 | Generators | `boomerang/generators/**` | B2 | local compose passes `docker compose config`; schema applies; `main.tf` renders | 60m |

## C: Transform (tech lead)

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| C1 | Scaffold | `pyproject.toml`, `boomerang/__init__.py`, `models.py`, `config.py`, `events.py`, `cli.py`, empty modules for everyone | – | `pip install -e .` works, `boomerang --help` runs, pushed by 0:45 | 30m |
| C2 | LLM layer | `boomerang/llm.py` | C1 | see [03 §3.2](03-components.md) | 30m |
| C3 | Rewriter | `boomerang/rewriter.py`, `prompts/rewrite.md` | C2, B2 | 3 files rewritten, compile, diffs written | 60m |
| C4 | Pipeline `migrate()` | `boomerang/pipeline.py`, `report.py` | B4, B6, B7, A5, A6, C3 | aws→local run dir complete | 60m |
| C5 | Fixer + loop | `boomerang/fixer.py`, `prompts/fix.md` | C4 | a failing attempt gets fixed, tests untouched | 45m |
| C6 | `roundtrip()` | `boomerang/pipeline.py` | C4, C5 | round trip green in CLI | 30m |
| C7 | Commit demo LLM cache | `demo/llm-cache/` | CP3 | replay works with no network | 10m |

`pyproject.toml` dependencies (declare all at scaffold time so nobody edits it in parallel):
```
anthropic>=1,<2  pydantic>=2  typer  rich  python-dotenv  jinja2  pyyaml
boto3  psycopg[binary]>=3  redis>=5  fastapi  uvicorn  httpx  pytest  streamlit
```
`[project.scripts] boomerang = "boomerang.cli:app"`

## D: Experience

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| D1 | Streamlit skeleton + theme | `ui/app.py`, `.streamlit/config.toml` | – | sidebar + empty tabs, dark theme | 20m |
| D2 | Scan & Plan tabs | `ui/components/scan.py`, `plan.py` | B1 | renders fixtures; gauge + graph + step cards | 50m |
| D3 | Event feed | `ui/components/feed.py` | B1 | renders `events.sample.jsonl` | 20m |
| D4 | Migrate & Validate tabs | `ui/components/migrate.py`, `validate.py` | D3 | diffs + attempts render from a run dir | 50m |
| D5 | Round-trip tab | `ui/components/roundtrip.py` | C6 | 3-column view from `roundtrip.json` | 30m |
| D6 | Replay mode | `ui/components/replay.py` | D3 | recorded run plays back offline | 30m |
| D7 | Slides | `docs/pitch/` or Google Slides | – | 6 slides (see [09](09-demo-deploy-pitch.md)) | 40m |
| D8 | Demo script & rehearsal lead | `docs/09-...` updates | CP3 | 3 rehearsals done | 20m |

## Communication

- One shared channel. Post when you push something others depend on: "✅ B2 mappings on main".
- Blocked > 10 minutes → tell the lead. Don't sit on it.
- The lead calls each checkpoint and decides cuts. Disagreements: lead decides, move on.
