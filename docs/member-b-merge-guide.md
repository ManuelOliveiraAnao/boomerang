# Member B Merge Guide

## Scope

This guide is only for merging Member B.

Source branch:

```text
origin/member-b-after-c1
```

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
| `boomerang/models.py` | C1/C2 Pydantic types: `AppModel`, `Assessment`, `Finding`, `LostItem`, `GeneratedFile` |
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
- Pydantic conversion if C1/C2 types changed.

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
- `Assessment.findings` must use C1/C2 `Finding`;
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

- output must use C1/C2 `LostItem`;
- status meanings.

## Conflict Resolution

| File | Resolution |
|---|---|
| `boomerang/models.py` | Do not replace C1/C2 types |
| `boomerang/assess.py` | Keep B rules and Pydantic return types |
| `boomerang/scanner/static.py` | Keep B scanner, return C1 `AppModel` |
| `boomerang/scanner/ai.py` | Keep B merge logic and C LLM call |
| `boomerang/generators/leaseweb.py` | Keep B generator and C `GeneratedFile` |
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

- C1/C2 public types;
- B Member B behavior;
- A/RabbitMQ shared behavior;
- no duplicate implementation.

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

set DATABASE_URL=postgresql://shoplite:shoplite@127.0.0.1:54329/shoplite
python -m pytest -q tests/sovereign_sdk/test_kv.py
docker rm -f boomerang-member-b-kv
```

Generated Compose:

```bash
docker compose -f runs/<run-id>/leaseweb/docker-compose.yml config -q
```

## Member B Done Criteria

The merge is acceptable only if:

- static scan finds all ShopLite AWS dependencies;
- AI merge retains every static hint;
- traffic lights include green/amber/red;
- KV tests pass against real Postgres;
- both KV Key condition forms work;
- generator returns C1 `GeneratedFile` values;
- RabbitMQ arguments match A's adapter;
- `laptop.env` and `secrets.env` contain real local values;
- secret files are ignored by Git;
- generated Compose is valid.
