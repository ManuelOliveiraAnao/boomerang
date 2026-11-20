"""Leaseweb files from the PAM (Jinja2 templates in generators/templates/). Owner B, task B6.

Spec: docs/03 §3.7, docs/14. Stub created by C1: B replaces the bodies.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt
from boomerang.models import AppModel, GeneratedFile


def make_secrets(pam: AppModel) -> dict[str, str]:
    """Proposed (docs/15 L7): generated passwords/keys (`secrets.token_urlsafe`, 24+ chars).
    Only secret values: no user names, hosts or ports (every value here is redacted from prompts)."""
    raise NotBuilt("generators.leaseweb.make_secrets", owner="B", doc="docs/15 L7")


def render(pam: AppModel, secrets: dict[str, str]) -> list[GeneratedFile]:
    """Binding (docs/03 §3.7). Pure: returns files with paths relative to runs/<id>/leaseweb/.
    The pipeline writes them (LF) and copies sovereign_sdk/ into app/ (docs/15 L7)."""
    raise NotBuilt("generators.leaseweb.render", owner="B", doc="docs/03 §3.7")
