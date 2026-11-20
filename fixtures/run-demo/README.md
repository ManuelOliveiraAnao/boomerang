# run-demo: UI fixture in C's run-directory layout

A complete `runs/<id>/` folder (docs/15 §5) so the dashboard can be built and demoed before the
pipeline (C4/C6) exists. The UI shows a ⚠️ "demo data" banner because `run.json` lists
`fixtures_used: ["ui-demo"]`.

| File | Where it comes from |
|---|---|
| `pam.json`, `assessment.json` | **Real:** B's `scan_static()` + `assess()` on A's `sample-app/aws` |
| `leaseweb/**` | **Real:** B's `generators.leaseweb.render()` (on B's `fixtures/pam.aws.json`, because the scanned PAM has no partition keys yet). `secrets.env`/`laptop.env` removed and secret values in JSON masked |
| `run.json` (`lost`) | **Real:** B's `lost.build()`; lock-in from B's `lockin.score()` |
| `run.json` (verifications, data moves, round trip), `delock.json`, `diffs/`, `events.jsonl` | **Illustrative**, in C's models: the numbers follow the pitch (7/7 tests, 25 → 26 orders). The `list_orders_by_customer` AI rewrite and the `_from_ddb` fix show the L13/L14 shapes; today's ShopLite maps 1:1 everywhere |

Rebuild from an integrated A+B+C tree: `python -I make_demo_run.py <tree> fixtures/run-demo`.
