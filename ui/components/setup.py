"""Tab 3: Leaseweb setup (owner: D, task D5).

The live verification checklist (A's verifier: config → boot → schema → tests → health, per
attempt) and a browser for the generated Leaseweb files (B's generator), with secrets masked
on screen and left out of the zip.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import streamlit as st

from ui.data import HIDDEN_FILES, Source, mask_secrets
from ui.theme import LIGHT_COLORS, check_row, empty_state

CHECK_LABEL = {"config": "Files valid", "boot": "Stack booted", "schema": "Database schema",
               "tests": "Original tests", "health": "Health check"}
LANG = {".yml": "yaml", ".yaml": "yaml", ".json": "json", ".sh": "bash", ".md": "markdown",
        ".py": "python", ".sql": "sql", ".txt": "text", ".env": "bash", ".example": "bash", "": "docker"}


def _files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if (p.is_file() and p.name not in HIDDEN_FILES and "__pycache__" not in rel.parts
                and p.suffix != ".pyc"):
            out.append(rel)
    out.sort(key=lambda r: (len(r.parts) > 1, "sovereign_sdk" in r.parts, str(r)))
    return out


def _zip(root: Path, files: list[Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(root / rel, arcname=f"leaseweb/{rel.as_posix()}")
    return buf.getvalue()


def _checklist(summ: dict) -> None:
    verifs = summ.get("verifications") or []
    base = summ.get("baseline")
    if base:
        st.caption(f"Baseline: the app's original tests on AWS (moto) before migrating: "
                   f"{base['passed']}/{base['total']} passed.")
    if not verifs:
        empty_state("Not verified yet", "After generation the stack boots on this laptop and the app's "
                    "original tests run against it.")
        return
    cols = st.columns(len(verifs))
    for col, (i, v) in zip(cols, enumerate(verifs, 1)):
        ok = all(c["ok"] for c in v["checks"]) and bool(v.get("tests")) and \
            v["tests"]["failed"] == 0 and v["tests"]["errors"] == 0 and v["tests"]["total"] > 0
        color = LIGHT_COLORS["green"] if ok else LIGHT_COLORS["red"]
        with col, st.container(border=True):
            st.markdown(f'<b>Attempt {i}</b> <span style="color:{color};font-size:0.8rem">'
                        f'{"● passed" if ok else "● failed"}</span>', unsafe_allow_html=True)
            for c in v["checks"]:
                st.markdown(check_row(c["ok"], CHECK_LABEL.get(c["name"], c["name"]),
                                      f'· {c.get("detail", "")} · {c.get("duration_s", 0):.1f}s'),
                            unsafe_allow_html=True)
            t = v.get("tests") or {}
            for f in t.get("failures", []):
                with st.expander(f['test'].split('::')[-1], icon=":material/cancel:"):
                    st.code(f"{f['message']}\n\n{f['traceback']}", language="text")


def render(src: Source) -> None:
    summ = src.summary
    st.markdown("#### Verification")
    _checklist(summ)

    st.markdown("#### Generated Leaseweb files")
    root = (src.path / "leaseweb") if src.path else None
    if root is None or not root.is_dir():
        empty_state("No files yet", "docker-compose.yml, cloud-init.yaml, deploy.sh, backups and "
                    "DEPLOY.md appear here once the plan is approved.")
        return
    files = _files(root)
    left, right = st.columns([1, 3])
    with left:
        pick = st.radio("Files", files, format_func=lambda r: r.as_posix(), key="setup_file",
                        label_visibility="collapsed")
        st.download_button("Download setup (.zip)", _zip(root, files), file_name="leaseweb-setup.zip",
                           mime="application/zip", width="stretch")
        st.caption("secrets.env is never shown or zipped; move it to the server securely.")
    with right:
        text = (root / pick).read_text(encoding="utf-8", errors="replace")
        lang = LANG.get(pick.suffix, "text") if pick.suffix or pick.name != "Dockerfile" else "docker"
        st.caption(f"leaseweb/{pick.as_posix()} · secrets masked")
        st.code(mask_secrets(text), language=lang, line_numbers=True)
