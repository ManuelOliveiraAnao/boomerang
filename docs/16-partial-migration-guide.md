# 16 · Partial migration: implementation guide

**Goal:** let a company move **some** services from AWS to its own servers, keep the rest on AWS, and later move more over or move some back. Example: move the orders database and the receipts storage, keep the order-events queue on SQS; next week move the queue too; if the database causes trouble, move only the database back.

**Start from Joao's merge (A + B + C).** Create a branch from it, e.g. `feature/partial-migration`. This guide uses the function names that exist after that merge; check them against the merged code before starting.

---

## 1. Concepts

| Term | Meaning |
|---|---|
| **Movable component** | A PAM component of kind `object_store`, `kv_table` or `queue` (ShopLite: `orders`, `receipts`, `order_events`). |
| **Scope** | The set of movable components that should run self-hosted. Everything else stays on AWS. Empty scope = nothing moved; all movable components = today's full migration. |
| **Hybrid** | Any scope that is neither empty nor all. The app runs on the company's servers and talks to self-hosted services **and** AWS at the same time. |
| **Step** | One change of scope: move components over, or move components back. A run can have several steps. |

**Rules:**
- 🔴 components (Lambda, IAM, alarms) are never in a scope; they stay flagged as today.
- A component is either fully self-hosted or fully on AWS: no component is split.
- The original AWS code (`runs/<id>/original/`) stays untouched; it's the source for moving a component back.
- **Default scope = all movable components**, so the full migration and every existing test keep working unchanged.

## 2. The contract change (C: `boomerang/models.py`)

```python
class Scope(BaseModel):                       # new, not LLM-facing
    selfhosted: list[str]                     # component ids running self-hosted
    def has(self, component_id: str) -> bool: return component_id in self.selfhosted

class RunSummary(BaseModel):                  # add, with a default so old run.json files still load
    scope: Scope | None = None                # None = all movable components (today's behavior)
    steps: list[ScopeStep] = []

class ScopeStep(BaseModel):                   # new: history of scope changes
    at: float
    action: Literal["move_over", "move_back"]
    components: list[str]
    verification_ok: bool | None = None
    data_moves: list[DataMove] = []
```
Helper (C, `boomerang/scope.py`): `full_scope(pam) -> Scope`, `movable(pam) -> list[Component]`, `validate(pam, scope)` (unknown ids, red components → error).

## 3. Changes per role

### 3.1 C: pipeline, de-lock, rules (critical path)

**`pipeline.approve(run_id, scope=None)`**: store the scope (default: all movable components) in `run.json`. The UI/CLI pass a list of component ids.

**`delock`**: only rewrite usages of components in scope.
- The slicer already finds functions by usage. Filter: a function is rewritten **only if it uses at least one in-scope component**.
- A function that uses an in-scope **and** an out-of-scope component: rewrite only the in-scope calls. Add to the de-lock prompt: *"Calls to `<kept components>` must stay exactly as they are (boto3)."*
- **Module-level clients:** swap only the clients of in-scope services (`dynamodb = boto3.resource("dynamodb")` → `kv.resource()` only if a kv_table is in scope; leave `sqs = boto3.client("sqs")` if the queue stays on AWS).
- **Rules check:** boto3 calls on out-of-scope components are allowed; boto3 calls on in-scope components are still violations. Rule 7 (adapter signatures) applies only to adapter calls.

**New: `pipeline.move_over(run_id, components)` and `pipeline.move_back(run_id, components)`**: each one is a step:
1. Compute the new scope (add or remove the components).
2. `move_over`: de-lock the new components' functions in `runs/<id>/selfhosted/app/` (or `leaseweb/app/` while the internal name stays).
   `move_back`: **restore** those components' functions from `runs/<id>/original/` (same slicer, replace function bodies) and restore the module-level boto3 client if no other in-scope component of that service remains.
3. Re-render the self-hosted files with the new scope (B).
4. Verify the hybrid (A).
5. Move the data of exactly those components (A), in the right direction.
6. Append a `ScopeStep` to `run.json`; emit events (`stage="approve"`/`"delock"`/`"verify"`/`"data"`, with `step="move_over"`/`"move_back"`).

**`pipeline.roundtrip`** stays as is (everything back). It's the special case `move_back(all)`.

**CLI:** `boomerang approve <run_id> --only orders,receipts`, `boomerang move-over <run_id> order_events`, `boomerang move-back <run_id> orders`.

### 3.2 B: generator, assessment, lock-in, lost report

**`generators.leaseweb.render(pam, secrets, scope=None)`**: generate only what the scope needs.
| Service | Include when |
|---|---|
| `postgres` + `schema.sql` (only in-scope tables) | any `kv_table` in scope |
| `rabbitmq` + `definitions.json` (only in-scope queues, still via `queue_arguments`) | any `queue` in scope |
| `seaweedfs` + `s3.json` (only in-scope buckets) | any `object_store` in scope |
| `api`, `worker`, `backup` (backup only if postgres) | always |

**Hybrid env for `api`/`worker`:** add the AWS variables for out-of-scope components: `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (from `secrets.env`, empty placeholders in production files), `AWS_ENDPOINT_URL` **only in the lab**, plus the out-of-scope name vars (e.g. `ORDER_EVENTS_QUEUE`).
- **Lab detail:** the containers must reach moto on the laptop: `AWS_ENDPOINT_URL=http://host.docker.internal:4566` and, for Linux hosts, `extra_hosts: ["host.docker.internal:host-gateway"]` on `api` and `worker`.
- **`laptop.env`** gets the same AWS variables with `http://127.0.0.1:4566` (for running the tests on the laptop).
- `make_secrets(pam, scope)`: AWS keys only when the scope is hybrid; in production these are the **company's real, scoped AWS credentials**: never generated, never sent to the LLM.

**`assess`**: add one finding per hybrid run: 🔴 *"Hybrid: the self-hosted app needs AWS credentials for `<kept components>`; create a scoped IAM user/role."* Keep everything else per component.

**`lockin.score(pam, scope)`**: lock-in after = only the out-of-scope components keep their AWS weight.

**`lost.build(..., scope)`**: out-of-scope components → `preserved` with detail *"stays on AWS"*.

### 3.3 A: verifier, data mover, lab (us)

**`verifier.leaseweb_env(leaseweb_dir)`**: unchanged (reads B's `laptop.env`, which now includes the AWS variables for kept components).

**`verifier.verify(...)`**: no API change.
- `check_schema` already reads `schema.sql`, so it checks only in-scope tables.
- `reset_data` already reads `schema.sql` and `definitions.json`, so it empties only self-hosted data. It must **not** touch AWS data (it doesn't today).
- **Before verifying a hybrid**, the kept AWS resources must exist in moto: `provisioner_aws.provision(pam)` (idempotent).

**`verifier.run_tests`**: unchanged. The env passed in is the hybrid env (allowlist + local + AWS variables).

**`datamover.move(pam, direction, src_env, dst_env, emit=None, *, components=None)`**: new keyword: copy only these component ids (default: all movable). Counts and checksums per component as today.

**`lab`**: unchanged. Hybrid tests need moto running **and** the self-hosted stack.

### 3.4 D: UI and costs

**"What breaks" tab:** a checkbox per movable component (🟢/🟠 selectable, 🔴 shown but disabled), **"Approve: move selected"**. Default: all checked. When the selection is hybrid, show the IAM warning (the 🔴 hybrid finding).

**New "Scope" panel** (in the Data & round trip tab): each component with where it runs now (AWS / self-hosted), buttons **Move over** / **Move back** per component, and the step history (`run.json` `steps`) with each step's verification and data counts.

**Costs (`cost.estimate(..., scope)`):** hybrid column = AWS costs of kept components + self-hosted costs of moved components + **ongoing egress** for traffic between the company's servers and AWS (not free: the EU Data Act €0 only covers switching) + the ops hours for the self-hosted part.

## 4. Demo scenario (ShopLite)

| Step | Scope after | What to show |
|---|---|---|
| 0 | – | Scan: 1 🟢 (receipts), 🟠 orders + queue, 3 🔴 |
| 1 | `receipts`, `orders` | Hybrid: Postgres + SeaweedFS self-hosted, SQS on AWS. Tests 7/7. Data: 25 orders, 25 receipts moved; 3 messages stay on SQS. 🔴 "needs AWS credentials for order_events". Cost: hybrid column. |
| 2 | + `order_events` | Full self-hosted. Tests 7/7. 3 messages moved. |
| 3 | − `orders` | Orders back on DynamoDB (incl. orders created on step 1–2's stack), rest stays self-hosted. Tests 7/7, counts match. |

## 5. Tests to add

| Role | Test |
|---|---|
| C | `approve` stores the scope; default scope = all; `validate` rejects red/unknown ids; de-lock with scope rewrites only in-scope functions; a mixed function keeps out-of-scope boto3 calls (rules check passes); module-level clients swapped only for in-scope services; `move_back` restores original function bodies exactly; full scope = today's behavior (existing tests unchanged). |
| B | `render` with each single-component scope includes only the needed services; hybrid env has AWS vars only for kept components; `laptop.env` uses `127.0.0.1:4566`, containers use `host.docker.internal:4566`; compose passes `docker compose config -q` for every scope; lock-in and lost report per scope; hybrid red finding present only when hybrid. |
| A | `move(..., components=["orders"])` copies only orders; counts per selected component; `reset_data` on a partial stack leaves AWS untouched; **integration:** hybrid ShopLite (Postgres + SeaweedFS local, SQS on moto) passes the 7 spec tests; moving `orders` back makes the 7 tests pass with DynamoDB again. |
| D | Checkbox state → `approve(scope)`; red components disabled; hybrid cost column sums correctly. |

## 6. Order of work (≈ 2–3 hours with 4 people)

1. **C (30 min):** `Scope` model + `scope.py` + `approve(scope)`; push first, everyone codes against it.
2. **In parallel:** B: `render(scope)` + hybrid env (45 min) · A: `move(components=)` + hybrid integration test with a hand-written partial `laptop.env` (45 min) · D: checkboxes + scope panel on fixtures (60 min) · C: scoped de-lock + rules (60 min).
3. **C (45 min):** `move_over` / `move_back` steps.
4. **All (30 min):** run the demo scenario (§4) end to end.

**Cut line:** if time is short, ship step 1 of the demo only (a hybrid **move over** at approval time) and skip `move_over`/`move_back` after the fact. That already shows "move only what you want."

## 7. Risks and honest limits

| Risk | Mitigation |
|---|---|
| A function mixes moved and kept services, and the AI rewrites the wrong call | Rules check forbids adapter calls for out-of-scope components and boto3 calls for in-scope ones; the tests catch the rest. |
| Containers can't reach moto in the lab | `host.docker.internal` + `extra_hosts` (§3.2). |
| Real hybrid needs AWS credentials on the company's servers | 🔴 finding; credentials are the company's, scoped, never generated or sent to the LLM. |
| Latency and egress between self-hosted and AWS | Shown in the cost model; say it in the pitch. |
| No transactions across the two sides | Say it: each component is consistent on its own; cross-component consistency isn't guaranteed during a hybrid phase. |
| Moving a component back while the app writes to it | Same write freeze as today: stop `api`/`worker` before the data copy (`verifier.stop_service`). |
| Never claim | Zero downtime; "any combination always works"; transactional consistency across platforms. |
