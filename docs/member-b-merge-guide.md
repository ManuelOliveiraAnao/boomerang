# Member B Merge Guide

## Scope

This guide is only for merging Member B.

Source branch:

```text
origin/member-b-after-c1
```

Important: this branch contains older copies of A/C shared files and some
older A modules. Treat it as the source of Member B implementation, not as
the authority for A/C shared files. The current integration handoff is
`docs/roles/A-handoff-for-integration.md`.

Member B owns:

- static and AI scanner;
- traffic lights and lock-in;
- PostgreSQL KV adapter;
- Leaseweb generator;
- lost report.

## Critical Contract: Do Not Change

Keep these contracts unchanged:

| Contract | Must remain |
|---|---|
| `boomerang/models.py` | Latest C Pydantic types: `AppModel`, `Assessment`, `Finding`, `LostItem`, `GeneratedFile` |
| Scanner return | `AppModel` |
| Assessment return | `Assessment` |
| Lost report return | `list[LostItem]` |
| Generator return | `list[GeneratedFile]` |
| Traffic lights | Rules decide; AI may explain or raise a light, never lower it |
| KV API | boto3-like `put/get/update/delete/query/scan` signatures |
| Generator secrets | `make_secrets(pam) -> dict[str, str]` |
| Pure generation | `render(pam, secrets)` returns files and does not write/copy the app by default |

Do not replace these contracts with dictionaries, ad-hoc result objects or
plain SQL calls from application code.

## Merge Rules

### Scanner

Keep from Member B:

- AST/Python scanner;
- Terraform/Dockerfile/environment scanner;
- static hint merge after AI scan;
- exclusion of `tests/` and `sovereign_sdk/`;
- static hints that are not discarded by the AI.

May be changed:

- wording of prompts;
- small AI fallback behavior;
- emitter calls;
- Pydantic conversion if C types changed.

Must not be changed:

- every static AWS hint must remain represented in the final PAM;
- static-only residual lock-in must exclude generated SDK/tests.

### Assessment

Keep from Member B:

- deterministic traffic-light rules;
- lock-in weighting;
- AI explanation behavior;
- rule-light protection.

May be changed:

- AI prompt text;
- detailed `what_changes` wording;
- emitter behavior.

Must not be changed:

- AI cannot turn green/amber/red into a lower light;
- `Assessment.findings` must use C's `Finding`;
- output must remain `Assessment`.

### KV Adapter

Keep from Member B:

- `resource()` lazy connection;
- `Table()` lazy connection;
- reconnect behavior;
- Postgres DDL;
- GSI support;
- supported conditions;
- boto3-compatible return shapes and exceptions;
- boto3 and sovereign `Key(...).eq(...)` support.

May be changed:

- connection pool implementation;
- implementation details for SQL;
- retry details after dropped connections.

Must not be changed:

- public method names/signatures;
- `attribute_not_exists` / `attribute_exists` support scope;
- `Decimal` behavior;
- unsupported expressions must raise `UnsupportedFeature`;
- conditional writes must raise
  `ConditionalCheckFailedException`.

If C or A introduces a newer KV implementation, keep B behavior and merge
only compatible internal improvements.

### RabbitMQ

Keep the shared contract with Member A:

```python
from sovereign_sdk.queue import queue_arguments
```

The generator must generate RabbitMQ definitions using
`queue_arguments(name, max_receive_count)`.

Do not manually drift these values:

- main queue: quorum;
- max receive count 3: `x-delivery-limit: 2`;
- DLQ: quorum;
- dead-letter exchange: `""`;
- routing key: `<queue-name>-dlq`.

The queue adapter and generated `rabbitmq/definitions.json` must declare
the same arguments.

### Generator

Keep from Member B:

- pure `render(pam, secrets)`;
- `laptop.env`;
- real generated passwords;
- all documented Leaseweb files;
- secret/executable flags;
- RabbitMQ definitions from A's shared arguments;
- no secret values sent to prompts.

May be changed:

- template presentation;
- extra target files;
- optional `out_dir` compatibility writing.

Must not be changed:

- `GeneratedFile.path` is relative to the Leaseweb run directory;
- `render()` is pure by default;
- pipeline, not the generator, copies `sovereign_sdk/`;
- generator must never copy or edit `app/tests/`;
- secrets and laptop environment must be git-ignored;
- Compose output must pass `docker compose config -q`.

### Lost Report

Keep:

- red -> lost;
- amber -> changed;
- green -> preserved;
- data normalization/object metadata/in-flight message notes -> changed;
- missing findings/data changes -> preserved.

May be changed:

- detail wording;
- grouping or extra evidence.

Must not be changed:

- output must use C's `LostItem`;
- status meanings.

## Conflict Resolution

Before resolving, start from the latest A+C integration and merge Member B
into it. The rehearsed integration found eight shared-file conflicts:

The exact conflict count can differ as A/C change, so treat the file rules
below as authoritative. The latest merge check between A and B also predicts
conflicts in A-owned modules such as `lab.py`, `provisioner_aws.py`,
`verifier.py`, and `datamover.py`; keep the latest A/C side for those files.

| File | Resolution |
|---|---|
| `boomerang/__init__.py` | Keep current A+C version |
| `sovereign_sdk/__init__.py` | Keep current A+C version |
| `boomerang/events.py` | Keep current A+C emitter API |
| `boomerang/cli.py` | Keep current C CLI |
| `boomerang/lab.py` | Keep latest A implementation |
| `boomerang/provisioner_aws.py` | Keep latest A implementation |
| `boomerang/verifier.py` | Keep latest A implementation |
| `boomerang/datamover.py` | Keep latest A implementation |

After merging, verify that no latest-A file changed unintentionally:

```bash
git diff origin/role-a-platform-plan -- \
  sovereign_sdk/queue.py \
  boomerang/verifier.py \
  tests/sovereign_sdk/test_queue.py
```

This should be empty when the latest A branch is already the merge base.

| File | Resolution |
|---|---|
| `boomerang/models.py` | Keep latest C types |
| `boomerang/assess.py` | Keep B rules and Pydantic return types |
| `boomerang/scanner/static.py` | Keep B scanner, return C's `AppModel` |
| `boomerang/scanner/ai.py` | Keep B merge logic and C LLM call |
| `boomerang/generators/leaseweb.py` | Keep B generator and C `GeneratedFile`; keep C pipeline ownership for file writing |
| `boomerang/generators/templates/**` | Keep B templates |
| `boomerang/lost.py` | Keep B mapping and C `LostItem` |
| `boomerang/lockin.py` | Keep B weights and score |
| `sovereign_sdk/kv.py` | Keep B implementation |
| `fixtures/assessment.aws.json` | Keep B fixture |
| `fixtures/verification.json` | Keep B fixture |
| `fixtures/data-moves.json` | Keep B fixture |
| `fixtures/lost.json` | Keep B fixture |
| `fixtures/events.jsonl` | Keep B fixture |

For shared files, prefer:

- C public types;
- B Member B behavior;
- A/RabbitMQ shared behavior;
- no duplicate implementation.

## Tests That Become Stale

After merging B successfully, remove or update tests that still assert that
Member B code is a C stub. The rehearsed integration identified these:

- nine `test_stubs.py` entries for scanner, assessment, lock-in, generator,
  lost report and KV;
- `test_adapter_classes_are_stubbed_too` for `KV.Table`;
- `test_unbuilt_command_exits_3_and_names_the_owner` for the scanner;
- the delock test event assertion that expects
  `"scan_static is not built yet"`.

Do not replace these by weakening A or C tests. Remove them because the
Member B implementation now satisfies them.

## Minimum Validation

```bash
python -m compileall -q boomerang sovereign_sdk tests

python -m pytest -q \
  tests/test_scanner.py \
  tests/test_generator.py \
  tests/test_exhaustive_member_b.py \
  tests/test_models.py
```

Real Postgres:

```bash
docker run --name boomerang-member-b-kv \
  -e POSTGRES_DB=shoplite \
  -e POSTGRES_USER=shoplite \
  -e POSTGRES_PASSWORD=shoplite \
  -p 54329:5432 \
  -d postgres:16-alpine
```

PowerShell:

```powershell
$env:DATABASE_URL='postgresql://shoplite:shoplite@127.0.0.1:54329/shoplite'
python -m pytest -q tests/sovereign_sdk/test_kv.py
docker rm -f boomerang-member-b-kv
Remove-Item Env:DATABASE_URL
```

Generated Compose:

```bash
docker compose -f runs/<run-id>/leaseweb/docker-compose.yml config -q
```

## Known Integration Status

The latest A integration handoff reported:

- A alone: 57/57 integration-style tests passing;
- the rehearsed A+C+B merge: 441 unit tests passing after stale stub tests
  are updated, with 12 stale stub assertions removed;
- integration: 58 passed, 1 skipped in the rehearsed environment;
- storage/S3 integration needs explicit S3 test credentials;
- full verification of a real generated ShopLite stack, real data mover,
  and full round trip remain follow-up work.

Run Member B tests separately from A/C integration tests while C and A are
still changing. Member B work does not become invalid if A/C integration
tests are temporarily red.

## Member B Done Criteria

The merge is acceptable only if:

- static scan finds all ShopLite AWS dependencies;
- AI merge retains every static hint;
- traffic lights include green/amber/red;
- KV tests pass against real Postgres;
- both KV Key condition forms work;
- generator returns C's `GeneratedFile` values;
- RabbitMQ arguments match A's adapter;
- `laptop.env` and `secrets.env` contain real local values;
- secret files are ignored by Git;
- generated Compose is valid.
