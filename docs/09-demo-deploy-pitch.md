# 09 · Demo, deploy & pitch

## 1. "Deploy" for this project

Boomerang is a developer tool, and it needs Docker for validation, so the demo runs on **one presenter laptop**:

1. Presenter laptop = the machine with the most RAM, Docker running, lab up, `.env` set.
2. `BOOMERANG_LLM_MODE=record` with the cache already warm from the golden run (re-runs are instant and identical).
3. `runs/golden/` + `demo/llm-cache/` committed, so **replay works with no network**.
4. Backup video (≤ 3 min) uploaded before T+5:30.

What Boomerang **outputs** is deployable:
- Local target: `cd runs/<id>/infra && docker compose up -d` runs the migrated app on Postgres/MinIO/Redis.
- AWS target: `runs/<id>/infra/main.tf` is standard Terraform (`terraform init && terraform apply` with real credentials; not done in the demo).

Optional, only if everything else is done: deploy the UI in replay-only mode to Streamlit Community Cloud so judges can click around. Not required.

## 2. Demo run-of-show (3 minutes)

| Time | Screen | Say |
|---|---|---|
| 0:00 | Title slide | "Moving off a hyperscaler today takes months of manual rewrites, and the AI tools that exist only move you *in*. Boomerang moves you out **and** back, and proves it works." |
| 0:20 | UI, Scan tab | "Here's ShopLite, an order service built on DynamoDB, SQS and S3. Boomerang's AI read it: 3 cloud components, 12 call sites, **lock-in 70/100**." Point at the graph. |
| 0:45 | Plan tab | "For each service it picks an open-source counterpart, and it warns us where behavior changes: SQS is at-least-once, a Redis list isn't." |
| 1:05 | Click **Round trip**; Migrate tab | "Now it rewrites the code, file by file, with function signatures kept identical, and generates the Docker Compose and SQL schema." Show the DynamoDB → Postgres diff. |
| 1:35 | Validate tab | "The app's **own tests** run on real Postgres, MinIO and Redis. The first attempt failed one test. The AI read the error and fixed its own code. **7/7 green.**" (If live is slow: switch to the recorded run, say so honestly.) |
| 2:10 | Round-trip tab | "Then the other direction: back to AWS, re-scanned from the generated code. Green again. Lock-in 70 → 8 → 70: you decide, with numbers." |
| 2:35 | Closing slide | "No one-way doors. Boomerang makes cloud moves reversible and verified. Next: more services, more clouds, and an ops agent that runs what you moved." |

Rules: one person drives, one person talks. Don't type during the demo; everything is one click. If anything hangs for > 10 s, switch to replay without apologizing at length.

## 3. Slides (6)

1. **Title:** Boomerang 🪃 · "Leave AWS. Come back. Nothing breaks."
2. **Problem:** lock-in in code + no exit tooling + fear of one-way doors. One stat (e.g. 37signals' reported savings after leaving AWS, with source).
3. **How it works:** the PAM diagram (scan → model → generate either direction) + "AI for code, templates for infra, tests as the spec".
4. **Live demo** (just a title; switch to the UI).
5. **Why it's different:** the comparison table from [01 §3](01-product-and-scope.md).
6. **What's next:** more services (Lambda, IAM), more targets (EU sovereign clouds, GPU clouds), the ops agent; team.

## 4. Judge Q&A, prepared answers

| Question | Answer |
|---|---|
| Why not just use AWS Transform? | It only moves workloads into AWS. We move out, back and between, and verify with your own tests. |
| Why not Claude Code / a coding agent? | A coding agent edits files. Boomerang maps services, generates matching infrastructure, provisions it, and runs the tests in a loop. It's a pipeline with guarantees, not a chat. |
| How do you know the AI didn't break things? | The original tests are the spec and the AI can't edit them. We show pass/fail for every attempt. |
| Does it work on real AWS? | The AWS side uses standard boto3 and Terraform; in the lab we use moto, an AWS emulator, to avoid cost. Pointing it at real AWS is a credentials change. |
| What about data? | Code and infra today; data copy is designed (stretch S1 / roadmap). |
| Does it scale to big apps? | Per-file rewriting, the PAM and the fix loop scale with file count; bigger apps need more service mappings and better static analysis. That's the roadmap. |
| What if semantics differ? | The planner flags each difference (e.g. delivery guarantees) before anything is changed. |
| Cost to run? | A sample-app migration is a handful of model calls; the cache makes re-runs free. |

## 5. Submission checklist (A, by T+5:45)

- [ ] Repo public, README updated with what actually works (no claims beyond the demo)
- [ ] `runs/golden/` and `demo/llm-cache/` committed; `.env` NOT committed
- [ ] Backup video link in README
- [ ] Team names filled in README
- [ ] Hackathon submission form: title, tagline, description, repo, video, tech used
- [ ] Slides exported to PDF
