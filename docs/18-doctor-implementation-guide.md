# 18 · Boomerang Doctor: implementation guide

**Goal:** after a migration, something breaks on the self-hosted stack. Boomerang Doctor reads the stack's signals,
names the root cause and what it breaks for users, explains it in plain words, and offers fixes from a fixed list.
A human approves each one. One of those fixes is **"move this service back to AWS"**: incident response where the
rollback is a boomerang.

**Branch:** `doctor` (from `D` @ `6b13e72`, which contains all of `integration-abc`). Each piece below is owned by one
person and touches its own files, so the three can be built in parallel. D builds the Doctor tab against the fixture
in §6 at the same time.

| Piece | Owner | What | New files |
|---|---|---|---|
| **1 · Signals and actions** | **A (Manuel)** | Read the stack's state; run the allowlisted actions; break a service for the demo | `boomerang/doctor/signals.py`, `boomerang/doctor/actions.py` |
| **2 · Diagnosis rules** | **B (Francisco)** | Turn signals into a root cause, the affected component and user features, and ranked fixes | `boomerang/doctor/rules.py` |
| **3 · Contract, AI explanation, CLI** | **C (Joao)** | Models, the orchestrator, the AI's plain-words explanation, `boomerang doctor …` commands | `boomerang/models.py` (additions), `boomerang/doctor/__init__.py`, `prompts/doctor.md`, `boomerang/cli.py` |
| (UI) | D (Bernardo) | Doctor tab: incident card, evidence, fixes with approval | `ui/components/doctor.py`, `fixtures/doctor-demo.json` |

---

## 1. Rules that apply to every piece

These follow docs/13 (security) and the rule the traffic lights already use.

1. **Rules decide, the AI explains.** The root cause, the affected component and the candidate fixes come from code
   (piece 2). The AI only writes the explanation, and it may mark an incident as more severe, never less.
2. **The AI never produces or runs commands** (docs/13, S2). Fixes are a fixed allowlist of Python functions with
   fixed arguments (piece 1). The AI can't add an action or change its arguments.
3. **A human approves every fix.** Nothing runs on its own. The CLI needs the action id; the UI needs a click.
4. **Secrets never reach the AI** (S1). Log lines go through `llm.structured()`, which redacts; only the last 40 lines
   per service are sent.
5. **Lab only for breaking things.** `break` refuses unless the stack is the local verify stack (`-p bmr-verify`).

---

## 2. The contract (piece 3 writes it first, in the first 15 minutes)

Added to `boomerang/models.py`. Everyone codes against these; push them before anything else.

```python
Stage = Literal[..., "doctor"]                     # add "doctor" to the existing Stage literal

class Signal(BaseModel):                           # one observation of the stack (piece 1 produces these)
    source: Literal["container", "logs", "health", "probe", "queue"]
    service: str                                   # compose service: postgres | rabbitmq | seaweedfs | api | worker
    ok: bool
    detail: str = ""                               # "exited (1) 2 min ago", "GET /health 500", "connection refused"
    lines: list[str] = []                          # evidence: log lines, newest last (≤ 40)
    data: dict[str, Any] = {}                      # e.g. {"state": "exited", "exit_code": 1, "restarts": 3, "depth": 120}

class Action(BaseModel):                           # one fix from the allowlist (piece 1 runs it)
    id: Literal["restart_service", "reverify", "move_back", "start_service"]
    target: str                                    # service name, or component id for move_back
    label: str                                     # "Restart postgres", "Move orders back to AWS"
    risk: Literal["low", "medium", "high"]         # restart = low, reverify = low, move_back = high

class Diagnosis(BaseModel):                        # piece 2 produces this from signals
    rule: str                                      # "db_down", "queue_down", ... or "unknown"
    root_cause: str                                # one sentence, from the rule
    service: str | None                            # the failing compose service
    component_id: str | None                       # the PAM component behind it (orders, order_events, receipts)
    impact: list[str]                              # user-facing: ["Checkout fails", "Order history unavailable"]
    severity: Literal["degraded", "down"]
    evidence: list[Signal]                         # only the signals that support the rule
    actions: list[Action]                          # ranked, best first

class Incident(BaseModel):                         # piece 3 writes it to runs/<id>/doctor.json
    run_id: str
    at: float
    diagnosis: Diagnosis
    explanation: str = ""                          # the AI's plain words (empty if the model is unavailable)
    severity: Literal["degraded", "down"]          # = diagnosis.severity, or raised by the AI
    llm: str = ""                                  # config.Settings.llm_badge
    applied: list[dict[str, Any]] = []             # [{"action": Action, "by": "ui", "at": ts, "ok": bool, "detail": str}]
```

**Mapping used by all pieces** (compose service ↔ PAM component kind):

| Compose service | Component kind | ShopLite component |
|---|---|---|
| `postgres` | `kv_table` | `orders` |
| `rabbitmq` | `queue` | `order_events` |
| `seaweedfs` | `object_store` | `receipts` |
| `api`, `worker` | (the app) | none |

---

## 3. Piece 1 · Signals and actions: A (Manuel)

You own the platform side, and `boomerang/verifier.py` already does most of this. Reuse `_compose()`, `PROJECT`,
`HOST_PORTS`, `check_health()`, `start_service()`, `stop_service()` and `laptop_env()`.

### 3.1 `boomerang/doctor/signals.py`

```python
def collect(leaseweb_dir: Path, run: Run = subprocess.run, health_url: str = verifier.HEALTH_URL) -> list[Signal]:
```
Gather, in this order (each one independent; a failing probe becomes a `Signal(ok=False)`, never an exception):

1. **Containers:** `docker compose ps --all --format json` → one `container` signal per service with `state`,
   `exit_code`, `health` and restart count in `data`. Not running = `ok=False`.
2. **Logs:** `docker compose logs --tail 40 --no-color <service>` for `api`, `worker` and every service that isn't
   running → one `logs` signal each; `lines` = the tail; `ok=False` when a line matches
   `error|exception|traceback|refused|timed out|fatal` (case-insensitive).
3. **Health:** `verifier.check_health(health_url, timeout_s=3)` → one `health` signal for `api`.
4. **Probes:** a 2-second TCP connect to each `HOST_PORTS` entry → one `probe` signal per service
   (`detail="connection refused"` etc.).
5. **Queue depth:** RabbitMQ management API (`http://127.0.0.1:15672/api/queues`, credentials from `laptop_env()`) →
   one `queue` signal per queue with `data={"depth": n, "consumers": c}`. `ok=False` when there are no consumers and
   depth > 0. Skip silently if the management port isn't exposed.

### 3.2 `boomerang/doctor/actions.py`

```python
ALLOWED = {"restart_service", "start_service", "reverify", "move_back"}
SERVICES = {"postgres", "rabbitmq", "seaweedfs", "api", "worker"}

def apply(run_id: str, leaseweb_dir: Path, action: Action, *, by: str, emit: Emitter | None = None,
          run: Run = subprocess.run) -> dict[str, Any]:     # {"ok": bool, "detail": str}
def break_service(leaseweb_dir: Path, service: str, run: Run = subprocess.run) -> None:   # demo only
```
- `restart_service` → `docker compose restart <target>`; `start_service` → `verifier.start_service()`.
  Both reject a `target` that isn't in `SERVICES`.
- `reverify` → `verifier.verify(leaseweb_dir, emit=emit)`; `ok` = `Verification.ok`.
- `move_back` → `pipeline.move_back(run_id, [target], by=by)` (C's existing step); `ok` = the run passed.
- Anything not in `ALLOWED` raises `ValueError`. No `shell=True`, no strings from the AI.
- `break_service` = `verifier.stop_service()`. It refuses unless the compose project is `bmr-verify`.

### 3.3 Tests (`tests/test_doctor_signals.py`)
Use the `FakeDocker` pattern from `tests/test_verifier.py`: canned `ps` JSON (one service exited), canned logs and a
dead health URL → the right `ok` flags; `apply()` rejects an unknown action and an unknown service; `restart_service`
sends exactly `docker compose … restart postgres`.

**Done when:** `collect()` on a stack with Postgres stopped returns a failing `container` signal for `postgres`,
failing `probe` and `health` signals, and `api` logs that contain "connection refused".

> **Status: done** on branch `doctor-a` (20 Nov, 14:50). `boomerang/doctor/signals.py`, `boomerang/doctor/actions.py`,
> `tests/test_doctor_signals.py` (20 tests). Differences from the plan above, for pieces 2 and 3:
> - `actions.apply(..., ready_timeout_s=60)`: after a restart or start, `ok` means the container is **running and
>   healthy**, not just started (real Docker shows "health: starting" for a few seconds). `detail` says
>   "postgres healthy after 2 s", or "not healthy after 60 s".
> - `signals.collect(leaseweb_dir, run, health_url, *, connect=, fetch=)`: sockets and HTTP are injectable for tests.
>   A service compose doesn't list at all is a failing `container` signal with `detail="not created"`; no Docker at
>   all is one signal for service `"stack"`.
> - Queue signals skip dead-letter queues (messages waiting there are expected) and are left out silently when the
>   management API isn't reachable.
> - `break_service()` refuses a remote `DOCKER_HOST` (ssh:// or tcp://), not only another compose project.
> - The §2 contract is in `models.py` on this branch, field for field, so piece 1 could be built first. C can keep it.
>
> **Checked on real Docker** (a two-container `bmr-verify` stack): healthy → `break_service(postgres)` → container
> "Exited (0)", port 5432 "connection refused", Postgres's "database system is shut down" in the logs →
> `apply(restart_service postgres)` → "postgres healthy after 2 s". The full ShopLite stack wasn't booted here.

---

## 4. Piece 2 · Diagnosis rules: B (Francisco)

You own the rules engine, like `assess.py`: deterministic, table-driven, unit-tested.

### 4.1 `boomerang/doctor/rules.py`

```python
def diagnose(signals: list[Signal], pam: AppModel, scope: Scope | None) -> Diagnosis:
```
Evaluate the rules in order; the **first match wins**. Each rule names its evidence (the signals it used).

| Rule | Matches when | Root cause (template) | Severity | Actions, best first |
|---|---|---|---|---|
| `db_down` | `postgres` container not running **or** probe refused, and api logs or health fail | "The database (Postgres) is down, so the app can't read or write orders." | down | restart postgres · move `orders` back to AWS · reverify |
| `queue_down` | `rabbitmq` not running or probe refused | "The message queue (RabbitMQ) is down; new orders aren't being processed." | degraded | restart rabbitmq · move `order_events` back · reverify |
| `storage_down` | `seaweedfs` not running or probe refused | "Object storage is down; receipts can't be saved or read." | degraded | restart seaweedfs · move `receipts` back · reverify |
| `worker_stalled` | worker not running, or a queue with depth > 0 and 0 consumers | "The worker has stopped; {depth} messages are waiting." | degraded | start worker · reverify |
| `app_crash` | api not running or restarting, and its logs contain a traceback | "The app is crashing on start: {last exception line}." | down | restart api · reverify |
| `config_error` | api logs contain `KeyError`/`environment variable` | "A setting is missing: {name}." | down | reverify (and say which variable) |
| `unknown` | nothing above matches but something failed | "Something is failing; the evidence is below." | degraded | reverify |

- **Hybrid runs:** a component that stays on AWS (`scope` doesn't include it) can't be the cause on the self-hosted
  side. Skip its rule and don't offer `move_back` for it.
- **Impact:** map the component to user-facing features with the PAM: take `component.usages` (file and symbol) and
  the app's entrypoints, and name the routes that call it. For ShopLite that gives `orders` → "Checkout" and "Order
  history"; `receipts` → "Receipts"; `order_events` → "Order confirmation". Fall back to the component id.
- `move_back` is offered only when the component is self-hosted in the current scope **and** it isn't the last
  self-hosted component (moving the last one back is the round trip).

### 4.2 Tests (`tests/test_doctor_rules.py`)
One synthetic signal set per rule → the expected `rule`, `component_id`, `severity` and first action; the hybrid
skip; `move_back` never offered for the last self-hosted component; the order of rules (db down beats app crash).

**Done when:** every rule has a test, and `diagnose()` on piece 1's "Postgres stopped" signals returns `db_down`,
`component_id="orders"`, impact containing "Checkout", and `restart_service postgres` as the first action.

---

## 5. Piece 3 · Contract, AI explanation, CLI: C (Joao)

You own the contract, the LLM layer and the CLI.

### 5.1 Contract
Write §2 into `models.py` first and push it; pieces 1 and 2 and the UI start from it. Add `"doctor"` to `Stage`
(the UI's activity feed shows doctor events as they happen).

### 5.2 `boomerang/doctor/__init__.py`: the orchestrator

```python
def run(run_id: str, *, emit: Emitter | None = None) -> Incident | None:      # None = healthy
def fix(run_id: str, action_id: str, target: str, *, by: str, emit: Emitter | None = None) -> Incident:
```
- `run`: `signals.collect()` → if everything is ok, emit `success` "stack healthy" and return `None`. Otherwise
  `rules.diagnose()` → `explain()` → write `runs/<id>/doctor.json` → emit one `error` (down) or `warning` (degraded)
  event with the root cause.
- `fix`: load `doctor.json`, check that the action is one of the diagnosis's actions (same id and target), call
  `actions.apply()`, append to `applied`, rerun `collect()`, and emit `success` if the stack is healthy again.
- Precondition for both: the run has a self-hosted stack (status `passed`, or a scope).

### 5.3 The AI explanation: `prompts/doctor.md`
- `explain(diagnosis) -> str` calls `llm.structured(system=…, user=…, schema=DoctorExplanation, label="doctor",
  stage="doctor")` with `DoctorExplanation(explanation: str, raise_severity: bool)`.
- The user prompt: the rule's root cause, the impact, the evidence lines (≤ 40 per service, already capped by piece 1)
  and the candidate actions. Ask for three short sentences: what happened, what users see, why the first fix should
  work.
- The AI may set `raise_severity` (degraded → down); it can't change the cause, the actions or their order.
- If the model is unavailable (`LLMUnavailable`), keep `explanation=""`. The incident is still complete, because the
  rules carry it.
- The cache works as for the other prompts, so a rehearsed demo replays offline.

### 5.4 CLI (`boomerang/cli.py`)
```
boomerang doctor <run_id>                              # diagnose; exit 0 healthy, 1 incident found, 3 error
boomerang doctor fix <run_id> <action_id> <target> --by cli|ui
boomerang doctor break <run_id> <service>              # demo only, lab stack only
```
Print the incident as a short table (root cause, impact, evidence, numbered actions).

### 5.5 Tests (`tests/test_doctor.py`)
`run()` with faked `collect()` and a faked `structured()` writes `doctor.json` and emits one event; `fix()` refuses an
action that isn't in the diagnosis; the AI can raise severity but its other fields are ignored; `LLMUnavailable` →
an incident without explanation; CLI exit codes.

**Done when:** `boomerang doctor break <id> postgres` then `boomerang doctor <id>` prints `db_down` with an
explanation, and `boomerang doctor fix <id> restart_service postgres --by cli` brings the health check back.

---

## 6. What the UI reads (D builds against this now)

- `runs/<id>/doctor.json` = `Incident` (above). D starts from `fixtures/doctor-demo.json`, a hand-written `db_down`
  incident with ShopLite evidence, and switches to the real file when piece 3 lands.
- Doctor events arrive in `events.jsonl` with `stage="doctor"`, so the activity feed shows them live.
- Buttons call `ui.runner.start("doctor-fix", run_id, …)`, which maps to `boomerang doctor fix <id> <action> <target>
  --by ui`, and `"doctor"` for a fresh diagnosis. `move_back` asks for a second confirmation (risk high).

---

## 7. Order of work (~75 minutes)

| When | Who | What |
|---|---|---|
| 0–15 min | C | Contract (§2) pushed. Everyone pulls `doctor`. |
| 0–15 min | D | `fixtures/doctor-demo.json` from the contract; Doctor tab layout |
| 15–55 min | A | `signals.collect()`, `actions.apply()`, `break_service()` + tests |
| 15–55 min | B | `rules.diagnose()` + one test per rule |
| 15–55 min | C | orchestrator, `prompts/doctor.md`, CLI + tests |
| 15–55 min | D | Doctor tab on the fixture; runner commands |
| 55–75 min | all | On a laptop with Docker: break Postgres → diagnose → restart → healthy; then break again → move `orders` back. Record it. |

**Cut line:** if time runs short, ship `db_down` and `queue_down` only, with **restart** and **move back**. The demo
needs one rule and two buttons.

## 8. Demo (30 seconds)

1. The migrated ShopLite is running on the self-hosted stack; the dashboard is green.
2. `boomerang doctor break <id> postgres` (or the hidden button in the Doctor tab).
3. Checkout fails. The Doctor tab: **"Database down · Checkout and order history broken"**, the evidence (container
   exited, port refused, three log lines), the AI's three sentences, and the fixes.
4. Click **Restart postgres** → healthy again. Or click **Move orders back to AWS** → `orders` runs on AWS, the rest
   stays self-hosted, and the app works: *"when something breaks after the move, Boomerang can boomerang that one
   service back."*

## 9. Risks and limits

| Risk | Mitigation |
|---|---|
| No Docker on the demo laptop | Record the run (incident + fix) and replay it; the fixture covers the UI |
| Model slow or offline | The rules carry the diagnosis; the explanation is optional and cached after rehearsal |
| Restart doesn't fix it (data corrupted, disk full) | The incident stays open, and reverify shows what still fails; never claim auto-healing |
| `move_back` during an incident copies the data while the app is broken | Same write freeze as today (`stop_service` api/worker) before the copy |
| **Never claim** | "Self-healing", "fixes any incident", "no human needed" |
