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
