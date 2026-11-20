# 08 · Setup & workflow (v2)

## 1. Prerequisites (install before the event)

| Tool | Check |
|---|---|
| Python 3.12 + uv | `python --version`, `uv --version` (`pip install uv`) |
| Docker Desktop (running; WSL2 on Windows) | `docker compose version` |
| Ollama | `ollama --version`; then `ollama pull qwen2.5-coder:7b` |
| git | `git --version` |

Pull images before the event:
```powershell
docker pull motoserver/moto:latest
docker pull postgres:16-alpine
docker pull rabbitmq:4-management-alpine
docker pull chrislusf/seaweedfs:latest
docker pull python:3.12-slim
```

## 2. First-time setup

```powershell
git clone https://github.com/ManuelOliveiraAnao/boomerang.git C:\dev\boomerang   # not inside OneDrive
cd C:\dev\boomerang
uv venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
uv pip install -e ".[dev]"             # once C1 is pushed
copy .env.example .env                 # macOS/Linux: cp
$env:OLLAMA_KEEP_ALIVE = "60m"         # set before starting Ollama
boomerang lab up
```

## 3. Daily commands

| Goal | Command |
|---|---|
| Fake AWS up/down | `boomerang lab up` / `down` |
| Scan with traffic lights | `boomerang scan sample-app/aws` |
| Full migration | `boomerang migrate sample-app/aws` (asks for approval; `--yes` to skip) |
| Re-verify a run | `boomerang verify <run_id>` |
| Round trip | `boomerang roundtrip <run_id>` |
| Costs | `boomerang cost` |
| UI | `streamlit run ui/app.py` |
| Unit tests (adapters, cost, rules, redaction) | `pytest tests` |
| RabbitMQ UI | http://127.0.0.1:15672 (credentials in the run's `secrets.env`) |

## 4. Git workflow
- `main` always runnable; branch per task `<id>-<slug>`, < 1 hour; commit `<ID>: <what>`.
- `git pull --rebase` before push; whoever pushed second resolves conflicts.
- `boomerang/models.py` changes need the lead's OK.

## 5. Conventions
Type hints on public functions; Pydantic models across module boundaries; every stage takes `emit`; `pathlib` + `encoding="utf-8"`; subprocess with list args, never `shell=True`; prompts in `prompts/*.md`; no `print()` in the engine.

## 6. Secrets & the public repo
`.env`, `secrets.env` and `runs/*` are git-ignored. `git diff --staged` before each commit. A leaked key gets revoked immediately.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| Model takes > 60 s per function | Close other GPU apps; confirm `ollama ps` shows the model on GPU; shorter prompt; fallback provider |
| 10–25 s freeze between AI steps | A second model got loaded; use one model; `OLLAMA_KEEP_ALIVE=60m` |
| `pull access denied for minio/minio` | We don't use MinIO anymore; SeaweedFS instead |
| Port 5432/5672/8333/8080 in use | Stop the local service or change the host port in `.env` |
| boto3 slow on Windows | `127.0.0.1`, not `localhost` |
| DynamoDB "reserved keyword" | `ExpressionAttributeNames` for `status` |
| `Decimal is not JSON serializable` | Convert `Decimal` → `int` on read |
| RabbitMQ `PRECONDITION_FAILED` on declare | Queue already exists with other arguments; `docker compose down -v` |
| Replay `CacheMiss` | Prompt or input changed; re-record |
| Repo in OneDrive is slow / locked files | Clone to `C:\dev\` |
