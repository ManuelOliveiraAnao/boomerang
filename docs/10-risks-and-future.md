# 10 · Risks & future (v2)

## 1. Risk register

| # | Risk | Mitigation | Owner |
|---|---|---|---|
| R1 | Local 7B model too slow/unreliable on stage | Function-level prompts (< 6k tokens), one model only, `keep_alive=60m`, temperature 0, record cache, replay; Claude fallback by env | C |
| R2 | Model swap freeze (10–25 s) | Exactly one model for every task | C |
| R3 | "Valid JSON, wrong logic" | Reasoning field first in every schema; rules check after every output | C |
| R4 | Rewritten code passes tests but is wrong | Tests are the original ones; adapters carry the semantics and have their own tests; say "proves what the tests cover" | C, B |
| R5 | AI writes its own tests | Forbidden: `tests/` is read-only to the AI (rules check) | C |
| R6 | Semantic drift DynamoDB → Postgres, SQS → RabbitMQ | Adapters handle conditions/ack/visibility/DLQ; unsupported features are 🔴 | B, A |
| R7 | Hidden dependencies (IAM roles, S3 → Lambda triggers) | Scan Terraform, Dockerfile, serverless.yml, .env.example; flag 🔴 | B |
| R8 | Data out of sync during rollback | Writes frozen during each copy; counts + checksums; the demo shows a write on Leaseweb coming back | A |
| R9 | Secrets in prompts / repo | Redaction, git-ignore, scoped generated keys ([13](13-security.md)) | C, B |
| R10 | Docker missing / ports busy | Install before the event; ports bound to 127.0.0.1; troubleshooting in [08](08-setup-and-workflow.md) | A |
| R11 | moto differs from real AWS | Only basic ops used; say "emulated" | A |
| R12 | Image unavailable (MinIO history) | SeaweedFS, RabbitMQ, Postgres official images; pulled before the event | A |
| R13 | Scope too big for 6 h | Cut lines in [06](06-roadmap-6h.md); AI SRE already cut | C |
| R14 | Prices wrong or stale | Every price has URL + date; missing price → no total shown | D |
| R15 | Data Act rules change (Digital Omnibus) | Re-check before the demo; UI shows the legal basis and date | D |

## 2. Roadmap after the hackathon

1. **AI SRE (cut from the hackathon, designed):** watches the Leaseweb stack and proposes fixes. Design rules, carried over from the team's analysis:
   - Output is a **fixed enum action** (e.g. `RESTART_SERVICE`, `ROTATE_SERVICE_KEY`) chosen after a `root_cause_analysis` field, never a shell string; execution is separate, deterministic code.
   - **Log sanitization:** truncate and strip log lines before they reach the model (log injection via User-Agent etc.).
   - **Two-phase key rotation:** create new key → update app secret + rolling restart → verify health 200 → revoke old key.
   - **Cooldown lock:** max 1 automated action per service per 60 s; no restart loops.
   - **Human approval** for anything beyond restart; no secrets in prompts.
2. **ScyllaDB Alternator path** for DynamoDB-heavy apps (green: endpoint change only).
3. More mappings: Lambda → containers + cron/queue consumers, SNS, EventBridge, IAM → policy review.
4. Real Leaseweb provisioning via the official Terraform provider; Leaseweb Object Storage by default.
5. Online data sync for near-zero-downtime cut-over (still never claimed as zero).
6. More languages (TypeScript, Java) and bigger codebases (dependency-aware ordering).
