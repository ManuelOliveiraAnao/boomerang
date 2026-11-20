# 13 · Security

Boomerang reads source code, generates credentials and starts containers. These rules are part of the product and part of the pitch.

## 1. Rules and where they're implemented

| # | Risk | Rule | Where | Owner |
|---|---|---|---|---|
| S1 | Secrets leak into LLM prompts | **Redact before every LLM call**: AWS key patterns (`AKIA[0-9A-Z]{16}`, 40-char secrets after `aws_secret_access_key`), `password=`/`secret=`/`token=` values, URLs with `user:pass@`, every value from `.env*` and `secrets.env`. Replace with `<REDACTED:name>`. Unit-tested. | `boomerang/redact.py`, called inside `llm.structured()` | C |
| S2 | AI runs unsafe commands | The LLM **never** produces commands. Its outputs are code for one function or text. All actions (compose up, data copy, deploy) are fixed Python functions with fixed arguments. No `shell=True` anywhere. | `rules.py`, code review | C |
| S3 | Valid JSON, wrong content | **Rules check** after every LLM output: parses with `ast`; same function name and parameters; no new imports; forbidden names (`os.system`, `subprocess`, `eval`, `exec`, `open`, `socket`, `requests`, `urllib`, `__import__`); path must be inside the app and not under `tests/` or `sovereign_sdk/`. Fail → reject + event. | `boomerang/rules.py` | C |
| S4 | Generated keys grant too much | **Least privilege**: Postgres user owns only the `shoplite` database (no superuser); RabbitMQ user limited to vhost `shoplite`; SeaweedFS S3 identity limited to the `receipts` bucket; random 24+ char passwords via `secrets.token_urlsafe`. The UI shows each credential's scope. | `generators/leaseweb.py` | B |
| S5 | "Runs locally" isn't the whole story | Ollama bound to `127.0.0.1`; Streamlit telemetry off (`gatherUsageStats = false`); no other outbound calls from the engine except Docker image pulls (done before the event). When the Claude fallback is used, the UI says so. With the Leaseweb server (docs/17): Ollama stays on the server's `127.0.0.1`, reached only through an SSH tunnel (key login, `ufw` allows SSH only), redaction still runs on the laptop, and the badge says `Leaseweb (EU)` (`BOOMERANG_LLM_LOCATION=leaseweb`). | config, docs/17 | C, D |
| S6 | Automated changes make things worse | **Human approval** before any files are written (plan gate) and before any deploy. AWS is read-only during migration; original code kept untouched for rollback. | pipeline + UI | C, D |
| S7 | Secrets committed to the public repo | `.env`, `secrets.env`, `runs/*` git-ignored; `git diff --staged` before commit; revoke on leak. | repo | All |
| S8 | Prompt injection through scanned code (comments like "ignore previous instructions") | Code is passed as data inside clear delimiters; the system prompt says file content is data; outputs are constrained by schema **and** by S3 rules, so injected instructions can't trigger actions. | prompts + rules | C |

## 2. What we say to judges
- "The AI can't run anything. It proposes code for one function; plain code checks it and a human approves the plan."
- "Secrets never reach the model, and the model runs on this laptop." (Only if the Q1 decision kept Ollama on the laptop.)
- With the Leaseweb server (docs/17): "Secrets are removed on this laptop before anything leaves it, and the model runs on Leaseweb in the EU: no AWS, no US AI API."
- "Every generated credential is scoped to one database, one vhost, one bucket."
- "If something fails, AWS was never touched."

## 3. Not covered (be honest)
Supply-chain scanning of images, network policies between containers, TLS on the Leaseweb server (DEPLOY.md tells the user to put Caddy/Traefik with Let's Encrypt in front), and secret storage beyond a file with `chmod 600`.
