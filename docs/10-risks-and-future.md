# 10 · Risks & future

## 1. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Docker not working on someone's laptop | Medium | High | Install before the event; engine work (B, C, D) can proceed with fixtures; lab on the presenter laptop | A |
| R2 | AI rewrite produces wrong/non-compiling code | Medium | High | Conventions in the prompt, compile check + retry, fix loop, hand-written local flavor as reference | C |
| R3 | LLM latency makes live demo slow | High | Medium | Parallel rewrites, record cache, replay mode, optional Sonnet via env | C, D |
| R4 | API rate limit / outage during demo | Low | High | Replay mode from committed cache works offline | C |
| R5 | Scope creep | High | High | Cut lines at each checkpoint; lead says no | C |
| R6 | Integration surprises late | Medium | High | Shared models + fixtures from minute one; checkpoints at 2:00 and 3:30 | C |
| R7 | moto behaves differently from real AWS | Low | Low | Only basic S3/DynamoDB/SQS ops are used; say "emulated" openly | A |
| R8 | MinIO image unavailable | Low | Medium | `quay.io/minio/minio`, fallback `cgr.dev/chainguard/minio` | A |
| R9 | Secret leaked in the public repo | Low | High | `.env` ignored, staged diff check, revoke immediately | All |
| R10 | Port conflicts with local services | Medium | Low | Troubleshooting table in [08](08-setup-and-workflow.md) | A |
| R11 | Pre-written docs vs hackathon rules | Low | High | Check rules before the event; docs are specs | Team |
| R12 | Fix loop never needed (no failure to show) | Medium | Low | Show a recorded run with a real failure; never fake one | D |

## 2. After the hackathon (product roadmap)

1. **More mappings:** Lambda ⇄ containers, IAM ⇄ policy files, SNS ⇄ NATS, RDS ⇄ Postgres (config-only), ElastiCache ⇄ Redis.
2. **More targets:** generators for EU sovereign clouds, GPU clouds, Kubernetes/Helm.
3. **Data migration:** online copy with checksums; dual-write cut-over plans.
4. **The ops agent ("Phase 2")**: after migration, an agent watches metrics/logs of the self-hosted stack, diagnoses incidents, proposes fixes with human approval, writes postmortems. Removes the second reason companies stay on hyperscalers: operations.
5. **Larger codebases:** AST-based static analysis, dependency-graph-aware rewrite ordering, per-module test selection.
6. **Languages:** TypeScript/Node, Java.
7. **Cost model:** real provider pricing → savings and break-even per move.
