# 06 · Roadmap: the 6 hours (v2)

**Honest note:** v2 is more work than v1 (adapters, live boot, data moves, costs). It fits in 6 hours only because (a) the AI SRE is cut, (b) "back to AWS" is a rollback rather than a second rewrite, and (c) price data, model downloads and Docker images are prepared **before** the event. Apply the cut lines without hesitation.

## Before the event (not code)
- Every laptop: Docker Desktop, images pulled, Ollama + model pulled, Q1 benchmark done ([11](11-decisions.md)).
- D (with help): fill `pricing/prices.yaml` from official price pages, with URL + date per line ([12](12-costs.md)).
- Ask organizers for Leaseweb access (Q5).

## Timeline

| Time | A: Platform & Data | B: Understand & Generate | C: Lead & Transform | D: Experience & Costs |
|---|---|---|---|---|
| **0:00–0:15** | Kickoff: roles, issues board, `ollama run` warm-up on presenter laptop | ← | ← | ← |
| **0:15–0:45** | A1 lab (moto) · A2 start ShopLite | B1 fixtures (PAM, assessment, verification, data, lost, events) by 0:40 · B2 static scan | **C1 scaffold + models → push by 0:45** | D1 UI skeleton on fixtures · D2 cost model data loaded |
| **0:45–2:00** | A2 ShopLite + tests + TF · A3 AWS provisioner + seed · A4 `queue` adapter + tests | B3 AI scan · B4 traffic lights · B5 `kv` adapter + tests | C2 `llm.py` (Ollama + cache + redact) · C3 slicer + de-lock on fixtures | D3 "What breaks" tab · D4 cost engine + Costs tab |
| **2:00 ✅ CP1** | ShopLite 7/7 on moto; queue adapter tests green | `scan` + lights real; kv adapter tests green | de-lock rewrites ShopLite functions; rules check passes | Tabs 1 + 5 on fixtures |
| **2:00–3:30** | A5 verifier (config → boot → tests → health) · A6 data mover | B6 Leaseweb generator (all files) · B7 `lost.py` | C4 pipeline migrate (with approval gate) · C5 fixer | D5 Code + Leaseweb setup tabs |
| **3:30 ✅ CP2** | **`boomerang migrate sample-app/aws --yes` → Leaseweb setup boots on laptop, tests 7/7, health ✅, data counts match** | ← | ← | UI runs it live |
| **3:30–4:30** | Data back (leaseweb→aws) · help C | Lost report wording · DEPLOY.md polish | C6 roundtrip · bug fixes | D6 Data & round trip tab · D7 replay |
| **4:30 ✅ CP3 feature freeze** | Round trip green from the UI | ← | ← | ← |
| **4:30–5:15** | A7 golden run + backup video | README/docs match reality | C7 commit LLM cache; offline replay test | D8 slides + script |
| **5:15 code freeze** · 5:15–5:45 rehearse 3× (live, replay, "no network") · 5:45 submit (A) | | | | |

## Critical path
`C1 → C2 → C3 de-lock → B6 generator → A5 verifier → C4 pipeline → A6 data → C6 round trip → golden run`
Adapters (A4, B5) must be green by CP1. Everything else runs against fixtures.

## Cut lines

| At | If this is behind | Cut to |
|---|---|---|
| CP1 | 7B model too slow/unreliable | `BOOMERANG_LLM_PROVIDER=anthropic`; drop "fully local" from the pitch |
| CP1 | AI scan unreliable | Static scan + hand-checked PAM; AI only writes the traffic-light explanations |
| CP1 | queue adapter incomplete | Keep ack + DLQ; mark visibility timeout 🔴 "not emulated" (honest) |
| CP2 | Live boot of `api` container flaky | Boot infra services only; run tests in-process; health via `TestClient` |
| CP2 | Fixer flaky | 1 attempt; show recorded run |
| CP2 | Data mover for queues | Move tables + objects only; report "pending messages drained before cut-over" |
| CP3 | Round trip from UI | Run it in the CLI, show it via **replay** in the UI |
| Any | Cost tab | Static table with sources; no editing |
