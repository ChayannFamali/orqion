"""Ролевая модель: resolve_policy и enforce. Единственное место, где существует роль."""

from app.policy.enforce import enforce
from app.policy.models import Policy
from app.policy.presets import BUILTIN_ROLES
from app.policy.resolve import resolve_policy

__all__ = [
    "BUILTIN_ROLES",
    "Policy",
    "enforce",
    "resolve_policy",
]
