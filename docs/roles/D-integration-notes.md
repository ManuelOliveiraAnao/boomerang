# Role D: integration notes from the other branches

Snapshot of `origin/role-a-platform-plan` (A) and `origin/Francisco-membro-b-development-plan` (B), read on 20 Nov 2026. No branch from C yet. Re-check before each checkpoint (`git fetch && git log main..origin/<branch>`).

## 1. What exists now

| Branch | Delivered | Matters to D because |
|---|---|---|
| A | `models.py` (provisional copy of the contract), `events.py`, `pyproject.toml`, `verifier.py`, `datamover.py`, `lab.py`, `provisioner_aws.py`, ShopLite, queue adapter | Tabs 3 and 4 read A's `Verification` and `DataMove`; the live feed hooks into A's `Emitter` |
| B | `scanner/`, `assess.py`, `lockin.py`, `lost.py`, Leaseweb generator + templates, kv adapter, `fixtures/pam.aws.json` | Tabs 1, 3 and 4 read B's findings, generated files and lost report |
| C | **Nothing pushed yet**: no `cli.py`, `pipeline.py`, `config.py`, `delock.py`, `report.py` | The Migrate / Round trip buttons and the Code tab have nothing to call |

## 2. What D must implement or change

### Data & fixtures
- [ ] **B1 is incomplete:** only `fixtures/pam.aws.json` exists. There is no assessment, verification, data, lost or events fixture. **D generates its own** with a small script that runs B's `assess()` + `lost.build()` on `pam.aws.json` and writes hand-made `verification.json`, `data.json`, `events.jsonl` in A's model shapes. Agree the file names with B, or B adopts these:
  `fixtures/{assessment,delock,verification,data,lost,costs}.json` + `fixtures/events.jsonl`.
- [ ] The UI must accept **plain dicts and Pydantic models**: B returns dicts (`assess`, `lost.build`, `scan`), A returns models (`Verification`, `DataMove`). Normalise with `model_dump()` when present, as B does in `_model_to_dict`.

### Tab 1: What breaks (D3)
- [ ] B's findings are **per component × feature** (`put`, `get`, `send`, `dlq`, ...), so ShopLite gives ~20 rows, not 8. **Group by component**, show the worst light per component, expand to features. Counters count components, not features.
- [ ] The traffic-light labels must match B's rules: `object_store` = 🟢, `kv_table` / `queue` = 🟠, `function` / `identity` / `monitoring` = 🔴.
- [ ] B's `overall` **includes reds**; the spec says overall = worst *migrated* light. Compute it in the UI from non-red findings, or ask B to change it.
- [ ] `evidence` is copied from `usages`, which are **empty in the fixture**. Show "no code reference" gracefully; real refs arrive once the scanner runs on ShopLite.
- [ ] **Lock-in gauge:** `lockin.score()` is an **unbounded weighted sum** (provider weight × number of usages), not 0–100. Either show before/after as raw numbers or normalise (e.g. `after / before`). Agree with B.

### Tab 2: Code (D5)
- [ ] Blocked on C (`delock.py`, `FunctionRewrite`). Build it on a hand-made `delock.json` fixture with the `FunctionRewrite` shape (`analysis`, `new_source`, `unsupported`) plus the original source per function, and label S3 as "config change".

### Tab 3: Leaseweb setup (D5)
- [ ] B's generator writes these files to `runs/<id>/leaseweb/` (default `runs/leaseweb/` if no `out_dir`): `docker-compose.yml`, `secrets.env`, `rabbitmq/definitions.json`, `seaweedfs/s3.json`, `cloud-init.yaml`, `deploy.sh`, `backup/pg_backup.sh`, `DEPLOY.md`, `app/Dockerfile`, `app/requirements.txt` (+ the copied `sovereign_sdk`).
- [ ] ⚠️ **Never display `secrets.env`**, and mask `PASSWORD|SECRET|KEY|TOKEN` values in any file shown on screen. The demo is projected and the repo is public. Exclude `secrets.env` from the zip download too, or mask it.
- [ ] Verification checklist uses A's check names, in this order: `config` → `boot` → `schema` → `tests` → `health`. The `tests` check detail is `"<passed>/<total> passed"`; `Verification.tests` holds the full `TestReport` (failures with tracebacks).

### Tab 4: Data & round trip (D6)
- [ ] Read `DataMove` (`direction`, `counts[]` with `source_count`, `target_count`, `checksum_match`, `notes`). Two moves: `aws->leaseweb` and `leaseweb->aws`.
- [ ] `checksum_match` can be `None` (not computed): show "–", not ❌.
- [ ] Copy failures arrive as `notes` like `"copy failed: ..."` with zero counts. Show them in red.
- [ ] "What got lost" comes from B's `lost.build()`: `status` ∈ `preserved` / `changed` / `lost`. Group by status.

### Live feed & replay (D7)
- [ ] Hook into A's `events.Emitter(run_id, sinks=[ui_sink])`. The sink receives an **`Event` model**, so store `ev.model_dump()` in `session_state.events` (my `data.stage_status` reads dicts).
- [ ] Stage names that actually get emitted: `assess` (B), `verify` (A), `data` (A, also for `reset_stack`), `rollback` (A `freeze`). `scan`, `approve`, `delock`, `generate`, `fix`, `report` depend on C.
- [ ] Extra event payloads to render: `data.count` (a `DataCount` dict) on data events, `findings` (int) on assess.

### Costs (D2, D4)
- [ ] **Nobody else has touched costs.** `pricing/*.yaml`, `boomerang/cost.py` and `tests/test_cost.py` are fully on D. The price data was meant to be filled before the event: do it first.

### Setup
- [ ] After A's branch merges, replace my ad-hoc venv with `uv pip install -e ".[dev]"`. Streamlit is already in A's `pyproject.toml` dependencies.
- [ ] A's pytest config uses `testpaths = ["tests"]` and skips `fixtures/`: put `test_cost.py` in `tests/`.

## 3. Risks to raise with the team
1. **C hasn't pushed the scaffold.** `models.py` and `events.py` exist only as A's *provisional* copies. If C's version differs, the UI shapes may change. Ask C to adopt A's copy as-is.
2. **A and B both create `sovereign_sdk/__init__.py`** and both are missing pieces the other has (B has `boomerang/__init__.py`, A doesn't). Expect a merge conflict; trivial, but someone must own it.
3. **B's modules return dicts and don't import `models.py`.** Fine for the UI, but the pipeline (C) will need `Assessment.model_validate(...)` at the boundary.
4. **Generator default output is `runs/leaseweb/`**, not `runs/<id>/leaseweb/`. The pipeline must pass `out_dir`, or replay can't find the files per run.
5. **Secrets on screen:** see Tab 3.

## 5. Review: does the UI match the project? (B@680c7c6, A@483afdb, no C branch)

**Matches the spec ([05](../05-ui.md), README):** sidebar (source app, Migrate, Round trip, Live/Replay, LLM badge) · 5 tabs · What breaks has lights, evidence, counters, lock-in, red box, Approve · Costs follows [12](../12-costs.md) exactly (sources, missing-price refusal, ops hours, EU Data Act date) · the landing page's 9 steps, 3 lights, round trip and "never claim" list match the README and [09](../09-demo-deploy-pitch.md). Extra vs spec: the Overview landing page.

**Gaps that depend on others:**
| UI part | Status | Blocked by |
|---|---|---|
| Migrate / Round trip buttons, live feed, Approve → pipeline continues | placeholder toast | **C (no branch yet):** `pipeline.py`, `cli.py`, events wiring |
| Code tab (diffs, fixer) | empty state | **C:** `delock.py`, `fixer.py` |
| "Local AI" badge, "AI never sees secrets" on the landing page | true only once C's `llm.py` + `redact.py` exist | **C** |
| Leaseweb setup tab | empty state (D5) | B's generator exists; A's verifier exists. Can be built now on a generated run |
| Data & round trip tab | empty state (D6) | A's data mover exists (25/25/3 → 26 after round trip, as in the pitch) |

**Can be done without C:** A now has `cli_platform.py` (`lab`, `aws provision/test`, `verify`, `data`). The UI could call A's `verify()` and `datamover.move()` and B's `generators.leaseweb.render()` directly to fill tabs 3 and 4 live, if C is late.

**Mismatches to fix:**
1. **Counters:** [05](../05-ui.md) / [09](../09-demo-deploy-pitch.md) say "1 🟢 · 4 🟠 · 3 🔴". The UI counts components (now **1 · 3 · 3**, 1 · 2 · 3 once B folds the DLQ into `order_events`); per feature B now gives **4 · 16 · 4** (24 findings). The tab shows both now; the pitch script must quote one of them.
2. **Scanner (B), still open after B's fixes:** `orders` still has **0 code usages** (calls go through `_table()`, a function return, which the new `x = dynamodb.Table(...)` fix doesn't follow); `conditional_write` not detected although A's `db.py` now uses `attribute_not_exists` *and* `attribute_exists`; `order_events_dlq` still a separate component; `app_name` is `"aws"`, `entrypoints` empty. The pitch says "the scan found every AWS dependency in code", which is **not true yet** for DynamoDB.
3. **Costs vs pitch:** [09](../09-demo-deploy-pitch.md) closes with costs as a selling point. With the default ShopLite usage AWS is cheaper (€89.52 vs €392.29/month, ops hours dominate); Leaseweb wins from ~×50 traffic. The script should say "it depends on size, here's the proof", or demo at a larger scale.
4. **Secrets:** B's generator still writes passwords into `secrets.env` and the compose file. D5 must mask them on screen.
5. **A's RabbitMQ DLQ behaviour is unverified** (no Docker on A's laptop, see `docs/roles/A-testing-status.md` N1). Don't claim "DLQ after 3 receives" in the demo until it runs on real RabbitMQ.

## 4. Found while building D3 (tell B)
Ran B's `scan_static()` + `assess()` on A's real ShopLite (`sample-app/aws`). Output saved as `fixtures/pam.scanned.json` and `fixtures/assessment.json`.
- **`orders` (DynamoDB) has 0 code usages:** the scanner misses every call in `app/db.py`, so the DynamoDB card shows no evidence. It also loses `conditional_write`.
- **Features with no rule are dropped silently:** S3 `list` / `delete` and DynamoDB `put/get/update/delete/query` produce no finding. Either add rules or show them as 🟢/🟠 explicitly.
- **`order_events_dlq` appears as its own queue** with the full feature list of the main queue; it should be folded into `order_events` (the DLQ is a feature, not a second component).
- **`app_name` is `"aws"`** (the folder name) instead of `ShopLite`, and `entrypoints` is empty.
- Every finding of a component repeats **all** of that component's usages as evidence; the UI dedupes, but per-feature evidence would be more precise.
