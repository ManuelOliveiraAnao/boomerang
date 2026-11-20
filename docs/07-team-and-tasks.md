# 07 · Team & tasks (v2)

Copy each task into a GitHub issue at kickoff. Own only your files.

| Role | Mission |
|---|---|
| **A: Platform & Data** | ShopLite, fake AWS, the queue adapter, live verification, data moves |
| **B: Understand & Generate** | Scan, traffic lights, the kv adapter, Leaseweb files, "what got lost" |
| **C: Lead & Transform** | Scaffold, LLM layer, de-lock, fixer, pipeline, round trip, integration |
| **D: Experience & Costs** | UI, cost model, slides, demo |

## A: Platform & Data

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| A1 | Lab (moto) + `lab` CLI | `infra/lab.compose.yml`, `boomerang/lab.py` | C1 | moto up on `127.0.0.1:4566` | 15m |
| A2 | ShopLite AWS + tests + TF + Dockerfile | `sample-app/**` | – | 7/7 on moto | 60m |
| A3 | AWS provisioner + seed | `boomerang/provisioner_aws.py` | A2 | resources + 25 orders + 3 pending msgs | 20m |
| A4 | `sovereign_sdk.queue` (RabbitMQ) + tests | `sovereign_sdk/queue.py`, `tests/sovereign_sdk/test_queue.py` | – | send/receive/delete, redelivery after visibility timeout, DLQ after 3 receives | 55m |
| A5 | Verifier | `boomerang/verifier.py` | B6 | config/boot/schema/tests/health checks | 50m |
| A6 | Data mover (both directions) | `boomerang/datamover.py` | A3, B5 | counts + checksums match | 45m |
| A7 | Golden run, backup video, submission | `runs/golden/` | CP3 | done | 45m |

Lab compose (`infra/lab.compose.yml`), fake AWS only; the Leaseweb side is the generated compose:
```yaml
name: boomerang-lab
services:
  moto:
    image: motoserver/moto:latest
    ports: ["127.0.0.1:4566:5000"]
```

## B: Understand & Generate

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| B1 | Fixtures | `fixtures/**` | models | D/C load them | 20m |
| B2 | Static scan (py ast, tf, Dockerfile, env) | `boomerang/scanner/static.py` | – | all items of [04 §3](04-sample-app.md) found | 40m |
| B3 | AI scan + merge | `scanner/ai.py`, `prompts/scan.md` | C2 | matches fixture | 35m |
| B4 | Traffic lights + lock-in | `assess.py`, `lockin.py`, `prompts/assess.md` | B3 | 1 🟢, 🟠 for kv/queue features, 🔴 for lambda/iam/alarm | 35m |
| B5 | `sovereign_sdk.kv` (Postgres) + `ddl()` + tests | `sovereign_sdk/kv.py`, `tests/sovereign_sdk/test_kv.py` | – | put/get/update/delete/query-on-GSI/scan/conditions pass vs Postgres | 50m |
| B6 | Leaseweb generator | `generators/**` | B5 | all files of [14](14-leaseweb-target.md); `compose config` ok | 50m |
| B7 | What got lost | `boomerang/lost.py` | B4, A6 | preserved/changed/lost list | 20m |

## C: Lead & Transform

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| C1 | Scaffold + models | `pyproject.toml`, `boomerang/{models,config,events,cli}.py`, empty modules | – | pushed by 0:45 | 30m |
| C2 | LLM layer + redaction | `llm.py`, `redact.py` | C1 | [03 §3.2](03-components.md) | 35m |
| C3 | Slicer + de-lock + rules + `storage.py` | `slicer.py`, `delock.py`, `rules.py`, `sovereign_sdk/storage.py`, `prompts/delock.md` | C2 | ShopLite functions rewritten, rules pass | 60m |
| C4 | Pipeline migrate + approval gate + report | `pipeline.py`, `report.py` | B6, A5, C3 | CP2 command green | 50m |
| C5 | Fixer | `fixer.py`, `prompts/fix.md` | C4 | fixes a real failing function | 25m |
| C6 | Round trip | `pipeline.py` | A6, B7 | AWS→LW→AWS green + lost report | 30m |
| C7 | Demo LLM cache | `demo/llm-cache/` | CP3 | offline replay works | 10m |

`pyproject.toml` dependencies (all declared at scaffold time):
```
ollama  anthropic>=1,<2  pydantic>=2  typer  rich  python-dotenv  jinja2  pyyaml
boto3  psycopg[binary]>=3  pika  fastapi  uvicorn  httpx  pytest  streamlit
```

## D: Experience & Costs

| ID | Task | Files | Depends | Done when | Est |
|---|---|---|---|---|---|
| D1 | UI skeleton + theme | `ui/app.py`, `.streamlit/config.toml` | – | 5 tabs, telemetry off | 20m |
| D2 | Price table + default usage (data prepared before the event) | `pricing/*.yaml` | – | every price has URL + date | 15m |
| D3 | "What breaks" tab + approve button | `ui/components/breaks.py` | B1 | lights table from fixture | 40m |
| D4 | Cost engine + Costs tab | `boomerang/cost.py`, `ui/components/costs.py`, `tests/test_cost.py` | D2 | editable usage, sourced lines | 50m |
| D5 | Code + Leaseweb setup tabs | `ui/components/code.py`, `setup.py` | B1 | diffs + checklist | 40m |
| D6 | Data & round trip tab | `ui/components/roundtrip.py` | B1 | counts + lost list | 30m |
| D7 | Replay | `ui/components/replay.py` | D3 | offline replay | 25m |
| D8 | Slides + script | – | – | [09](09-demo-deploy-pitch.md) | 30m |
