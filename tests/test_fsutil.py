from __future__ import annotations

import os
import shutil

import pytest

from boomerang import fsutil


def test_atomic_write_text_is_utf8_lf_and_overwrites(tmp_path):
    path = tmp_path / "a" / "run.json"
    fsutil.atomic_write_text(path, "one\r\ntwo ✅\n")
    fsutil.atomic_write_text(path, "three\r\nfour ✅\n")
    assert path.read_bytes() == "three\nfour ✅\n".encode()
    assert [p.name for p in path.parent.iterdir()] == ["run.json"]


def test_atomic_write_retries_when_windows_blocks_the_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil, "REPLACE_DELAY_S", 0)
    fsutil.atomic_write_text(tmp_path / "run.json", "ok")
    assert len(calls) == 3 and (tmp_path / "run.json").read_text(encoding="utf-8") == "ok"


def test_atomic_write_gives_up_and_cleans_the_tmp_file(tmp_path, monkeypatch):
    def always(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(fsutil.os, "replace", always)
    monkeypatch.setattr(fsutil, "REPLACE_DELAY_S", 0)
    with pytest.raises(PermissionError):
        fsutil.atomic_write_text(tmp_path / "run.json", "x")
    assert list(tmp_path.iterdir()) == []


def test_write_text_lf_never_writes_crlf(tmp_path):
    path = tmp_path / "backup" / "pg_backup.sh"
    fsutil.write_text_lf(path, "#!/bin/sh\r\nset -eu\r\npg_dump\n")
    assert path.read_bytes() == b"#!/bin/sh\nset -eu\npg_dump\n"


def test_hash_tree_skips_caches(tmp_path):
    (tmp_path / "tests" / "__pycache__").mkdir(parents=True)
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    before = fsutil.hash_tree(tmp_path / "tests")
    (tmp_path / "tests" / "__pycache__" / "test_x.cpython-312-pytest-9.pyc").write_bytes(b"\x00")
    (tmp_path / "tests" / "conftest.pyc").write_bytes(b"\x00")
    (tmp_path / "tests" / ".pytest_cache").mkdir()
    (tmp_path / "tests" / ".pytest_cache" / "v").write_text("x", encoding="utf-8")
    assert fsutil.hash_tree(tmp_path / "tests") == before
    assert list(before) == ["test_x.py"]
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): assert 1\n", encoding="utf-8")
    assert fsutil.hash_tree(tmp_path / "tests") != before


def test_copy_ignore_keeps_env_example_only(tmp_path):
    src = tmp_path / "src"
    for rel in ("app/db.py", ".env", ".env.local", ".env.example", "app/__pycache__/db.cpython-312.pyc",
                ".venv/pyvenv.cfg", "infra/main.tf"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("x", encoding="utf-8")
    shutil.copytree(src, tmp_path / "dst", ignore=fsutil.copy_ignore(extra=("infra",)))
    copied = sorted(p.relative_to(tmp_path / "dst").as_posix() for p in (tmp_path / "dst").rglob("*") if p.is_file())
    assert copied == [".env.example", "app/db.py"]
