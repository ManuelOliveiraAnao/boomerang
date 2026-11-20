# 09 · Demo, deploy & pitch (v2)

## 1. Demo setup
- Presenter laptop chosen by the Q1 benchmark. Docker running, `boomerang lab up`, Ollama warm (`OLLAMA_KEEP_ALIVE=60m`, one call made before going on stage).
- `BOOMERANG_LLM_MODE=record` with the cache warm from the golden run → identical, fast re-runs. `runs/golden/` + `demo/llm-cache/` committed → **replay works offline**.
- Leaseweb stack from the golden run already booted (saves ~30 s on stage).
- Backup video (≤ 3 min) uploaded by T+5:30.
- If organizers gave Leaseweb access: screenshot/URL of the real deployment, made before the demo.

## 2. Run-of-show (3 minutes)

| Time | Screen | Say |
|---|---|---|
| 0:00 | Title | "Leaving AWS means rewriting code, guessing what breaks, and hoping you can go back. Boomerang shows what breaks, moves you to your own servers (here: a Leaseweb-style setup), proves it, and brings you back." |
| 0:20 | What breaks | "ShopLite uses DynamoDB, SQS and S3. The scan found every AWS dependency in code **and** Terraform. S3 is green: config only. DynamoDB and SQS are amber: they move through tested adapters, and here's exactly what changes. Three reds: a Lambda trigger, an IAM role and an alarm. We don't fake those; a human handles them." → **Approve** |
| 0:55 | Code | "A local model on this laptop rewrote each function onto our adapter. Same names, same return values. S3 wasn't touched by AI: that's a config change." |
| 1:20 | Leaseweb setup | "These are real Leaseweb files: compose, cloud-init, deploy script, backups. Watch: valid → booted → the app's **original** tests, 7 of 7 → health." ✅ |
| 1:50 | Data & round trip | "25 orders, 25 receipts, 3 pending messages moved; counts and checksums match. Now we go back: one new order created on Leaseweb, data copied back to AWS, the original code passes its tests, 26 orders. And here's what got lost: the Lambda trigger, the IAM role, and visibility timeouts are now emulated." |
| 2:30 | Costs | "Published prices, your usage, **plus** the hours to run it yourself. Moving the data out costs €0 from 12 January under the EU Data Act." |
| 2:45 | Close | "No faked AI, no hidden losses, no one-way doors." |

Rules: one driver, one speaker; no typing; anything stuck > 10 s → replay, no long apology.

## 3. Slides (6)
1. Title + tagline. 2. Problem (lock-in, unknown losses, fear, fuzzy costs). 3. How it works (scan → lights → approve → de-lock → Leaseweb files → verify → data → round trip). 4. Live demo. 5. Why trust it (tests are the spec, AI can't run commands, secrets never reach the model, AWS untouched, reds flagged). 6. Next (AI SRE, more services, Alternator path, more targets) + team.

## 4. Judge Q&A

**Say it clearly:** the target is *any* server that runs Docker; Leaseweb is our example. In the demo the self-hosted stack runs locally in Docker, the same files `deploy.sh` would ship to a real server.


| Question | Answer |
|---|---|
| Does it only work with Leaseweb? | No. The output is standard Docker Compose + open-source services, and `deploy.sh` works with any SSH server: your own servers, on-premises, or any provider. Leaseweb is our example and the price table we filled in. |
| Why AI? | It reads code it hasn't seen and maps each call onto our adapters. Everything else is plain code. |
| Why not Terraform or Kubernetes? | They set up infrastructure; they don't remove AWS from the code. |
| How do you prove it works? | The same original tests pass on both sides, and data counts and checksums match. |
| What if it fails? | AWS stays untouched until the checks pass. |
| Can you go back? | Yes: we redeploy the original code and copy the data back, including data written after the move, then rerun the same tests. |
| Why trust the AI? | It runs locally, can only propose code for one function, every output is rule-checked, and a human approves the plan. |
| Does it really save money? | Only if the numbers say so. We use published prices and include ops costs; sometimes AWS wins. |
| What about services you don't support? | We flag them red for a human; we don't fake them. |
| Do passing tests prove everything? | No, only what the tests cover. Our adapter tests cover retries and the DLQ separately. |
| Is S3 → SeaweedFS "AI migration"? | No, it's a config change, and we label it that way. |
| Zero downtime? | We don't claim it. Cut-over needs a short write freeze; the DEPLOY.md describes it. |

## 5. Never claim
Zero downtime · full compatibility · "migrates any app" · "always cheaper" · "fully local" if the Claude fallback was used.

## 6. Submission checklist (A, by T+5:45)
- [ ] README matches what works · [ ] `runs/golden/` + `demo/llm-cache/` committed, no secrets · [ ] video link · [ ] team names · [ ] form submitted · [ ] slides PDF
