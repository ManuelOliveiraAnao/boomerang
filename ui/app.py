"""Boomerang UI (owner: D). Run with: streamlit run ui/app.py

Two pages: the landing page (what Boomerang is and how it works) and the
dashboard (run a migration and inspect every stage).
"""
from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run ui/app.py` only puts ui/ on the path; we need the repo root
# so `ui.*` and (later) `boomerang.*` import the same way everywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from ui import dashboard, landing, theme  # noqa: E402

st.set_page_config(
    page_title="Boomerang",
    page_icon=theme.FAVICON,
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject_css()
st.logo(str(theme.ASSETS / "wordmark.svg"), icon_image=str(theme.ASSETS / "logo.svg"), size="large")

home = st.Page(landing.render, title="Overview", default=True)
app = st.Page(dashboard.render, title="Dashboard", url_path="dashboard")
st.session_state["_pages"] = {"home": home, "dashboard": app}

st.navigation([home, app], position="top").run()
