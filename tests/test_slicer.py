"""Slicer (docs/15 §4.2): innermost function, decorators outside, CRLF/tabs kept, bottom-up stitching."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from boomerang import slicer
from boomerang.events import Emitter
from boomerang.models import AppModel, CodeRef, Component, Event

MODULE = '''\
"""Orders."""
import os

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["ORDERS_TABLE"])
UNUSED = 1


def helper(x):
    return x


@decorate("a")
@other
def get_order(order_id):
    resp = table.get_item(Key={"order_id": order_id})
    return helper(resp.get("Item"))


def outer():
    def inner():
        return table.scan()
    return inner()


class Repo:
    def put(self, item):
        sql = """
SELECT *
    FROM t"""
        table.put_item(Item=item)
        return sql
'''


def _write(tmp_path: Path, text: str, rel: str = "app/db.py", newline: str = "\n") -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def _line(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), 1) if needle in ln)


def _pam(*usages: CodeRef) -> AppModel:
    comp = Component(id="orders", kind="kv_table", provider="aws.dynamodb", resource_name="orders",
                     usages=list(usages))
    return AppModel(app_name="t", platform="aws", entrypoints=[], components=[comp], files=[], env=[])


def _ref(file: str, line: int, symbol: str | None = None) -> CodeRef:
    return CodeRef(file=file, line=line, symbol=symbol, operation="op", snippet="")


# ---------- load / split ----------

def test_split_lines_matches_the_tokenizer_not_str_splitlines():
    text = "a = '\x0c'\r\nb = 1\rc = 2\nd"
    assert slicer.split_lines(text) == ["a = '\x0c'\r\n", "b = 1\r", "c = 2\n", "d"]
    assert "".join(slicer.split_lines(text)) == text


def test_load_module_keeps_crlf(tmp_path):
    path = _write(tmp_path, MODULE, newline="\r\n")
    text, tree, newline = slicer.load_module(path)
    assert newline == "\r\n" and "\r\n" in text
    assert isinstance(tree, ast.Module)


# ---------- functions_with_usages ----------

def test_innermost_function_wins(tmp_path):
    _write(tmp_path, MODULE)
    out = slicer.functions_with_usages(tmp_path, _pam(_ref("app/db.py", _line(MODULE, "table.scan()"))))
    [(sl, comps)] = out["app/db.py"]
    assert sl.qualname == "outer.inner" and sl.name == "inner"
    assert [c.id for c in comps] == ["orders"]


def test_decorators_stay_outside_the_slice(tmp_path):
    _write(tmp_path, MODULE)
    out = slicer.functions_with_usages(tmp_path, _pam(_ref("app/db.py", _line(MODULE, "table.get_item"))))
    [(sl, _)] = out["app/db.py"]
    assert sl.start == _line(MODULE, "def get_order")
    assert sl.source.startswith("def get_order(order_id):")
    assert "@decorate" not in sl.source


def test_module_names_and_context(tmp_path):
    _write(tmp_path, MODULE)
    out = slicer.functions_with_usages(tmp_path, _pam(_ref("app/db.py", _line(MODULE, "table.get_item"))))
    [(sl, _)] = out["app/db.py"]
    assert sl.module_names == ["helper", "table"]
    # transitive: table needs dynamodb, os and boto3; a small helper comes whole; UNUSED is left out
    assert sl.context.split("\n") == [
        "import os", "import boto3", 'dynamodb = boto3.resource("dynamodb")',
        'table = dynamodb.Table(os.environ["ORDERS_TABLE"])', "def helper(x):", "    return x",
    ]


def test_module_level_refs_and_other_files_are_left_out(tmp_path):
    _write(tmp_path, MODULE)
    pam = _pam(_ref("app/db.py", _line(MODULE, "boto3.resource")), _ref("infra/main.tf", 3),
               _ref("app/missing.py", 1))
    assert slicer.functions_with_usages(tmp_path, pam) == {}


def test_one_slice_per_function_even_with_several_refs(tmp_path):
    _write(tmp_path, MODULE)
    other = Component(id="events", kind="queue", provider="aws.sqs", resource_name="q",
                      usages=[_ref("app/db.py", _line(MODULE, "table.put_item"))])
    pam = _pam(_ref("app/db.py", _line(MODULE, "table.put_item")), _ref("app/db.py", _line(MODULE, "return sql")))
    pam.components.append(other)
    [(sl, comps)] = slicer.functions_with_usages(tmp_path, pam)["app/db.py"]
    assert sl.qualname == "Repo.put"
    assert [c.id for c in comps] == ["orders", "events"]


def test_windows_style_ref_paths_are_normalized(tmp_path):
    _write(tmp_path, MODULE)
    out = slicer.functions_with_usages(tmp_path, _pam(_ref("app\\db.py", _line(MODULE, "table.get_item")),
                                                      _ref("./app/db.py", _line(MODULE, "table.scan"))))
    assert [sl.qualname for sl, _ in out["app/db.py"]] == ["get_order", "outer.inner"]


def test_symbol_mismatch_trusts_the_ast_and_warns(tmp_path):
    _write(tmp_path, MODULE)
    seen: list[Event] = []
    out = slicer.functions_with_usages(
        tmp_path, _pam(_ref("app/db.py", _line(MODULE, "table.get_item"), symbol="wrong_name")),
        Emitter("t", [seen.append]))
    assert out["app/db.py"][0][0].qualname == "get_order"
    assert any(e.level == "warning" and "wrong_name" in e.message for e in seen)


def test_normalize_path():
    assert slicer.normalize_path("app\\db.py") == "app/db.py"
    assert slicer.normalize_path("./app/db.py") == "app/db.py"
    assert slicer.normalize_path("C:foo.py") is None
    assert slicer.normalize_path("/etc/passwd") is None


def test_function_at(tmp_path):
    _write(tmp_path, MODULE)
    assert slicer.function_at(tmp_path, "app/db.py", _line(MODULE, "return sql")).qualname == "Repo.put"
    assert slicer.function_at(tmp_path, "app/db.py", 1) is None
    assert slicer.function_at(tmp_path, "app/nope.py", 3) is None


# ---------- replace_function ----------

def _slice(tmp_path: Path, needle: str, text: str = MODULE, newline: str = "\n") -> tuple[str, slicer.FunctionSlice]:
    path = _write(tmp_path, text, newline=newline)
    sl = slicer.function_at(tmp_path, "app/db.py", _line(text, needle))
    assert sl is not None
    return path.read_bytes().decode("utf-8"), sl


def test_crlf_file_round_trips_byte_identical(tmp_path):
    text, sl = _slice(tmp_path, "table.get_item", newline="\r\n")
    assert slicer.replace_function(text, sl, sl.source, "\r\n") == text


def test_crlf_is_kept_when_a_function_changes(tmp_path):
    text, sl = _slice(tmp_path, "table.get_item", newline="\r\n")
    new = sl.source.replace("helper(resp.get(\"Item\"))", "resp.get(\"Item\")")
    out = slicer.replace_function(text, sl, new, "\r\n")
    assert out != text
    assert "\n" not in out.replace("\r\n", "")              # no lone LF slipped in
    assert out.count("\r\n") == text.count("\r\n")
    ast.parse(out)
    assert '@decorate("a")\r\n@other\r\ndef get_order' in out


def test_method_is_reindented_and_string_content_untouched(tmp_path):
    text, sl = _slice(tmp_path, "return sql")
    assert sl.source.startswith("def put(self, item):\n    sql")
    assert "\nSELECT *\n    FROM t" in sl.source               # string lines are not dedented
    new = sl.source.replace("table.put_item(Item=item)", "table.put_item(Item=dict(item))")
    out = slicer.replace_function(text, sl, new, "\n")
    assert "        table.put_item(Item=dict(item))\n" in out
    assert '        sql = """\nSELECT *\n    FROM t"""\n' in out
    ast.parse(out)


def test_tabs_stay_tabs(tmp_path):
    text = "class A:\n\tdef f(self):\n\t\treturn 1\n"
    path = _write(tmp_path, text)
    sl = slicer.function_at(tmp_path, "app/db.py", 3)
    assert sl.source == "def f(self):\n\treturn 1"
    out = slicer.replace_function(path.read_text(encoding="utf-8"), sl, "def f(self):\n\treturn 2", "\n")
    assert out == "class A:\n\tdef f(self):\n\t\treturn 2\n"


def test_indented_answer_is_dedented_first(tmp_path):
    text, sl = _slice(tmp_path, "return x")
    out = slicer.replace_function(text, sl, "    def helper(x):\n        return x + 1\n", "\n")
    assert "\ndef helper(x):\n    return x + 1\n" in out


def test_bottom_up_replacements_keep_line_numbers_valid(tmp_path):
    text, first = _slice(tmp_path, "table.get_item")
    second = slicer.function_at(tmp_path, "app/db.py", _line(MODULE, "return sql"))
    out = text
    for sl, extra in sorted([(first, "    a = 1\n    b = 2\n"), (second, "    c = 3\n")],
                            key=lambda item: item[0].start, reverse=True):
        head, rest = sl.source.split("\n", 1)
        out = slicer.replace_function(out, sl, f"{head}\n{extra}{rest}", "\n")
    tree = ast.parse(out)
    names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert {"get_order", "put", "outer", "inner", "helper"} <= set(names)
    assert "    a = 1\n    b = 2\n    resp = table.get_item" in out
    assert "        c = 3\n        sql = " in out


def test_last_function_without_trailing_newline(tmp_path):
    text = "def f():\n    return 1"
    path = _write(tmp_path, text)
    sl = slicer.function_at(tmp_path, "app/db.py", 2)
    assert slicer.replace_function(path.read_text(encoding="utf-8"), sl, "def f():\n    return 2\n", "\n") \
        == "def f():\n    return 2"


def test_bom_is_kept(tmp_path):
    path = tmp_path / "app" / "db.py"
    path.parent.mkdir(parents=True)
    path.write_bytes("\ufeffdef f():\n    return 1\n".encode())
    sl = slicer.function_at(tmp_path, "app/db.py", 1)
    assert sl.source == "def f():\n    return 1"
    out = slicer.replace_function(path.read_bytes().decode("utf-8"), sl, "def f():\n    return 2", "\n")
    assert out == "\ufeffdef f():\n    return 2\n"


def test_out_of_range_slice_raises():
    sl = slicer.FunctionSlice(file="a.py", name="f", qualname="f", start=5, end=9, col=0,
                              source="def f(): pass", is_async=False)
    with pytest.raises(ValueError, match="outside the file"):
        slicer.replace_function("x = 1\n", sl, "def f(): return 1", "\n")
