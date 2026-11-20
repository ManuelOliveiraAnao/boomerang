# 08 · Setup & workflow

## 1. Prerequisites (install before the event)

| Tool | Version | Check |
|---|---|---|
| Python | 3.12 | `python --version` |
| uv | latest | `uv --version` (`pip install uv`) |
| Docker Desktop | latest, running | `docker compose version` |
| git | any recent | `git --version` |
| Terraform (optional, stretch S3) | 1.x | `terraform version` |

## 2. First-time setup

```powershell
git clone https://github.com/<owner>/boomerang.git
cd boomerang
uv venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
uv pip install -e ".[dev]"             # works once C1 (scaffold) is pushed
copy .env.example .env                 # macOS/Linux: cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=...
boomerang lab up
boomerang lab status
```

## 3. Daily commands

| Goal | Command |
|---|---|
| Start/stop lab | `boomerang lab up` / `boomerang lab down` |
| Lab without the CLI | `docker compose -f infra/lab.compose.yml up -d` |
| Test a sample flavor | `boomerang test sample-app/aws --platform aws` |
| Scan | `boomerang scan sample-app/aws` |
| Migrate | `boomerang migrate sample-app/aws --to local` |
| Round trip | `boomerang roundtrip sample-app/aws` |
| UI | `streamlit run ui/app.py` |
| Engine unit tests | `pytest tests` |
| MinIO console | http://127.0.0.1:9001 (boomerang / boomerang-secret) |

## 4. Git workflow (built for speed)

- `main` is always runnable. No long-lived branches.
- Branch per task: `a2-aws-flavor`, `c3-rewriter`. Keep branches < 1 hour.
- Merge your own PR once it runs; a review is optional, except for `models.py` changes (lead approves).
- Or, for tiny changes in files you own: `git pull --rebase` then push to `main` directly.
- Commit messages: `<task-id>: <what>`, e.g. `B4: AI scanner returns PAM`.
- Pull often: `git pull --rebase` before every push.
- Conflicts: whoever pushed second resolves. If it's in someone else's file, ask them.

## 5. Coding conventions

- Python 3.12, type hints on public functions, Pydantic models from `boomerang/models.py` across module boundaries (no loose dicts).
- Every stage function takes `emit` and reports progress. No `print()` in the engine.
- Paths: `pathlib.Path`; always `encoding="utf-8"` on read/write (Windows defaults differ).
- Subprocesses: list args, never `shell=True`.
- Prompts live in `prompts/*.md`, not inline strings.
- Keep functions small; no premature abstraction. It's a hackathon.
- Format with `ruff format` if you have it; don't fight about style.

## 6. Secrets & the public repo

- `.env` is git-ignored. Never commit keys; never paste them in issues or PRs.
- `git diff --staged` before every commit (5 seconds).
- If a key leaks: revoke it in the Anthropic Console immediately, create a new one, tell the team.
- The LLM cache in `demo/llm-cache/` contains prompts and outputs about the sample app only. Fine to commit.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` | Install/start Docker Desktop; on Windows, enable WSL2 |
| Port 5432/6379 in use | Stop the local Postgres/Redis service, or remap the port and update `.env` |
| `pull access denied for minio/minio` | Use `quay.io/minio/minio` (already in the compose); fallback `cgr.dev/chainguard/minio` |
| boto3 hangs or slow on Windows | Use `127.0.0.1` instead of `localhost` everywhere |
| S3 `InvalidBucketName` / DNS errors | Ensure path-style addressing and `127.0.0.1` endpoint |
| DynamoDB `ValidationException ... reserved keyword` | Use `ExpressionAttributeNames` for `status` |
| `Decimal is not JSON serializable` | Convert `Decimal` → `int` in `db.py` on read |
| `anthropic.AuthenticationError` | `ANTHROPIC_API_KEY` missing in `.env` or the shell |
| Replay mode `CacheMiss` | Prompt or input changed since recording; re-record in `record` mode |
| Repo inside OneDrive/Dropbox is slow or files lock | Clone outside synced folders (e.g. `C:\dev\boomerang`) |
