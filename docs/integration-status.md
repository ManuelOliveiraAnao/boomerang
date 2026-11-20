# Integration status: branch `integration-abc` (20 Nov, 13:25)

Work continues on **`integration-abc`**. It holds A + B + C + D, merged in the order both merge guides give.

| Merged | Commit | Conflicts and resolution |
|---|---|---|
| C (`role-c`) | `f73954f` (C1–C7) | base |
| A (`role-a-platform-plan`) | `fdf0b69` | none |
| B (`member-b-after-c1`) | `51c6ff8` | 10 files: kept C+A for `__init__.py` ×2, `lab.py`, `provisioner_aws.py`, `verifier.py`, `datamover.py`, `test_stubs.py` (as both guides say), and C for `cli.py`, `pyproject.toml`, `tests/test_cli.py` (B had C2-era copies) |
| D (`D`) | `2ce6d29` | `boomerang/cost.py`: kept D's (C had the stub) |
| B again (`member-b-implementation`) | `6fc7db0` | B's rewrite of scanner/assess/lockin/lost/generator/kv (built on `role-c`). 20 files: **B's side** for all B files and fixtures; **ours** for C's tests (B's copy predates the A/D merges) and `.gitignore` (same `!**/*.j2` line). B's old tests `test_scanner.py`, `test_generator.py`, `test_exhaustive_member_b.py` were removed: they test the previous API (`assess(pam)` without `emit`, dict PAMs, `generate_secrets`, `WEIGHTS`, `scanner.ai.scan`) that the rewrite replaced; `tests/test_member_b.py` + `tests/sovereign_sdk/test_kv_member_b.py` are B's tests now. The ShopLite scanner fixes below were re-applied on top of the rewrite (additive, end of `scanner/static.py`). **Not merged on purpose:** the partial-migration work (A's guide `afad599` on `role-a-platform-plan`, and B's feature in progress). |

Checked after merging: A's files are identical to `role-a-platform-plan`, C's to `role-c`, `cli_platform.py` did not
come back, no stub is left anywhere (`tests/test_stubs.py::test_no_stub_is_left`).

**Unit tests: `pytest` → 451 passed, 23 skipped** (after B's rewrite; the skips need Postgres / S3 credentials / Docker).

## Changed in other roles' files (please review)

| Owner | File | Why |
|---|---|---|
| B | `boomerang/scanner/static.py` | On the real ShopLite the scan found **no DynamoDB usage** (calls go through `_table()`) and read no Terraform details, so `render()` crashed ("A KV table must have a partition key") and A's provisioner got variable names (`sqs`, `s3`) as resource names. Added: helper receivers, `name_env_var` from `os.environ[...]`, real names / `hash_key` / `range_key` / GSIs / visibility timeout / `maxReceiveCount` from Terraform, the DLQ folded into its queue (no `order_events_dlq` component), `entrypoints` from `FastAPI()`. Guarded by `tests/test_shoplite_integration.py`. |
| D | `ui/components/breaks.py` | "Lock-in removed 100%" before execute (`lock_in_after` is `None` then): shows the score instead. |
| all | `.gitignore` | `laptop.env` (the generator writes real passwords into it; `runs/golden/` is committed). |
| A | — | `tests/__init__.py` makes tests a package: shared helpers are imported as `tests.test_…`. |
| A | `tests/test_verifier.py` | **macOS** (D, 20 Nov 14:20): 5 verifier tests failed on a Mac. They patched `shutil.which` to `/usr/bin/<name>` for every tool, and `FakeDocker` really runs `bash -n deploy.sh`; macOS has bash in `/bin`, not `/usr/bin`, so the config check failed with `FileNotFoundError` and boot/schema/tests/health never ran. Now a shared `_fake_which()` keeps bash's real path and fakes the rest. Linux and Git Bash behave as before; on Windows `find_bash()` still finds Git Bash first. Test-only: `verifier.py` is unchanged. |

C-side fixes from the integration: rule 7 and the L14 1:1 check follow helpers (`_table()` → `kv.Table`), so on
ShopLite only `get_order` (`ConsistentRead`) and `list_orders_by_customer` (string condition) go to the AI; small
helpers come whole into the AI context; `plan` accepts the `runs/<id>/` folder the UI creates for its log; the
"waiting for approval" event is `info` (the UI showed Approve as done).

## Run it

```powershell
$env:PYTHONUTF8 = "1"
uv pip install -e ".[dev]"            # moto[server] is new (A)
boomerang lab up                      # fake AWS (Docker, or a moto process without Docker)
ollama serve                          # + ollama pull qwen2.5-coder:7b
streamlit run ui/app.py               # Dashboard → Live → Migrate to Leaseweb → Approve plan and migrate
```

CLI only: `boomerang migrate sample-app/aws --yes`, then `boomerang roundtrip <run_id>`.

Checked in the browser on this branch: Migrate → `boomerang plan` (real scan + lights: 1 🟢 · 2 🟠 · 3 🔴 per
component, 4 code references on `orders`), Approve → `approve --by ui` → `execute`. Without a lab, `execute` stops at
the baseline with "the lab (fake AWS) is down: run `boomerang lab up`".

## Not done or not verified yet

1. **A full live run** (lab + Docker + Ollama): verify on B's real generated stack, data mover on the real Leaseweb
   side, round trip (A's G2–G4, G6). Nothing past the baseline has run for real on this branch.
2. **C7 golden run**: record `demo/llm-cache` and `runs/golden` (see `demo/llm-cache/README.md`), then the offline test.
3. Integration tests (`pytest -m integration`) were not run after the merge here (no Docker on this laptop).
4. Known open B items from D's notes: secrets inside `rabbitmq/definitions.json` and `seaweedfs/s3.json`; the kv
   adapter's conditions hard-code `order_id`.

## Partial migration (docs/16-partial-migration-guide.md) — 20 Nov, 14:00

Merged B's `member-b-partial-migration` (`e85c28f`: `Scope`/`ScopeStep` models, `scope.py`, scoped `render`,
`assess`, `lockin`, `lost`) and A's guide (`afad599`), then finished the rest:

| Part | What | Where |
|---|---|---|
| C | `approve(run_id, scope=[...])` stores the scope in `approval.json` (and `run.json`); `execute` de-locks only in-scope components (kept ones keep boto3 and their module clients; the prompt says so), renders and moves data for the scope, adds the 🔴 hybrid finding; hybrid round trip keeps AWS and copies back only the self-hosted components; `move_over` / `move_back` steps (self-hosted data → AWS, rebuild for the new scope, verify, move the new scope over), history in `run.json` `steps` | `pipeline.py`, `delock.py`, `prompts/delock.md` |
| C | CLI: `boomerang approve <id> --only orders,receipts`, `migrate --only …`, `move-over <id> order_events`, `move-back <id> orders` | `cli.py` |
| A | `datamover.move(..., components=[...])` (default: all, unchanged) | `datamover.py` |
| B | hybrid env used `<aws-name>` placeholders, which overrode the queue name in `laptop.env`: now the real names; `assess` shares `scope.hybrid_finding` | `generators/leaseweb.py`, `assess.py` |
| D | "What breaks": "Choose what moves" cards per movable component (🔴 listed, not movable), hybrid/all summary, notes (AWS credentials, no cross-side transactions, bucket events), "Approve and move N of M"; new **Placement** tab: AWS and self-hosted lanes, Move over / Move back on a passed live run, step history; **Costs**: all on AWS vs the chosen placement vs all self-hosted, per service, by scale (`cost.scenario()` / `compare()`) | `ui/components/breaks.py`, `ui/components/scope.py`, `ui/scope.py`, `ui/components/cost_compare.py`, `boomerang/cost.py`, `ui/runner.py` |

Full scope (the default) = the old behaviour; the other roles are called exactly as before. Lock-in before/after
are scored with an empty scope (B's `score()` now excludes the scope; its default would drop every component).
Tests: `tests/test_partial_migration.py` (14). Checked in the browser: unchecking `order_events` → "Approve: move
2 of 3" → `approval.json` scope `[orders, receipts]` → `execute` uses it.

**Not done / limits:** a real hybrid run (lab + Docker:
the containers reach moto through `host.docker.internal`); during a hybrid verify the app's tests drain the
SQS queue that stays on AWS (its seeded messages are not counted anywhere, but they are gone); B's new `scan()`
calls the AI, so `plan` needs Ollama (or `BOOMERANG_ALLOW_FIXTURES=1` for static hints only).

## D reconciled with integration-abc — 20 Nov, 14:15 (branch `D` @ `8861e2c`)

The partial-migration UI was built twice at the same time (`7a3eb61` here, `c1ec332` on `D`). `D` merged
`integration-abc` and kept one UI:

- **Runner:** integration-abc's interface, `runner.start(..., components=[...])` → `approve --only a,b`,
  `move-over|move-back <id> <c> --by ui`.
- **What breaks:** D's "Choose what moves" gate replaces `_choose_scope()`. The approved message still names what is
  self-hosted and what stays on AWS.
- **Data & round trip:** the "Where each component runs" panel (`_scope_panel`) is gone; the **Placement** tab
  covers it, with the same rules: moves only on a passed live run with nothing running, the last self-hosted
  component can't be moved back (that is the round trip), data counts read `component_id` / `direction`.
- **Costs:** every cost line is tagged with what it pays for; `cost.scenario()` prices any placement (a hybrid
  pays the AWS services it keeps, both bases, and the traffic back to AWS as egress); `compare()`,
  `per_component()`, `scale_usage()`. With the default usage, all self-hosted gets cheaper than AWS from about ×54.
- **Verifier tests on macOS:** fixed, see the table above.

Checked on a Mac: `pytest tests` → **482 passed, 24 skipped**. Through `ui.runner` on a real ShopLite plan:
`approve --only orders,receipts` → `approval.json` scope `[orders, receipts]`; `move-over` is parsed and refused
before a passed run; the dashboard shows the approval as Hybrid, 2 of 3. Not checked: a full live run (no Ollama
on that laptop). `D` merges into `integration-abc` without conflicts.
