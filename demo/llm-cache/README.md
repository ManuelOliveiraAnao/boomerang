# demo/llm-cache: the recorded AI answers (C7)

Every AI call goes through `llm.structured()`, which can store the answer under the sha256 of what decided it
(provider, model, redacted prompt, schema). This folder holds the answers of **one** run, the golden run, so the
demo can replay it with Ollama stopped and no network (`BOOMERANG_LLM_MODE=replay`). Spec: docs/15 §8.

Each `<sha256>.json` holds the redacted prompt and the answer, never a secret (checked below). `manifest.json`
records what the keys depend on: every `prompts/*.md`, the LLM-facing schemas, the provider and the model.

## 1. Record the golden run (with A, after CP3)

Start from an empty folder, so the cache holds only what this run needs (no stale entries from the day):

```powershell
Remove-Item demo/llm-cache/*.json
$env:PYTHONUTF8 = "1"
$env:BOOMERANG_LLM_MODE = "record"
$env:BOOMERANG_CACHE_DIR = "demo/llm-cache"
boomerang migrate sample-app/aws --yes --run-id golden --force
boomerang roundtrip golden
```

## 2. Check and freeze

```powershell
boomerang cache freeze --run golden
```

`freeze` first runs `boomerang cache check --run golden`, and writes `manifest.json` only when it is green:

- every entry parses, its file name is its key, and its stored answer still validates against its schema;
- no secret in any entry or in any file of `runs/golden/` (`secrets.env` holds the passwords by design: the check
  makes sure git ignores it);
- every AI call of the run has an entry (a missing one was made in `live` mode) and every entry was used
  (an unused one is stale: `boomerang cache check --run golden --prune` deletes it).

Then confirm by eye that the passwords stay out of git:

```powershell
git status --ignored runs/golden
```

## 3. Offline test

Quit Ollama (tray icon → Quit) and turn Wi-Fi off. Docker images, including the built `api` image, must already
be local. Then:

```powershell
$env:BOOMERANG_LLM_MODE = "replay"
$env:BOOMERANG_CACHE_DIR = "demo/llm-cache"
boomerang migrate sample-app/aws --yes
```

Same result as the golden run, and no `CacheMiss`. A `CacheMiss` names the call that has no answer.

## 4. Commit

`demo/llm-cache/` (this folder, with `manifest.json`) and `runs/golden/` (A owns that commit; `.gitignore` keeps
`runs/golden/` and drops `secrets.env`).

## 5. Prompts are frozen from here

Any edit to `prompts/*.md` (including `adapter_interface.md`) or to an LLM-facing schema in `boomerang/models.py`
changes the cache keys, and replay misses. `tests/test_democache.py` fails when they no longer match
`manifest.json`. If something must change: re-record (steps 1–2).

Replay also needs the same provider and model as the recording: a cache recorded with
`ollama · qwen2.5-coder:7b` doesn't answer `BOOMERANG_LLM_PROVIDER=anthropic`.
