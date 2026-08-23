"""Service: export/import ролей, routing rules и корпусов в YAML.

T-425: роли + routing rules. T-438: секция corpora (только метаданные:
data_class и пин-модель по алиасу).
Export: выгрузка всех ролей, routing rules и корпусов из workspace в YAML.
Import: идемпотентная загрузка (upsert ролей по name, sync routing rules,
upsert корпусов по name).
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.config_io.schemas import (
    ConfigYAML,
    CorpusYAML,
    ImportResult,
    RoleYAML,
    RoutingRuleYAML,
)
from app.db.models import Corpus, Model, Role, RoutingRule
from app.errors import BadRequest
from app.policy.models import Policy
from app.policy.presets import BUILTIN_ROLES

SCHEMA_VERSION = 2
# v1 читается (обратная совместимость — секция corpora просто отсутствует);
# экспорт всегда пишет текущую версию.
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

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
    """Экспортирует роли, routing rules и корпуса в YAML-строку."""
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

    # T-438: корпуса — только метаданные. Пин выгружается алиасом модели:
    # UUID различается между инстансами, алиас стабилен (см. CorpusYAML).
    corpora_result = await session.execute(
        select(Corpus).where(Corpus.workspace_id == workspace_id).order_by(Corpus.name)
    )
    corpora = corpora_result.scalars().all()

    pinned_ids = {c.pinned_model_id for c in corpora if c.pinned_model_id is not None}
    models_by_id: dict[str, Model] = {}
    if pinned_ids:
        models_result = await session.execute(
            select(Model).where(
                Model.workspace_id == workspace_id,
                Model.id.in_(pinned_ids),
            )
        )
        models_by_id = {m.id: m for m in models_result.scalars().all()}

    corpora_yaml: list[CorpusYAML] = []
    for corpus in corpora:
        pinned_alias: str | None = None
        if corpus.pinned_model_id is not None:
            model = models_by_id.get(corpus.pinned_model_id)
            if model is None:
                # FK гарантирует существование, но ошибка — явно, не тихо.
                raise BadRequest(
                    f"Корпус '{corpus.name}' ссылается на несуществующую модель",
                    hint="Нарушена целостность данных: пересоздайте привязку пина",
                )
            pinned_alias = model.alias
        try:
            corpus_yaml = CorpusYAML.model_validate(
                {
                    "name": corpus.name,
                    "data_class": corpus.data_class,
                    "pinned_model_alias": pinned_alias,
                }
            )
        except ValidationError as exc:
            raise BadRequest(
                f"Корпус '{corpus.name}' содержит недопустимый data_class",
                hint="Допустимые значения: К0, К1, К2, К3",
            ) from exc
        corpora_yaml.append(corpus_yaml)

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
        corpora=corpora_yaml,
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
    actor_user_id: str | None = None,
) -> ImportResult:
    """Импортирует роли, routing rules и корпуса из YAML.

    Роли: upsert по name (full overwrite policy + is_builtin).
    Routing rules: full sync (delete all existing, insert from YAML).
    Корпуса (T-438): upsert по name; аудит пишется только при реальном
    изменении и только если передан actor (CLI работает без пользователя).
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

    if config.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise BadRequest(
            f"Неподдерживаемая версия схемы: {config.schema_version}",
            hint=f"Поддерживаются версии: {', '.join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)}",
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

    # T-438: резолв алиасов пин-моделей и валидация ограничений ADR-12
    # до любой записи — ошибка откатывает весь импорт целиком.
    corpus_pins, models_by_id = await _resolve_corpus_pins(session, workspace_id, config.corpora)

    if dry_run:
        # В dry-run считаем что было бы изменено
        roles_created, roles_updated, roles_unchanged = await _compute_role_diff(
            session, workspace_id, config, validated_policies, warnings
        )
        rules_identical = await _check_rules_identical(session, workspace_id, config)
        corpora_created, corpora_updated, corpora_unchanged = await _compute_corpora_diff(
            session, workspace_id, config, corpus_pins
        )
        return ImportResult(
            roles_created=roles_created,
            roles_updated=roles_updated,
            roles_unchanged=roles_unchanged,
            routing_rules_replaced=not rules_identical,
            routing_rules_count=len(config.routing_rules),
            corpora_created=corpora_created,
            corpora_updated=corpora_updated,
            corpora_unchanged=corpora_unchanged,
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

    # --- T-438: корпуса: upsert по (workspace_id, name) ---
    corpora_created = 0
    corpora_updated = 0
    corpora_unchanged = 0

    for corpus_yaml in config.corpora:
        corpus_result = await session.execute(
            select(Corpus).where(
                Corpus.workspace_id == workspace_id,
                Corpus.name == corpus_yaml.name,
            )
        )
        existing_corpus = corpus_result.scalar_one_or_none()
        pinned_model_id = corpus_pins[corpus_yaml.name]

        if existing_corpus is None:
            session.add(
                Corpus(
                    workspace_id=workspace_id,
                    name=corpus_yaml.name,
                    data_class=corpus_yaml.data_class,
                    pinned_model_id=pinned_model_id,
                )
            )
            corpora_created += 1
            continue

        data_class_changed = existing_corpus.data_class != corpus_yaml.data_class
        pin_changed = existing_corpus.pinned_model_id != pinned_model_id
        if not data_class_changed and not pin_changed:
            corpora_unchanged += 1
            continue

        corpora_updated += 1
        if data_class_changed:
            old_data_class = existing_corpus.data_class
            existing_corpus.data_class = corpus_yaml.data_class
            # Аудит — только при реальном изменении (паттерн T-401)
            # и только при наличии actor (CLI работает без пользователя).
            if actor_user_id is not None:
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    action="corpus.data_class_changed",
                    object_type="corpus",
                    object_id=existing_corpus.id,
                    meta={
                        "old": old_data_class,
                        "new": corpus_yaml.data_class,
                        "corpus_name": existing_corpus.name,
                    },
                )
        if pin_changed:
            old_alias = None
            if existing_corpus.pinned_model_id is not None:
                old_model = models_by_id.get(existing_corpus.pinned_model_id)
                old_alias = old_model.alias if old_model is not None else None
            existing_corpus.pinned_model_id = pinned_model_id
            if actor_user_id is not None:
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    action="corpus.pinned_model_changed",
                    object_type="corpus",
                    object_id=existing_corpus.id,
                    meta={
                        "old": old_alias,
                        "new": corpus_yaml.pinned_model_alias,
                        "corpus_name": existing_corpus.name,
                    },
                )

    await session.flush()

    return ImportResult(
        roles_created=roles_created,
        roles_updated=roles_updated,
        roles_unchanged=roles_unchanged,
        routing_rules_replaced=not rules_identical,
        routing_rules_count=len(config.routing_rules),
        corpora_created=corpora_created,
        corpora_updated=corpora_updated,
        corpora_unchanged=corpora_unchanged,
        warnings=warnings,
    )


async def _resolve_corpus_pins(
    session: AsyncSession,
    workspace_id: str,
    corpora: list[CorpusYAML],
) -> tuple[dict[str, str | None], dict[str, Model]]:
    """Резолвит pinned_model_alias → Model.id и валидирует ограничения.

    ADR-12: для К2/К3 пин обязателен и указывает только на локальную
    модель. Любой нерезолвящийся алиас — явная ошибка (молчаливый null
    тихо терял бы гарантии К2/К3).

    Возвращает (имя корпуса → pinned_model_id или None, все модели
    workspace по id — для аудита старых значений).
    """
    models_result = await session.execute(select(Model).where(Model.workspace_id == workspace_id))
    models = models_result.scalars().all()
    models_by_alias = {m.alias: m for m in models}
    models_by_id = {m.id: m for m in models}

    pins: dict[str, str | None] = {}
    for corpus_yaml in corpora:
        alias = corpus_yaml.pinned_model_alias
        if alias is None:
            if corpus_yaml.data_class in ("К2", "К3"):
                raise BadRequest(
                    f"Корпус '{corpus_yaml.name}': класс {corpus_yaml.data_class} "
                    "требует pinned_model_alias",
                    hint="ADR-12: К2/К3 фиксируют локальную модель пином",
                )
            pins[corpus_yaml.name] = None
            continue

        model = models_by_alias.get(alias)
        if model is None:
            raise BadRequest(
                f"Корпус '{corpus_yaml.name}': модель с алиасом '{alias}' не найдена",
                hint="Создайте модель до импорта или уберите pinned_model_alias",
            )
        if corpus_yaml.data_class in ("К2", "К3") and model.locality != "local":
            raise BadRequest(
                f"Корпус '{corpus_yaml.name}': модель '{alias}' не локальная",
                hint="ADR-12: К2/К3 пинятся только на локальные модели",
            )
        pins[corpus_yaml.name] = model.id

    return pins, models_by_id


async def _compute_corpora_diff(
    session: AsyncSession,
    workspace_id: str,
    config: ConfigYAML,
    corpus_pins: dict[str, str | None],
) -> tuple[int, int, int]:
    """Вычисляет diff корпусов без записи в БД (для dry-run)."""
    created = 0
    updated = 0
    unchanged = 0

    for corpus_yaml in config.corpora:
        result = await session.execute(
            select(Corpus).where(
                Corpus.workspace_id == workspace_id,
                Corpus.name == corpus_yaml.name,
            )
        )
        existing = result.scalar_one_or_none()
        pinned_model_id = corpus_pins[corpus_yaml.name]

        if existing is None:
            created += 1
            continue

        changed = (
            existing.data_class != corpus_yaml.data_class
            or existing.pinned_model_id != pinned_model_id
        )
        if changed:
            updated += 1
        else:
            unchanged += 1

    return created, updated, unchanged


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
