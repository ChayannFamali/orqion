"""Allowlist пакетов с UNKNOWN лицензией для CI license check (TD-1).

pip-licenses не находит trove classifiers или metadata License field для этих
пакетов (PEP 639 License-Expression, нестандартный формат). Каждый пакет вручную
проверен против LICENSE file в upstream репозитории.

Формат: имя_пакета → (лицензия, источник, дата_проверки)
При добавлении новой зависимости с UNKNOWN лицензией — добавь запись сюда
с указанием источника проверки.

Список генерируется для ALLOWED_UNKNOWN в .github/workflows/ci.yml.
При обновлении версии пакета — перепроверь лицензию.
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
    "numpy": ("BSD-3-Clause", "https://github.com/numpy/numpy/blob/main/LICENSE.txt", "2026-08-15"),
    "openapi-spec-validator": (
        "Apache-2.0",
        "https://github.com/python-openapi/openapi-spec-validator/blob/main/LICENSE",
        "2026-08-15",
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
    "wcwidth": ("MIT", "https://github.com/jquast/wcwidth/blob/main/LICENSE", "2026-08-15"),
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
}
