"""Build fixtures/run-demo/ from A+B+C code (run from an integrated tree, -I with sys.path)."""
import difflib, json, shutil, sys, time
from pathlib import Path

tree, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(tree))
from boomerang import models as M
from boomerang.scanner.static import scan_static
from boomerang.assess import assess
from boomerang.lockin import score
from boomerang.lost import build as build_lost
from boomerang.generators.leaseweb import render, make_secrets

RUN = "demo"
if out.exists(): shutil.rmtree(out)
out.mkdir(parents=True)
app = tree / "sample-app/aws"
pam = scan_static(app); pam_d = pam.model_dump() if hasattr(pam, "model_dump") else pam
pam_d["app_name"] = "ShopLite"
a = assess(pam_d); a_d = a.model_dump() if hasattr(a, "model_dump") else a
(out / "pam.json").write_text(json.dumps(pam_d, indent=2))
(out / "assessment.json").write_text(json.dumps(a_d, indent=2))
red = {f["component_id"] for f in a_d["findings"] if f["light"] == "red"}
before = score(pam_d); after = score({"components": [c for c in pam_d["components"] if c["id"] in red]})

# Leaseweb files from B's real generator
lw = out / "leaseweb"
gen_pam = json.loads((tree / "fixtures/pam.aws.json").read_text())  # scanner PAM lacks partition keys (B bug)
files = render(gen_pam, make_secrets(gen_pam), out_dir=lw)
(lw / "app").mkdir(exist_ok=True)

# De-lock diffs (illustrative; see fixtures/run-demo/README.md)
diffs = out / "diffs"; diffs.mkdir()
def write_diff(name, path, old, new):
    d = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))
    (diffs / name).write_text(d); return f"diffs/{name}", sum(1 for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
src = {p: (app / p).read_text() for p in ("app/db.py", "app/storage.py", "app/queue.py")}
recs = []
def rec(file, fn, kind, analysis, old, new, attempt=0, unsupported=()):
    dp, n = write_diff(f"{file.replace('/', '_')}__{fn}.diff", file, old, new)
    recs.append(M.RewriteRecord(file=file, function=fn, kind=kind, attempt=attempt, accepted=True,
                                changed_lines=n, analysis=analysis, unsupported=list(unsupported), diff_path=dp))
rec("app/storage.py", "module", "config", "S3 → S3-compatible object storage: client swap only (endpoint + keys from env).",
    "import boto3\n\ns3 = boto3.client(\"s3\")\n", "import boto3\nfrom sovereign_sdk import storage\n\ns3 = storage.s3_client()\n")
rec("app/queue.py", "module", "config", "SQS client swapped for the queue adapter; every call maps 1:1, checked by code.",
    "import boto3\n\nsqs = boto3.client(\"sqs\")\n", "import boto3\nfrom sovereign_sdk import queue\n\nsqs = queue.client()\n")
rec("app/db.py", "module", "config", "DynamoDB resource swapped for the kv adapter (Postgres).",
    "dynamodb = boto3.resource(\"dynamodb\")\n", "from sovereign_sdk import kv\n\ndynamodb = kv.resource()\n")
for fn in ("put_order", "get_order", "set_status", "publish_order_created", "receive_one", "ack"):
    f = "app/queue.py" if fn in ("publish_order_created", "receive_one", "ack") else "app/db.py"
    recs.append(M.RewriteRecord(file=f, function=fn, kind="config", accepted=True, changed_lines=0,
                                analysis="1:1 mapping, checked by code"))
old_q = ("def list_orders_by_customer(customer_id: str) -> list[dict]:\n    resp = _table().query(\n"
         "        IndexName=\"customer_id-index\",\n        KeyConditionExpression=\"customer_id = :c\",\n"
         "        ExpressionAttributeValues={\":c\": customer_id},\n    )\n    return [_from_ddb(i) for i in resp.get(\"Items\", [])]\n")
new_q = ("def list_orders_by_customer(customer_id: str) -> list[dict]:\n    resp = _table().query(\n"
         "        IndexName=\"customer_id-index\",\n        KeyConditionExpression=Key(\"customer_id\").eq(customer_id),\n"
         "    )\n    return [_from_ddb(i) for i in resp.get(\"Items\", [])]\n")
rec("app/db.py", "list_orders_by_customer", "ai",
    "Queries the customer_id GSI with a string KeyConditionExpression. The kv adapter only accepts "
    "Key(x).eq(v), so the expression and ExpressionAttributeValues become Key(\"customer_id\").eq(customer_id). "
    "Same index, same return shape.", old_q, new_q)
old_f = "def _from_ddb(value):\n    if isinstance(value, Decimal):\n        return int(value)\n"
new_f = ("def _from_ddb(value):\n    if isinstance(value, Decimal):\n"
         "        return int(value) if value == value.to_integral_value() else float(value)\n")
rec("app/db.py", "_from_ddb", "fix",
    "test_order_total_is_number failed: totals with cents were truncated to int after the move because Postgres "
    "NUMERIC comes back as Decimal('12.50'). Keep floats when the value isn't integral.", old_f, new_f, attempt=1)
(out / "delock.json").write_text(json.dumps([r.model_dump() for r in recs], indent=2))

def tr(platform, attempt, passed, failed=0, failures=()):
    return M.TestReport(attempt=attempt, platform=platform, passed=passed, failed=failed, errors=0,
                        total=passed + failed, duration_s=4.2, failures=list(failures))
def checks(tests_ok, detail):
    return [M.Check(name="config", ok=True, detail="docker compose config OK", duration_s=0.8),
            M.Check(name="boot", ok=True, detail="postgres, rabbitmq, seaweedfs, api, worker healthy", duration_s=41.3),
            M.Check(name="schema", ok=True, detail="orders: table + customer_id-index", duration_s=0.3),
            M.Check(name="tests", ok=tests_ok, detail=detail, duration_s=4.2),
            M.Check(name="health", ok=tests_ok, detail="GET /health 200" if tests_ok else "skipped", duration_s=0.1)]
fail = M.TestFailure(test="tests/test_orders.py::test_order_total_is_number",
                     message="assert 12 == 12.5", traceback="E   assert 12 == 12.5\nE    +  where 12 = resp.json()['total']")
verifs = [M.Verification(checks=checks(False, "6/7 passed"), tests=tr("leaseweb", 1, 6, 1, [fail])),
          M.Verification(checks=checks(True, "7/7 passed"), tests=tr("leaseweb", 2, 7))]
def dc(cid, kind, s, t, ok=True, notes=()):
    return M.DataCount(component_id=cid, kind=kind, source_count=s, target_count=t, checksum_match=ok, notes=list(notes))
moves = [M.DataMove(direction="aws->leaseweb", counts=[dc("orders", "kv_table", 25, 25), dc("receipts", "object_store", 25, 25),
                                                     dc("order_events", "queue", 3, 3, None, ["pending messages copied; order not checked"])]),
         M.DataMove(direction="leaseweb->aws", counts=[dc("orders", "kv_table", 26, 26), dc("receipts", "object_store", 25, 25),
                                                     dc("order_events", "queue", 4, 4, None)])]
lost = [x if isinstance(x, M.LostItem) else M.LostItem(**x) for x in build_lost(pam_d, a_d)]
now = time.time() - 600
summary = M.RunSummary(
    run_id=RUN, source_dir="sample-app/aws", status="passed", started_at=now, finished_at=now + 412,
    lock_in_before=before, lock_in_after=after, assessment=M.Assessment(**a_d), verifications=verifs,
    data_moves=moves, lost=lost, out_dir=f"runs/{RUN}", baseline=tr("aws", 0, 7), rewrites=recs,
    tests_unchanged=True, llm="ollama · qwen2.5-coder:7b · local", roundtrip="passed",
    roundtrip_checks=[M.Check(name="orders count", ok=True, detail="26 on Leaseweb → 26 on AWS"),
                      M.Check(name="receipts count", ok=True, detail="25 → 25"),
                      M.Check(name="new order exists on AWS", ok=True, detail="order created on Leaseweb found after copy back")],
    roundtrip_tests=tr("aws", 1, 7), fixtures_used=["ui-demo"])
(out / "run.json").write_text(summary.model_dump_json(indent=2))
(out / "approval.json").write_text(M.Approval(approved_by="ui", ts=now + 20, plan_sha256="demo").model_dump_json(indent=2))

ev = []
def e(dt_, stage, level, msg, **data): ev.append(M.Event(ts=now + dt_, run_id=RUN, stage=stage, level=level, message=msg, data=data or None))
e(0, "scan", "info", "scanning sample-app/aws (code, Terraform, Dockerfile, env)")
e(3, "scan", "success", f"{len(pam_d['components'])} components found")
e(4, "assess", "success", "traffic lights ready", findings=len(a_d["findings"]))
e(5, "approve", "info", "awaiting approval")
e(20, "approve", "success", "plan approved (ui)")
e(22, "delock", "info", "client swaps: storage, queue, db")
e(40, "delock", "success", "app/db.py:list_orders_by_customer rewritten (ai)")
e(41, "delock", "success", "8 functions mapped 1:1, checked by code")
e(45, "generate", "success", f"{len(files)} Leaseweb files written")
e(50, "verify", "info", "booting the Leaseweb stack")
e(95, "verify", "warning", "tests: 6/7 passed (attempt 1)")
e(110, "fix", "success", "app/db.py:_from_ddb fixed (attempt 1)")
e(160, "verify", "success", "tests: 7/7 passed (attempt 2) · health OK")
e(170, "data", "success", "aws->leaseweb: orders 25/25, receipts 25/25, messages 3/3")
e(260, "rollback", "info", "write freeze; new order created on Leaseweb")
e(330, "data", "success", "leaseweb->aws: orders 26/26, receipts 25/25, messages 4/4")
e(400, "rollback", "success", "original tests on AWS: 7/7 · round trip passed")
e(412, "report", "success", "run passed")
(out / "events.jsonl").write_text("".join(x.model_dump_json() + "\n" for x in ev))
print("files", len(files), "rewrites", len(recs), "lockin", before, after)
# Never ship generated secrets in the repo, even demo ones.
for name in ("secrets.env", "laptop.env"):
    (lw / name).unlink(missing_ok=True)
for p in lw.rglob("__pycache__"):
    shutil.rmtree(p)
import re
for p in lw.rglob("*.json"):
    t = p.read_text()
    t = re.sub(r'("(?:password|secretKey|accessKey|password_hash)"\s*:\s*")[^"]*(")', r'\1<redacted-in-fixture>\2', t)
    p.write_text(t)
