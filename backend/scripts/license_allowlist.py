"""Allowlist пакетов с UNKNOWN лицензией для CI license check (TD-1).

pip-licenses не находит trove classifiers или metadata License field для этих
пакетов (PEP 639 License-Expression, нестандартный формат). Каждый пакет вручную
проверен против LICENSE file в upstream репозитории.

Формат: имя_пакета → (лицензия, источник, дата_проверки)
При добавлении новой зависимости с UNKNOWN лицензией — добавь запись сюда
с указанием источника проверки.

Список генерируется для ALLOWED_UNKNOWN в .github/workflows/ci.yml.
При обновлении версии пакета — перепроверь лицензию.

ОБОСНОВАНИЕ ИСКЛЮЧЕНИЯ ДЛЯ MPL-2.0 (решение согласовано в ревью Т-502,
2026-09-03). ADR-2 называет три разрешённых типа — MIT, Apache, BSD;
MPL-2.0 там не поименован и в списке запрещённых (GPL/AGPL/SSPL/BUSL)
CI-проверки тоже не значится. Пакеты под MPL-2.0 появляются в дереве
транзитивно и осознанно принимаются на следующих основаниях:
  - MPL-2.0 — слабый файловый копилефт: обязательства копилефта
    ограничены собственными файлами пакета и НЕ распространяются на код
    проекта, в отличие от GPL/AGPL/SSPL/BUSL, которые реально запрещены;
  - это транзитивные листовые зависимости (не ядро архитектуры), которые
    невозможно убрать, не отказавшись от вышестоящей разрешённой
    зависимости;
  - записи ниже документируют каждый такой пакет как осознанное
    исключение, чтобы проникновение не было молчаливым.
Первый случай — `certifi` (транзитивно через `httpx`, фактически в дереве
с начала проекта); второй — `orjson` (транзитивно через `langsmith`
в составе дополнения `agent`, Т-502).
"""

# (имя_пакета, лицензия, источник, дата_проверки)
ALLOWED_UNKNOWN_VERIFIED: dict[str, tuple[str, str, str]] = {
    "aioitertools": (
        "MIT",
        "https://github.com/omnilib/aioitertools/blob/main/LICENSE",
        "2026-08-15",
    ),
    "alembic": ("MIT", "https://github.com/sqlalchemy/alembic/blob/main/LICENSE", "2026-08-15"),
    "annotated-doc": ("MIT", "PyPI metadata License-Expression", "2026-08-15"),
    "anyio": ("MIT", "https://github.com/agronholm/anyio/blob/master/LICENSE", "2026-08-15"),
    "argon2-cffi": ("MIT", "https://github.com/hynek/argon2-cffi/blob/main/LICENSE", "2026-08-15"),
    "argon2-cffi-bindings": (
        "MIT",
        "https://github.com/hynek/argon2-cffi/blob/main/LICENSE",
        "2026-08-15",
    ),
    "ast_serialize": ("MIT", "PyPI metadata", "2026-08-15"),
    "attrs": ("MIT", "https://github.com/python-attrs/attrs/blob/main/LICENSE", "2026-08-15"),
    "certifi": (
        "MPL-2.0",
        "https://github.com/certifi/python-certifi/blob/master/LICENSE",
        "2026-09-03",
    ),
    "cffi": ("MIT", "https://github.com/python-cffi/cffi/blob/main/LICENSE", "2026-08-15"),
    "charset-normalizer": (
        "MIT",
        "https://github.com/Ousret/charset_normalizer/blob/master/LICENSE",
        "2026-08-15",
    ),
    "cfn-lint": (
        "Apache-2.0",
        "https://github.com/aws-cloudformation/cfn-lint/blob/main/LICENSE",
        "2026-08-15",
    ),
    "click": ("BSD-3-Clause", "https://github.com/pallets/click/blob/main/LICENSE", "2026-08-15"),
    "cryptography": (
        "Apache-2.0 OR BSD-3-Clause",
        "https://github.com/pyca/cryptography/blob/main/LICENSE",
        "2026-08-15",
    ),
    "doclang": ("MIT", "PyPI metadata", "2026-08-15"),
    "docling-core": (
        "MIT",
        "https://github.com/docling-project/docling/blob/main/LICENSE",
        "2026-08-15",
    ),
    "docling-parse": (
        "MIT",
        "https://github.com/docling-project/docling/blob/main/LICENSE",
        "2026-08-15",
    ),
    "docling-slim": (
        "MIT",
        "https://github.com/docling-project/docling/blob/main/LICENSE",
        "2026-08-15",
    ),
    "dulwich": (
        "Apache-2.0 OR GPL-2.0-or-later",
        "https://github.com/jelmer/dulwich/blob/master/COPYING",
        "2026-08-16",
    ),
    "fastapi": ("MIT", "https://github.com/tiangolo/fastapi/blob/master/LICENSE", "2026-08-15"),
    "flask": ("BSD-3-Clause", "https://github.com/pallets/flask/blob/main/LICENSE", "2026-08-15"),
    "flask-cors": (
        "MIT",
        "https://github.com/corydolphin/flask-cors/blob/main/LICENSE",
        "2026-08-15",
    ),
    "fsspec": (
        "Apache-2.0",
        "https://github.com/fsspec/filesystem_spec/blob/main/LICENSE",
        "2026-08-15",
    ),
    "greenlet": (
        "MIT",
        "https://github.com/python-greenlet/greenlet/blob/main/LICENSE",
        "2026-08-15",
    ),
    "idna": (
        "BSD-3-Clause",
        "https://github.com/jeffkaufman/idna/blob/master/LICENSE",
        "2026-08-15",
    ),
    "iniconfig": (
        "MIT",
        "https://github.com/python-iniconfig/iniconfig/blob/main/LICENSE",
        "2026-08-15",
    ),
    "jsonschema": (
        "MIT",
        "https://github.com/python-jsonschema/jsonschema/blob/main/LICENSE",
        "2026-08-15",
    ),
    "jsonschema-specifications": (
        "MIT",
        "https://github.com/python-jsonschema/jsonschema/blob/main/LICENSE",
        "2026-08-15",
    ),
    # Т-502 (экстра orqion[agent]): PEP 639 License-Expression в колёсах,
    # который старый pip-licenses не читает. Лицензии проверены по текстам
    # LICENSE внутри колёс версии 1.2.11 / 4.2.0 / 1.1.0 / 0.4.4.
    "langgraph": (
        "MIT",
        "https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/LICENSE",
        "2026-09-03",
    ),
    "langgraph-checkpoint": (
        "MIT",
        "https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/LICENSE",
        "2026-09-03",
    ),
    "langgraph-prebuilt": (
        "MIT",
        "https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/LICENSE",
        "2026-09-03",
    ),
    "langgraph-sdk": (
        "MIT",
        "https://github.com/langchain-ai/langgraph/blob/main/libs/sdk-py/LICENSE",
        "2026-09-03",
    ),
    "lazy-object-proxy": (
        "MIT",
        "https://github.com/abarnert/lazy-object-proxy/blob/master/LICENSE",
        "2026-08-15",
    ),
    "librt": ("MIT", "PyPI metadata", "2026-08-15"),
    "mako": ("MIT", "https://github.com/sqlalchemy/mako/blob/main/LICENSE", "2026-08-15"),
    "markupsafe": (
        "BSD-3-Clause",
        "https://github.com/pallets/markupsafe/blob/main/LICENSE",
        "2026-08-15",
    ),
    "mypy": ("MIT", "https://github.com/python/mypy/blob/master/LICENSE", "2026-08-15"),
    "mypy_extensions": ("MIT", "https://github.com/python/mypy/blob/master/LICENSE", "2026-08-15"),
    "networkx": (
        "BSD-3-Clause",
        "https://github.com/networkx/networkx/blob/main/LICENSE",
        "2026-08-15",
    ),
    # Прямая зависимость (экстра orqion[graph], Т-505). PyPI PEP 639:
    # «BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0» — ядро BSD-3-Clause,
    # остальные лицензии вшитых вспомогательных файлов, все пермиссивные.
    "numpy": ("BSD-3-Clause", "https://github.com/numpy/numpy/blob/main/LICENSE.txt", "2026-08-30"),
    "openapi-spec-validator": (
        "Apache-2.0",
        "https://github.com/python-openapi/openapi-spec-validator/blob/main/LICENSE",
        "2026-08-15",
    ),
    # MPL-2.0 AND (Apache-2.0 OR MIT) по PEP 639; MPL-2.0 принят как
    # осознанное исключение — см. обоснование в шапке модуля (Т-502).
    "orjson": (
        "MPL-2.0 AND (Apache-2.0 OR MIT)",
        "https://github.com/ijl/orjson/blob/master/LICENSE-MPL",
        "2026-09-03",
    ),
    "packaging": (
        "Apache-2.0 OR BSD-3-Clause",
        "https://github.com/pypa/packaging/blob/main/LICENSE",
        "2026-08-15",
    ),
    "pillow": (
        "MIT-CMU",
        "https://github.com/python-pillow/Pillow/blob/main/LICENSE",
        "2026-08-15",
    ),
    "pluggy": ("MIT", "https://github.com/pytest-dev/pluggy/blob/main/LICENSE", "2026-08-15"),
    "prettytable": (
        "BSD-3-Clause",
        "https://github.com/jazzband/prettytable/blob/main/LICENSE",
        "2026-08-15",
    ),
    "prometheus_client": (
        "Apache-2.0",
        "https://github.com/prometheus/client_python/blob/main/LICENSE",
        "2026-08-15",
    ),
    "psycopg2-binary": (
        "LGPL-3.0 OR Python-2.0",
        "https://github.com/psycopg/psycopg2/blob/master/LICENSE",
        "2026-08-15",
    ),
    "pycparser": (
        "BSD-3-Clause",
        "https://github.com/eliben/pycparser/blob/main/LICENSE",
        "2026-08-15",
    ),
    "pydantic": ("MIT", "https://github.com/pydantic/pydantic/blob/main/LICENSE", "2026-08-15"),
    "pydantic_core": (
        "MIT",
        "https://github.com/pydantic/pydantic-core/blob/main/LICENSE",
        "2026-08-15",
    ),
    "pydantic-settings": (
        "MIT",
        "https://github.com/pydantic/pydantic-settings/blob/main/LICENSE",
        "2026-08-15",
    ),
    # Клиент протокола передачи контекста моделям (экстра orqion[mcp],
    # Т-503), транзитивная зависимость. В метаданных только PEP 639
    # License-Expression; текст «The MIT License (MIT), Copyright (c)
    # 2015-2022 José Padilla» проверен в файле LICENSE внутри колеса
    # 2.13.0.
    "pyjwt": (
        "MIT",
        "https://github.com/jpadilla/pyjwt/blob/master/LICENSE",
        "2026-09-03",
    ),
    "pygments": (
        "BSD-2-Clause",
        "https://github.com/pygments/pygments/blob/master/LICENSE",
        "2026-08-15",
    ),
    "pip": ("MIT", "https://github.com/pypa/pip/blob/main/LICENSE.txt", "2026-08-15"),
    "pyparsing": (
        "MIT",
        "https://github.com/pyparsing/pyparsing/blob/master/LICENSE",
        "2026-08-15",
    ),
    "pyyaml": (
        "MIT",
        "https://github.com/yaml/pyyaml/blob/main/LICENSE",
        "2026-08-22",
    ),
    "pytest": ("MIT", "https://github.com/pytest-dev/pytest/blob/main/LICENSE", "2026-08-15"),
    "pytest-asyncio": (
        "MIT",
        "https://github.com/pytest-dev/pytest-asyncio/blob/main/LICENSE",
        "2026-08-15",
    ),
    "referencing": (
        "MIT",
        "https://github.com/python-jsonschema/referencing/blob/main/LICENSE",
        "2026-08-15",
    ),
    "regex": ("Apache-2.0", "https://github.com/mrab-Rab/regex/blob/master/LICENSE", "2026-08-15"),
    "rpds-py": ("MIT", "https://github.com/cringescriptor/rpds-py/blob/main/LICENSE", "2026-08-15"),
    "ruff": ("MIT", "https://github.com/astral-sh/ruff/blob/main/LICENSE", "2026-08-15"),
    "rtree": ("MIT", "https://github.com/Toblerity/rtree/blob/main/LICENSE", "2026-08-15"),
    "setuptools": ("MIT", "https://github.com/pypa/setuptools/blob/main/LICENSE", "2026-08-15"),
    # Серверная часть SSE — транзитивная зависимость экстра orqion[mcp]
    # (Т-503). В метаданных только PEP 639 License-Expression; текст
    # BSD-3-Clause («Copyright © 2020, sysid») проверен в файле LICENSE
    # внутри колеса 3.4.10.
    "sse-starlette": (
        "BSD-3-Clause",
        "https://github.com/sysid/sse-starlette/blob/master/LICENSE",
        "2026-09-03",
    ),
    "starlette": (
        "BSD-3-Clause",
        "https://github.com/encode/starlette/blob/master/LICENSE",
        "2026-08-15",
    ),
    "tabulate": (
        "MIT",
        "https://github.com/astanin/python-tabulate/blob/main/LICENSE",
        "2026-08-15",
    ),
    "tomli": ("MIT", "https://github.com/hukkin/tomli/blob/master/LICENSE", "2026-08-15"),
    # Т-502 (транзитивно через langsmith дополнения orqion[agent]):
    # PEP 639 License-Expression, старый pip-licenses не читает.
    # Проверено по тексту LICENSE в колесе 0.10.4.
    "truststore": (
        "MIT",
        "https://github.com/pypa/truststore/blob/main/LICENSE",
        "2026-09-03",
    ),
    "typer": ("MIT", "https://github.com/fastapi/typer/blob/master/LICENSE", "2026-08-15"),
    "typing-inspection": (
        "MIT",
        "https://github.com/pydantic/typing-inspection/blob/main/LICENSE",
        "2026-08-15",
    ),
    "typing_extensions": (
        "Python-2.0",
        "https://github.com/python/typing_extensions/blob/main/LICENSE",
        "2026-08-15",
    ),
    "tzdata": ("Apache-2.0", "https://github.com/python/tzdata/blob/main/LICENSE", "2026-08-15"),
    "urllib3": ("MIT", "https://github.com/urllib3/urllib3/blob/main/LICENSE", "2026-08-15"),
    # Т-502 (транзитивно через langsmith дополнения orqion[agent]):
    # PEP 639 License-Expression, старый pip-licenses не читает.
    # Проверено по тексту LICENSE в колесе 0.17.0.
    "uuid_utils": (
        "BSD-3-Clause",
        "https://github.com/aminalaee/uuid-utils/blob/main/LICENSE",
        "2026-09-03",
    ),
    "wcwidth": ("MIT", "https://github.com/jquast/wcwidth/blob/main/LICENSE", "2026-08-15"),
    # Т-502 (транзитивно через langsmith дополнения orqion[agent]):
    # PEP 639 License-Expression, старый pip-licenses не читает.
    # Проверено по тексту LICENSE в колесе 16.1.1.
    "websockets": (
        "BSD-3-Clause",
        "https://github.com/python-websockets/websockets/blob/main/LICENSE",
        "2026-09-03",
    ),
    "werkzeug": (
        "BSD-3-Clause",
        "https://github.com/pallets/werkzeug/blob/main/LICENSE",
        "2026-08-15",
    ),
    "wrapt": (
        "BSD-2-Clause",
        "https://github.com/GrahamDumpleton/wrapt/blob/main/LICENSE",
        "2026-08-15",
    ),
    "xmltodict": (
        "MIT",
        "https://github.com/martinblech/xmltodict/blob/master/LICENSE",
        "2026-08-15",
    ),
    # Т-502 (транзитивно через langsmith дополнения orqion[agent]):
    # PEP 639 License-Expression, старый pip-licenses не читает.
    # Проверено по тексту LICENSE в колесе 0.25.0.
    "zstandard": (
        "BSD-3-Clause",
        "https://github.com/indygreg/python-zstandard/blob/main/LICENSE",
        "2026-09-03",
    ),
}
