# 11 · Decisions (v2)

The team raised these questions before building. Each has a **recommended answer** (already reflected in all other docs), how to confirm it, and a deadline. If the team decides differently, update this page first, then the affected docs.

| # | Question | Decision (recommended) | Confirm by | Deadline |
|---|---|---|---|---|
| Q1 | Can our laptops run a 7B coding model fast enough? | **Yes, if we send functions, not files.** Default `qwen2.5-coder:7b` via Ollama, one model for every AI task. Target: < 60 s per function on the presenter laptop. If it fails, try `:14b` only if the laptop has the VRAM; otherwise switch `BOOMERANG_LLM_PROVIDER=anthropic` (Claude) and **drop the "runs fully locally" claim** in the pitch. Option: run Ollama on a Leaseweb server over an SSH tunnel (`qwen2.5-coder:14b`, ~26 s per function), see docs/17. | Run the benchmark in §Q1 on every laptop | Before the event |
| Q2 | LocalStack or Moto for fake AWS? | **Moto** (`motoserver/moto`). Free, no account or token, covers S3, DynamoDB and SQS, which is all we use. | `boomerang lab up` + sample app tests pass | T+1:00 |
| Q3 | What replaces S3: MinIO, SeaweedFS or Garage? | **SeaweedFS** on the laptop and in the self-hosted compose; **Leaseweb Object Storage** (S3-compatible, managed) as the production option on Leaseweb. Not MinIO: the community edition is in maintenance mode and its Docker Hub images were removed. Garage is fine too but needs more setup (layout + key commands). Either way, S3 → S3-compatible is **a config change, not a rewrite**. | `aws s3`-style smoke test against SeaweedFS | Before the event (pull image) |
| Q4 | DynamoDB → ScyllaDB Alternator (no code change) or Postgres (rewrite)? | **Postgres, through our `sovereign_sdk.kv` adapter.** Postgres is what a Leaseweb customer already knows, runs, and backs up; it's cheap and light on a laptop. Alternator is a valid "green path" for heavy DynamoDB users: roadmap, not demo. | Adapter unit tests pass | T+2:00 |
| Q5 | Can we get a Leaseweb server or API access from the organizers? | **Ask now.** The plan works without it: the generated Leaseweb files are verified by booting them on a laptop. With access, we also run `deploy.sh` against a real Leaseweb server **before** the demo (never live). | Organizer answer | Before the event |
| Q6 | Keep the AI SRE, or cut it? | **Cut it.** Six hours is not enough for the migration additions plus a safe self-healing agent. Its design and security notes move to [10](10-risks-and-future.md) as the roadmap. Mention it in one sentence in the pitch. | – | Decided |
| Q7 | (new) SQS → Redis or RabbitMQ? | **RabbitMQ**, through our `sovereign_sdk.queue` adapter that handles acknowledgements, retries, visibility timeout and a dead-letter queue. Redis lists can't honestly provide those semantics. | Adapter unit tests pass | T+2:00 |
| Q8 | (new) How does "back to AWS" work? | **Rollback, not a second rewrite.** The original AWS code is never modified (AWS stays untouched until checks pass), so going back = redeploy the original code + **copy the data back**, including data written on Leaseweb. Then the same tests run and the data counts are compared. | Round trip green | T+4:30 |

## Q1 benchmark (each laptop, ~10 minutes)

```powershell
ollama pull qwen2.5-coder:7b
$env:OLLAMA_KEEP_ALIVE = "60m"
ollama run qwen2.5-coder:7b "Rewrite this Python function to use a class with the same methods as boto3 DynamoDB Table, named kv.Table. Return only code.`n<paste get_order() from the sample app>"
```
Record: time to first token, total time, and whether the output is correct. Repeat 3×. Note RAM/VRAM. Pick the presenter laptop from the results.

## What changed vs. v1 of the plan

| v1 | v2 |
|---|---|
| Target "local" (generic Docker) | Target **Leaseweb**: real deployable files, verified by booting them on a laptop |
| Claude API | **Ollama local model** by default; Claude as a documented fallback |
| AI writes raw SQL/Redis calls | AI maps call sites onto **pre-tested `sovereign_sdk` adapters**; S3 is config only |
| MinIO, Redis | **SeaweedFS** (or Leaseweb Object Storage), **RabbitMQ** |
| Round trip = rewrite code back | Round trip = **rollback with data copied back**, plus a "what got lost" report |
| Lock-in score | Kept, plus **green / amber / red** label per service and feature |
| Code only | **Data migration** with counts and checksums, both directions |
| Optional cost panel | **Honest cost model**: real price table, editable usage, self-hosting ops cost, egress €0 from 12 Jan 2027 |
| – | **Security** rules: secret redaction, allowlisted actions, human approval ([13](13-security.md)) |
