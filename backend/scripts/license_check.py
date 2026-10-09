"""Проверка лицензий зависимостей: разрешены только пермиссивные.

Запускается в CI (job `licenses`) и локально тем же способом:

    PYTHONPATH=backend python -m scripts.license_check

Область проверки — все дистрибутивы, установленные в текущем окружении
(pip-licenses читает метаданные установленного дерева). В CI окружение
создаётся заново из объявленных extras, поэтому там проверяется ровно набор
зависимостей проекта; локальный `.venv` может содержать случайные остатки,
и они тоже будут проверены.

Почему это отдельный скрипт, а не инлайн-код в пайплайне: логику внутри YAML
нельзя прогнать локально, поэтому её расхождение с реальным деревом
зависимостей всплывает только после пуша. Здесь классификатор — чистая
функция `classify`, покрытая юнит-тестами, а запуск одинаков в CI и на
машине разработчика.

Политика (три разрешённых семейства — MIT, Apache, BSD — плюс точечные
осознанные исключения, каждое со своим обоснованием ниже):

* `OR` — достаточно одного пермиссивного варианта: двойное лицензирование
  означает право выбрать его, поэтому `Apache-2.0 OR GPL-2.0-or-later`
  разрешено (берём Apache-2.0).
* `AND` — разрешено, только если разрешены все части: от копилефта в
  conjunction отказаться нельзя, поэтому `GPL-2.0-only AND MIT` запрещено.
* `WITH` — оценивается базовая лицензия, исключение её не ужесточает.

Строки, которые не разбираются как SPDX-выражение (названия из trove
классификаторов, свободный текст), проходят эвристическую проверку по
целым словам.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from scripts.license_allowlist import ALLOWED_UNKNOWN_VERIFIED

Status = Literal["allowed", "banned", "unknown"]

#: Собственный пакет проекта: его лицензия — BUSL-1.1 (см. LICENSE),
#: проверка относится к зависимостям, а не к нам самим.
SELF_PACKAGE = "orqion"

#: Разрешённые идентификаторы SPDX. Три основных семейства — MIT, Apache,
#: BSD; всё остальное добавлено точечно и осознанно:
#:
#: * `mpl-2.0` — слабый файловый копилефт: обязательства ограничены
#:   собственными файлами пакета и не распространяются на код проекта, в
#:   отличие от GPL/AGPL/SSPL/BUSL, которые запрещены. Пакеты под MPL-2.0
#:   (certifi, orjson) приходят транзитивно и неустранимы без отказа от
#:   разрешённых вышестоящих зависимостей.
#: * `cnri-python` — OSI-одобренная пермиссивная лицензия (у regex),
#:   отличается от BSD лишь оговоркой о месте разрешения споров.
#: * `mit-0`, `mit-cmu`, `0bsd`, `cc0-1.0`, `unlicense`, `zlib` — варианты
#:   общественного достояния и ослабленного MIT, обязательств не накладывают.
PERMISSIVE = frozenset(
    {
        "0bsd",
        "apache-1.1",
        "apache-2.0",
        "bsd",
        "bsd-2-clause",
        "bsd-3-clause",
        "bsd-3-clause-clear",
        "cc0-1.0",
        "cnri-python",
        "hpnd",
        "isc",
        "mit",
        "mit-0",
        "mit-cmu",
        "mpl-2.0",
        "psf-2.0",
        "python-2.0",
        "unlicense",
        "zlib",
        "zpl-2.1",
    }
)

#: Префиксы идентификаторов, которые запрещены всегда: сильный копилефт и
#: лицензии, ограничивающие использование. `lgpl` не открывается как
#: категория — он разрешён только внутри `OR` с пермиссивным вариантом.
COPYLEFT_PREFIXES = (
    "agpl",
    "busl",
    "cddl",
    "cpal",
    "epl",
    "gpl",
    "ipl",
    "lgpl",
    "npl",
    "osl",
    "qpl",
    "sleepycat",
    "sspl",
    "watcom",
)

#: Маркеры копилефта для строк, которые не разбираются как SPDX.
_COPYLEFT_RE = re.compile(
    r"\b(?:gpl|lgpl|agpl|sspl|busl)\b"
    r"|gnu (?:general public|lesser general public|library or lesser general public) license"
    r"|server side public license"
    r"|business source license"
    r"|sustainable use license"
    r"|copyleft",
)

#: Маркеры пермиссивных лицензий для тех же строк. Совпадение ищется по
#: границам слова, чтобы `limited` из текста MIT не читалось как `mit`.
_PERMISSIVE_RE = re.compile(
    r"\b(?:mit|bsd|apache|isc|zlib|unlicense|mit-0|mit-cmu|0bsd|psf-2\.0|python-2\.0)\b"
    r"|python software foundation license"
    r"|mozilla public license"
    r"|dfsg approved",
)

#: Значения, которые pip-licenses отдаёт, когда лицензию определить не удалось.
_UNRECOGNIZED = frozenset({"", "none", "unknown", "n/a", "no license"})

_OPERATORS = frozenset({"and", "or", "with"})
_RESERVED = _OPERATORS | frozenset({"(", ")"})
_SEPARATORS = frozenset({",", ";"})
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.+-]*")


@dataclass(frozen=True)
class Verdict:
    """Решение по одной лицензии."""

    status: Status
    detail: str

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


@dataclass(frozen=True)
class _Node:
    """Узел разобранного SPDX-выражения."""

    kind: Literal["leaf", "and", "or"]
    leaf: str = ""
    children: tuple[_Node, ...] = ()


def _normalize(raw: str) -> str:
    """Строка лицензии к нижнему регистру с пробелами вместо переносов."""
    return re.sub(r"\s+", " ", raw.strip().lower())


def _is_copyleft(leaf: str) -> bool:
    return leaf.startswith(COPYLEFT_PREFIXES)


def _tokenize(text: str) -> list[str] | None:
    """Разбивает SPDX-выражение на токены.

    Возвращает `None`, если строка не похожа на выражение: в ней есть
    нелицензионные слова, незакрытые скобки или неизвестный оператор.
    Разделители `,` и `;` трактуются как `AND` — перечисление лицензий
    означает соблюдение всех их. Идентификатор исключения после `WITH`
    лицензией не является и принимается как есть.
    """
    tokens: list[str] = []
    position = 0
    depth = 0
    exception_expected = False
    while position < len(text):
        char = text[position]
        if char in "()":
            depth += 1 if char == "(" else -1
            if depth < 0:
                return None
            tokens.append(char)
            exception_expected = False
            position += 1
            continue
        if char in _SEPARATORS:
            tokens.append("and")
            exception_expected = False
            position += 1
            continue
        match = _WORD_RE.match(text, position)
        if match is None:
            if char.isspace():
                position += 1
                continue
            return None
        word = match.group(0)
        known = word in _OPERATORS or word in PERMISSIVE or _is_copyleft(word)
        if not known and not exception_expected:
            return None
        tokens.append(word)
        exception_expected = word == "with"
        position = match.end()
    return tokens if depth == 0 and tokens else None


def _parse(tokens: list[str]) -> _Node | None:
    """Рекурсивный спуск: `OR` слабее `AND`, скобки задают приоритет."""
    position = 0

    def parse_or() -> _Node | None:
        nonlocal position
        left = parse_and()
        if left is None:
            return None
        branches = [left]
        while position < len(tokens) and tokens[position] == "or":
            position += 1
            right = parse_and()
            if right is None:
                return None
            branches.append(right)
        return branches[0] if len(branches) == 1 else _Node("or", children=tuple(branches))

    def parse_and() -> _Node | None:
        nonlocal position
        left = parse_primary()
        if left is None:
            return None
        parts = [left]
        while position < len(tokens) and tokens[position] == "and":
            position += 1
            right = parse_primary()
            if right is None:
                return None
            parts.append(right)
        return parts[0] if len(parts) == 1 else _Node("and", children=tuple(parts))

    def parse_primary() -> _Node | None:
        nonlocal position
        if position >= len(tokens):
            return None
        token = tokens[position]
        if token == "(":
            position += 1
            inner = parse_or()
            if inner is None or position >= len(tokens) or tokens[position] != ")":
                return None
            position += 1
            return inner
        if token in _RESERVED:
            return None
        position += 1
        node = _Node("leaf", leaf=token)
        if position < len(tokens) and tokens[position] == "with":
            position += 1
            if position >= len(tokens) or tokens[position] in _RESERVED:
                return None
            position += 1  # идентификатор исключения не меняет оценку базы
        return node

    result = parse_or()
    return result if result is not None and position == len(tokens) else None


def _evaluate(node: _Node) -> Status:
    """Оценка выражения: `or` — достаточно одного, `and` — нужны все."""
    if node.kind == "leaf":
        if node.leaf in PERMISSIVE:
            return "allowed"
        return "banned" if _is_copyleft(node.leaf) else "unknown"
    statuses = [_evaluate(child) for child in node.children]
    if node.kind == "or":
        if "allowed" in statuses:
            return "allowed"
        return "banned" if "banned" in statuses else "unknown"
    if "banned" in statuses:
        return "banned"
    return "unknown" if "unknown" in statuses else "allowed"


def classify(raw: str) -> Verdict:
    """Классифицирует строку лицензии из вывода pip-licenses."""
    text = _normalize(raw)
    if text in _UNRECOGNIZED:
        return Verdict("unknown", "лицензия не определена инструментом")

    tokens = _tokenize(text)
    if tokens is not None:
        node = _parse(tokens)
        if node is not None:
            status = _evaluate(node)
            if status != "unknown":
                return Verdict(status, text)

    # Строка не является SPDX-выражением: название из trove классификаторов
    # или свободный текст. Копилефт запрещён, если нет пермиссивной ветки.
    if _COPYLEFT_RE.search(text):
        return Verdict("banned", text)
    if _PERMISSIVE_RE.search(text):
        return Verdict("allowed", text)
    return Verdict("unknown", text)


def _packages() -> list[dict[str, str]]:
    """Вывод pip-licenses по установленному дереву (те же флаги, что в CI)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--from",
            "mixed",
            "--format",
            "json",
            "--with-system",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("pip-licenses failed:", result.stderr.strip(), file=sys.stderr)
        raise SystemExit(1)
    loaded: list[dict[str, str]] = json.loads(result.stdout)
    return loaded


def check() -> int:
    """Проверяет дерево зависимостей. Возвращает код возврата процесса."""
    packages = _packages()
    banned: list[str] = []
    unknown: list[str] = []
    rescued: list[str] = []

    for package in packages:
        name = (package.get("Name") or "").strip()
        if name.lower() == SELF_PACKAGE:
            continue
        raw = package.get("License") or ""
        verdict = classify(raw)
        if verdict.status == "unknown":
            record = ALLOWED_UNKNOWN_VERIFIED.get(name.lower())
            if record is not None:
                # Ручная запись не обходит политику: провер её так же строго.
                recorded = classify(record[0])
                if recorded.status == "unknown":
                    unknown.append(f"{name}: не определена ни инструментом, ни записью")
                    continue
                if recorded.status == "banned":
                    banned.append(f"{name}: {record[0]} (из allowlist)")
                    continue
                rescued.append(f"{name}: {record[0]}")
                continue
            unknown.append(f"{name}: {raw or '(пусто)'}")
            continue
        if verdict.status == "banned":
            banned.append(f"{name}: {verdict.detail}")

    if rescued:
        print("ЛИЦЕНЗИЯ ВЗЯТА ИЗ ALLOWLIST (инструмент её не определил):")
        for item in rescued:
            print(f"  - {item}")
    if unknown:
        print(
            "PACKAGES WITH UNKNOWN LICENSE "
            "(проверь лицензию по тексту LICENSE в upstream и добавь запись "
            "в backend/scripts/license_allowlist.py):"
        )
        for item in unknown:
            print(f"  - {item}")
    if banned:
        print("BANNED LICENSES FOUND:")
        for item in banned:
            print(f"  - {item}")
        print(
            "\nПроверка охватывает все установленные дистрибутивы, а не только объявленные\n"
            "зависимости. Если пакет попал в окружение случайно, это видно по пустому\n"
            "`Required-by` в `pip show <имя>` — в дереве, которое ставит CI из объявленных\n"
            "extras, такого пакета нет."
        )
    if unknown or banned:
        return 1

    print(f"All {len(packages)} packages passed license check")
    return 0


def main() -> None:
    raise SystemExit(check())


if __name__ == "__main__":
    main()
