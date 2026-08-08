"""Пресеты пяти встроенных ролей. Соответствуют arch.md §3."""

from __future__ import annotations

from app.policy.models import Policy

BUILTIN_ROLES: dict[str, Policy] = {
    "support": Policy(
        models=["local/*"],
        max_input_tokens=16000,
        max_output_tokens=2000,
        reasoning="off",
        budget={"tokens_month": 2_000_000, "cost_month": 0},
        rpm=30,
        tpm=20000,
        corpora=["public"],
        capabilities=["chat"],
    ),
    "developer": Policy(
        models=["local/*", "external/*"],
        max_input_tokens=64000,
        max_output_tokens=8000,
        reasoning="optional",
        budget={"tokens_month": 5_000_000, "cost_month": 10},
        rpm=60,
        tpm=60000,
        corpora=["public", "team"],
        capabilities=["chat", "upload", "custom_prompts"],
    ),
    "architect": Policy(
        models=["local/*", "external/*"],
        max_input_tokens=200000,
        max_output_tokens=32000,
        reasoning="optional",
        budget={"tokens_month": 20_000_000, "cost_month": 50},
        rpm=120,
        tpm=200000,
        corpora=["public", "team", "private"],
        capabilities=["chat", "upload", "custom_prompts", "manage_corpora", "share"],
    ),
    "manager": Policy(
        models=["local/*", "external/*"],
        max_input_tokens=64000,
        max_output_tokens=8000,
        reasoning="optional",
        budget={"tokens_month": 5_000_000, "cost_month": 10},
        rpm=60,
        tpm=60000,
        corpora=["public", "team"],
        capabilities=["chat", "upload", "custom_prompts", "view_analytics"],
    ),
    "admin": Policy(
        models=["*"],
        max_input_tokens=None,
        max_output_tokens=None,
        reasoning="optional",
        budget=None,
        rpm=None,
        tpm=None,
        corpora=["*"],
        capabilities=["*"],
    ),
}
