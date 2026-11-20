"""Helpers for full, empty and partial migration scopes."""

from __future__ import annotations

from collections.abc import Iterable

from boomerang.models import AppModel, Component, Scope


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
