# 06 · Roadmap: the 6 hours

T+0:00 = the moment building is allowed. Times are targets; **checkpoints are not optional**.

## Timeline

| Time | A: Platform | B: Understand | C: Transform (lead) | D: Experience |
|---|---|---|---|---|
| **0:00–0:15** | Kickoff (all): confirm roles, open the task board, `boomerang lab` images already pulled | ← | ← | ← |
| **0:15–0:45** | A1 lab compose up; A2 start AWS flavor | B1 fixtures (PAM aws/local, plan, events, reports) → push by 0:40; B2 mappings + conventions | **C1 scaffold** (pyproject, package, `models.py`, config, events, CLI skeleton) → **push by 0:45** | D1 Streamlit skeleton + theme on fixtures |
| **0:45–2:00** | A2 AWS flavor + A4 tests pass (with dev bootstrap); A5 provisioner | B3 static hints; B4 AI scanner; B5 lock-in | C2 `llm.py` + cache; C3 rewriter (on `fixtures/pam.aws.json`) | D2 Scan + Plan tabs; D3 event feed |
| **2:00 ✅ CHECKPOINT 1** (15 min, all) | `boomerang test sample-app/aws --platform aws` green | `boomerang scan sample-app/aws` → valid PAM | `rewrite_all` produces 3 rewritten files from the real PAM | UI shows real scan output |
| **2:15–3:30** | A3 local flavor; A6 validator; `boomerang test sample-app/local` green | B6 planner; B7 generators (local, then aws) | C4 pipeline `migrate()` end-to-end aws→local; C5 fixer + loop | D4 Migrate + Validate tabs, diff viewer |
| **3:30 ✅ CHECKPOINT 2** (15 min, all) | **`boomerang migrate sample-app/aws --to local` passes tests (with fixes allowed)** | ← | ← | UI can run it live |
| **3:45–4:30** | Help C on reverse leg; S2 if green | Tune scan/plan prompts on local → aws | C6 `roundtrip()`; reverse-direction fixes | D5 Round-trip tab; D6 replay mode |
| **4:30 ✅ CHECKPOINT 3: feature freeze** | Round trip passes from the UI | ← | ← | ← |
| **4:30–5:15** | Record golden run → `runs/golden/`, commit LLM cache; A7 backup video | README polish, docs updated to match reality | Bug fixes only; replay mode verified offline | D7 slides; D8 demo script |
| **5:15 ✅ CODE FREEZE** | | | | |
| **5:15–5:45** | Rehearse the demo 3× (all): one live, one replay, one with "the Wi-Fi is down" | ← | ← | ← |
| **5:45–6:00** | Submit (A): repo link, video, description. Buffer. | ← | ← | ← |

## Critical path

`C1 scaffold → C2 llm → B4 scanner → C3 rewriter → C4 pipeline → C5 fix loop → C6 roundtrip → golden run`

Everything else runs in parallel against fixtures. The lead (C) protects this path: answer questions fast, merge quickly, and push work off it.

## Cut lines (apply at the checkpoint if behind)

| If at… | …this is not working | Cut to |
|---|---|---|
| CP1 (2:00) | AI scanner unreliable | Use static hints + a hand-corrected PAM from `fixtures/` for the demo; keep the AI scanner as "shown on the side" |
| CP1 | Rewriter output doesn't compile | Rewrite one file at a time with the syntax-error retry; reduce scope to DynamoDB ⇄ Postgres + S3 ⇄ MinIO, keep SQS on a hand-written adapter |
| CP2 (3:30) | Forward migration not green | Drop reverse migration of SQS; demo forward migration + reverse for DynamoDB/S3 only |
| CP2 | Fix loop flaky | Max attempts = 2; show the recorded run where it worked |
| CP3 (4:30) | Round trip not green from UI | Demo forward leg live + **replay** of a recorded round trip (that ran green in the CLI) |
| CP3 | UI unfinished | Drop tabs 4/6; Round-trip tab + Migrate tab only |
| Any | LLM latency kills the demo | `record` mode cache warm (re-run = instant), or switch `BOOMERANG_MODEL=claude-sonnet-5-5` |

## Checkpoint ritual (15 min)

1. Everyone pushes to `main` (green or not, behind a flag if broken).
2. Lead pulls, runs the checkpoint command on one machine, shares screen.
3. 5 min: what's red? Who unblocks it? Apply cut lines.
4. Update the task board.
