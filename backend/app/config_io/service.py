"""Service: export/import ролей и routing rules в YAML (T-425).

Export: выгрузка всех ролей + routing rules из workspace в YAML-строку.
Import: идемпотентная загрузка (upsert ролей по name, sync routing rules).
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_io.schemas import (
    ConfigYAML,
    ImportResult,
    RoleYAML,
    RoutingRuleYAML,
)
from app.db.models import Role, RoutingRule
from app.errors import BadRequest
from app.policy.models import Policy
from app.policy.presets import BUILTIN_ROLES

SCHEMA_VERSION = 1

ROUTING_RULE_BUSINESS_FIELDS = (
    "order",
    "is_default",
    "is_terminal",
    "when_corpus_class",
    "when_role",
    "when_task",
    "when_model_alias",
    "to_models",
    "allow_locality",
    "fallback_models",
    "reason",
)


async def export_config(session: AsyncSession, workspace_id: str) -> str:
    """Экспортирует роли и routing rules в YAML-строку."""
    roles_result = await session.execute(
        select(Role)
        .where(Role.workspace_id == workspace_id)
        .order_by(Role.is_builtin.desc(), Role.name)
    )
    roles = roles_result.scalars().all()

    rules_result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.workspace_id == workspace_id)
        .order_by(RoutingRule.order)
    )
    rules = rules_result.scalars().all()

    config = ConfigYAML(
        schema_version=SCHEMA_VERSION,
        roles=[
            RoleYAML(
                name=role.name,
                is_builtin=role.is_builtin,
                policy=role.policy,
            )
            for role in roles
        ],
        routing_rules=[
            RoutingRuleYAML(
                order=rule.order,
                is_default=rule.is_default,
                is_terminal=rule.is_terminal,
                when_corpus_class=rule.when_corpus_class,
                when_role=rule.when_role,
                when_task=rule.when_task,
                when_model_alias=rule.when_model_alias,
                to_models=rule.to_models,
                allow_locality=rule.allow_locality,
                fallback_models=rule.fallback_models,
                reason=rule.reason,
            )
            for rule in rules
        ],
    )

    yaml_data = config.model_dump(mode="json")
    result: str = yaml.dump(
        yaml_data, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    return result


async def import_config(
    session: AsyncSession,
    workspace_id: str,
    yaml_content: str,
    *,
    dry_run: bool = False,
) -> ImportResult:
    """Импортирует роли и routing rules из YAML.

    Роли: upsert по name (full overwrite policy + is_builtin).
    Routing rules: full sync (delete all existing, insert from YAML).
    Вся операция в одной транзакции — откат при любой ошибке.
    """
    raw: Any
    try:
        raw = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise BadRequest(
            "YAML-парсинг не удался",
            hint=str(exc),
        ) from exc

    if not isinstance(raw, dict):
        raise BadRequest(
            "YAML должен быть mapping-ом на верхнем уровне",
            hint="Ожидается структура с schema_version, roles, routing_rules",
        )

    try:
        config = ConfigYAML.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors()
        loc = ".".join(str(p) for p in errors[0]["loc"]) if errors else "root"
        raise BadRequest(
            f"Схема YAML некорректна: поле '{loc}'",
            hint=errors[0]["msg"] if errors else "Проверьте структуру",
        ) from exc

    if config.schema_version != SCHEMA_VERSION:
        raise BadRequest(
            f"Неподдерживаемая версия схемы: {config.schema_version}",
            hint=f"Ожидается версия {SCHEMA_VERSION}",
        )

    # Валидация policy для каждой роли до записи в БД
    validated_policies: dict[str, Policy] = {}
    for role_yaml in config.roles:
        try:
            policy = Policy.model_validate(role_yaml.policy)
        except ValidationError as exc:
            errors = exc.errors()
            loc = ".".join(str(p) for p in errors[0]["loc"]) if errors else "policy"
            raise BadRequest(
                f"Политика роли '{role_yaml.name}' некорректна: поле '{loc}'",
                hint=errors[0]["msg"] if errors else "Проверьте структуру политики",
            ) from exc
        validated_policies[role_yaml.name] = policy

    # Предварительный сбор warnings для loose references
    warnings: list[str] = []
    role_names = {r.name for r in config.roles}
    for rule in config.routing_rules:
        if rule.when_role is not None and rule.when_role not in role_names:
            warnings.append(
                f"Routing rule order={rule.order}: when_role='{rule.when_role}' "
                "не найдено среди ролей в YAML"
            )

    if dry_run:
        # В dry-run считаем что было бы изменено
        roles_created, roles_updated, roles_unchanged = await _compute_role_diff(
            session, workspace_id, config, validated_policies, warnings
        )
        rules_identical = await _check_rules_identical(session, workspace_id, config)
        return ImportResult(
            roles_created=roles_created,
            roles_updated=roles_updated,
            roles_unchanged=roles_unchanged,
            routing_rules_replaced=not rules_identical,
            routing_rules_count=len(config.routing_rules),
            warnings=warnings,
        )

    # --- Роли: upsert по name ---
    roles_created = 0
    roles_updated = 0
    roles_unchanged = 0

    for role_yaml in config.roles:
        result = await session.execute(
            select(Role).where(
                Role.workspace_id == workspace_id,
                Role.name == role_yaml.name,
            )
        )
        existing = result.scalar_one_or_none()
        policy = validated_policies[role_yaml.name]
        policy_dict = policy.model_dump(mode="json")

        if existing is None:
            session.add(
                Role(
                    workspace_id=workspace_id,
                    name=role_yaml.name,
                    is_builtin=role_yaml.is_builtin,
                    policy=policy_dict,
                )
            )
            roles_created += 1
        else:
            changed = False
            if existing.policy != policy_dict:
                existing.policy = policy_dict
                changed = True
            if existing.is_builtin != role_yaml.is_builtin:
                existing.is_builtin = role_yaml.is_builtin
                changed = True

            if changed:
                roles_updated += 1
                # Warning: builtin-роль перезаписана с policy, отличным от presets.py
                if role_yaml.is_builtin and role_yaml.name in BUILTIN_ROLES:
                    preset_policy = BUILTIN_ROLES[role_yaml.name].model_dump(mode="json")
                    if policy_dict != preset_policy:
                        warnings.append(
                            f"Builtin-роль '{role_yaml.name}' перезаписана с policy, "
                            "отличным от текущего кода (presets.py)"
                        )
            else:
                roles_unchanged += 1

    await session.flush()

    # --- Routing rules: full sync ---
    rules_identical = await _check_rules_identical(session, workspace_id, config)

    if not rules_identical:
        await session.execute(delete(RoutingRule).where(RoutingRule.workspace_id == workspace_id))
        for rule_yaml in config.routing_rules:
            session.add(
                RoutingRule(
                    workspace_id=workspace_id,
                    order=rule_yaml.order,
                    is_default=rule_yaml.is_default,
                    is_terminal=rule_yaml.is_terminal,
                    when_corpus_class=rule_yaml.when_corpus_class,
                    when_role=rule_yaml.when_role,
                    when_task=rule_yaml.when_task,
                    when_model_alias=rule_yaml.when_model_alias,
                    to_models=rule_yaml.to_models,
                    allow_locality=rule_yaml.allow_locality,
                    fallback_models=rule_yaml.fallback_models,
                    reason=rule_yaml.reason,
                )
            )
        await session.flush()

    return ImportResult(
        roles_created=roles_created,
        roles_updated=roles_updated,
        roles_unchanged=roles_unchanged,
        routing_rules_replaced=not rules_identical,
        routing_rules_count=len(config.routing_rules),
        warnings=warnings,
    )


async def _compute_role_diff(
    session: AsyncSession,
    workspace_id: str,
    config: ConfigYAML,
    validated_policies: dict[str, Policy],
    warnings: list[str],
) -> tuple[int, int, int]:
    """Вычисляет diff ролей без записи в БД (для dry-run)."""
    created = 0
    updated = 0
    unchanged = 0

    for role_yaml in config.roles:
        result = await session.execute(
            select(Role).where(
                Role.workspace_id == workspace_id,
                Role.name == role_yaml.name,
            )
        )
        existing = result.scalar_one_or_none()
        policy_dict = validated_policies[role_yaml.name].model_dump(mode="json")

        if existing is None:
            created += 1
        else:
            changed = existing.policy != policy_dict or existing.is_builtin != role_yaml.is_builtin
            if changed:
                updated += 1
                if role_yaml.is_builtin and role_yaml.name in BUILTIN_ROLES:
                    preset_policy = BUILTIN_ROLES[role_yaml.name].model_dump(mode="json")
                    if policy_dict != preset_policy:
                        warnings.append(
                            f"Builtin-роль '{role_yaml.name}' перезаписана с policy, "
                            "отличным от текущего кода (presets.py)"
                        )
            else:
                unchanged += 1

    return created, updated, unchanged


async def _check_rules_identical(
    session: AsyncSession,
    workspace_id: str,
    config: ConfigYAML,
) -> bool:
    """Проверяет, идентичны ли существующие routing rules YAML (по бизнес-полям)."""
    result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.workspace_id == workspace_id)
        .order_by(RoutingRule.order)
    )
    existing = result.scalars().all()

    if len(existing) != len(config.routing_rules):
        return False

    for db_rule, yaml_rule in zip(existing, config.routing_rules, strict=False):
        for field_name in ROUTING_RULE_BUSINESS_FIELDS:
            db_val = getattr(db_rule, field_name)
            yaml_val = getattr(yaml_rule, field_name)
            if db_val != yaml_val:
                return False

    return True
