"""Helpers for full, empty and partial migration scopes."""

from __future__ import annotations

from collections.abc import Iterable

from boomerang.models import AppModel, Component, Finding, Scope

MOVABLE_KINDS = {"object_store", "kv_table", "queue"}


def movable(pam: AppModel) -> list[Component]:
    return [component for component in pam.components if component.kind in MOVABLE_KINDS]


def full_scope(pam: AppModel) -> Scope:
    return Scope(selfhosted=[component.id for component in movable(pam)])


def empty_scope() -> Scope:
    return Scope(selfhosted=[])


def validate(pam: AppModel, scope: Scope) -> Scope:
    movable_ids = {component.id for component in movable(pam)}
    unknown = sorted(set(scope.selfhosted) - movable_ids)
    if unknown:
        raise ValueError(f"unknown or non-movable component id(s): {', '.join(unknown)}")
    return Scope(selfhosted=list(dict.fromkeys(scope.selfhosted)))


def normalize_scope(pam: AppModel, scope: Scope | Iterable[str] | None) -> Scope:
    if scope is None:
        return full_scope(pam)
    if isinstance(scope, Scope):
        return validate(pam, scope)
    return validate(pam, Scope(selfhosted=[str(component_id) for component_id in scope]))


def kept(pam: AppModel, scope: Scope | Iterable[str] | None) -> list[Component]:
    """Movable components that stay on AWS under ``scope``."""
    selfhosted = set(normalize_scope(pam, scope).selfhosted)
    return [component for component in movable(pam) if component.id not in selfhosted]


def is_hybrid(pam: AppModel, scope: Scope | Iterable[str] | None) -> bool:
    """Some movable components self-hosted and some on AWS."""
    selfhosted = set(normalize_scope(pam, scope).selfhosted)
    return bool(selfhosted) and bool(kept(pam, scope))


def hybrid_finding(pam: AppModel, scope: Scope | Iterable[str] | None) -> Finding | None:
    """The 🔴 finding of a hybrid scope (docs/16 §3.2): the self-hosted app needs scoped AWS credentials."""
    if not is_hybrid(pam, scope):
        return None
    names = ", ".join(sorted(component.id for component in kept(pam, scope)))
    return Finding(
        analysis="Hybrid scope keeps AWS components while other components run self-hosted.",
        component_id="hybrid",
        feature="aws_credentials",
        light="red",
        what_changes=f"Create scoped AWS credentials for kept components: {names}.",
        evidence=[],
    )
