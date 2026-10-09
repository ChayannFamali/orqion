"""Тесты классификатора лицензий (CI job `licenses`).

Строки в parametrized наборах — фактический вывод pip-licenses 5.5.5 по
дереву зависимостей проекта, снятый 2026-10-09; к ним добавлены
враждебные случаи, которых в дереве нет.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from scripts import license_check
from scripts.license_check import Verdict, classify

InstallPackages = Callable[[list[tuple[str, str]]], None]
InstallAllowlist = Callable[[dict[str, tuple[str, str, str]]], None]

# Фактические строки из дерева: SPDX-идентификаторы и их комбинации.
REAL_SPDX_ALLOWED = [
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "BSD",
    "MIT-0",
    "MIT-CMU",
    "PSF-2.0",
    "Apache-2.0 AND MIT",
    "Apache-2.0 AND BSD-2-Clause",
    "Apache-2.0 AND CNRI-Python",
    "MIT AND PSF-2.0",
    "MPL-2.0 AND MIT",
    "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
    "Apache-2.0 OR MIT",
    "Apache-2.0 OR BSD-3-Clause",
    "Apache-2.0 OR BSD-2-Clause",
    # orjson: MPL-2.0 принят как осознанное исключение, вторая часть — OR.
    "MPL-2.0 AND (Apache-2.0 OR MIT)",
    # dulwich: двойное лицензирование, выбираем пермиссивный вариант.
    # Именно эта строка роняла CI при подстрочной проверке на «GPL».
    "Apache-2.0 OR GPL-2.0-or-later",
    # psycopg2-binary: запись из allowlist, LGPL разрешён только через OR.
    "LGPL-3.0 OR Python-2.0",
]

# Фактические строки из дерева: названия trove-классификаторов и свободный текст.
REAL_LEGACY_ALLOWED = [
    "MIT License",
    "BSD License",
    "Apache Software License",
    "Apache License 2.0",
    "Apache 2.0 License",
    "Python Software Foundation License",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "ISC License (ISCL)",
    "DFSG approved; MIT License",
    "Apache Software License; BSD License",
    "Apache Software License; MIT License",
    "BSD-3-Clause, Apache-2.0, dependency licenses",
    "MIT License, Apache License, Version 2.0",
]

REAL_BANNED = [
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "AGPL-3.0-only",
    "LGPL-3.0-only",
    "SSPL-1.0",
    "BUSL-1.1",
    # Копилефт в conjunction обязателен — запрещён, несмотря на MIT рядом.
    "GPL-2.0-only AND MIT",
    "GNU Library or Lesser General Public License (LGPL)",
    "GNU General Public License v3 (GPLv3)",
    "Sustainable Use License",
]

REAL_UNKNOWN = ["UNKNOWN", "", "None", "unknown", "N/A"]


@pytest.mark.parametrize("raw", REAL_SPDX_ALLOWED)
def test_real_spdx_strings_allowed(raw: str) -> None:
    assert classify(raw).status == "allowed"


@pytest.mark.parametrize("raw", REAL_LEGACY_ALLOWED)
def test_real_legacy_strings_allowed(raw: str) -> None:
    assert classify(raw).status == "allowed"


@pytest.mark.parametrize("raw", REAL_BANNED)
def test_copyleft_banned(raw: str) -> None:
    assert classify(raw).status == "banned"


@pytest.mark.parametrize("raw", REAL_UNKNOWN)
def test_unrecognized_is_unknown(raw: str) -> None:
    assert classify(raw).status == "unknown"


def test_or_branch_cannot_escape_and() -> None:
    """Скобки не дают пермиссивной OR-ветке отменить обязательный копилефт.

    Наивное разбиение по «or» дало бы здесь allowed: ветка `apache-2.0`
    выглядит пермиссивной. Но GPL обязателен в conjunction, поэтому верно
    banned.
    """
    assert classify("GPL-3.0-only AND (MIT OR Apache-2.0)").status == "banned"


def test_or_with_copyleft_only_is_banned() -> None:
    """OR без пермиссивной альтернативы выбора не даёт."""
    assert classify("GPL-3.0-only OR AGPL-3.0-only").status == "banned"


def test_word_boundary_not_substring() -> None:
    """`limited` из текста MIT не читается как `mit`.

    Иначе любой копилефтный текст со словом «LIMITED» прошёл бы проверку.
    """
    copyleft_with_limited = (
        "GNU GENERAL PUBLIC LICENSE Version 3. "
        "THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A "
        "PARTICULAR PURPOSE ARE LIMITED TO."
    )
    assert classify(copyleft_with_limited).status == "banned"


def test_full_mit_text_allowed() -> None:
    """Полный текст MIT из metadанных (jiter) — разрешён."""
    text = (
        "MIT License\n\nCopyright (c) 2022 OpenAI, Shantanu Jain\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        'of this software and associated documentation files (the "Software"), to deal\n'
        "in the Software without restriction, including without limitation the rights\n"
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.\n'
    )
    assert classify(text).status == "allowed"


def test_with_exception_evaluates_base() -> None:
    assert classify("Apache-2.0 WITH LLVM-exception").status == "allowed"
    assert classify("GPL-3.0-only WITH GCC-exception-3.1").status == "banned"


@pytest.mark.parametrize("raw", ["mit", "MIT", "Mit License", "  Apache-2.0  "])
def test_case_and_whitespace_insensitive(raw: str) -> None:
    assert classify(raw).status == "allowed"


def test_verdict_allowed_flag() -> None:
    assert classify("MIT").allowed is True
    assert classify("GPL-3.0-only").allowed is False
    assert isinstance(classify("MIT"), Verdict)


def _fake_packages(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"Name": name, "Version": "1.0", "License": lic} for name, lic in rows]


@pytest.fixture
def packages(monkeypatch: pytest.MonkeyPatch) -> InstallPackages:
    """Замена вывода pip-licenses на фиксированный набор пакетов."""

    def _install(rows: list[tuple[str, str]]) -> None:
        def _stub() -> list[dict[str, str]]:
            return _fake_packages(rows)

        monkeypatch.setattr(license_check, "_packages", _stub)

    return _install


@pytest.fixture
def allowlist(monkeypatch: pytest.MonkeyPatch) -> InstallAllowlist:
    """Подмена allowlist с восстановлением после теста."""

    def _install(entries: dict[str, tuple[str, str, str]]) -> None:
        monkeypatch.setattr(license_check, "ALLOWED_UNKNOWN_VERIFIED", entries)

    return _install


def test_check_passes_on_clean_tree(
    packages: InstallPackages,
    allowlist: InstallAllowlist,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packages([("requests", "Apache-2.0"), ("click", "BSD-3-Clause")])
    allowlist({})
    assert license_check.check() == 0
    assert "All 2 packages passed license check" in capsys.readouterr().out


def test_check_skips_self_package_even_though_busl(
    packages: InstallPackages, allowlist: InstallAllowlist
) -> None:
    """Собственный пакет проекта — BUSL-1.1, проверка его не касается."""
    packages([("orqion", "BUSL-1.1"), ("orqion", "BUSL-1.1"), ("requests", "MIT")])
    allowlist({})
    assert license_check.check() == 0


def test_check_fails_on_unknown_without_record(
    packages: InstallPackages,
    allowlist: InstallAllowlist,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packages([("mystery", "UNKNOWN")])
    allowlist({})
    assert license_check.check() == 1
    out = capsys.readouterr().out
    assert "PACKAGES WITH UNKNOWN LICENSE" in out
    assert "mystery" in out


def test_check_fails_on_banned(
    packages: InstallPackages,
    allowlist: InstallAllowlist,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packages([("badpkg", "AGPL-3.0-only")])
    allowlist({})
    assert license_check.check() == 1
    out = capsys.readouterr().out
    assert "BANNED LICENSES FOUND" in out
    assert "badpkg: agpl-3.0-only" in out


def test_allowlist_record_is_policy_checked_not_bypassed(
    packages: InstallPackages,
    allowlist: InstallAllowlist,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Запись в allowlist не обходит политику: копилефт в ней всё равно запрещён.

    До перевода проверки в скрипт allowlist сопоставлялся по имени и
    разрешал пакет с любой лицензией — в том числе записанной ошибочно.
    """
    packages([("sneaky", "UNKNOWN")])
    allowlist({"sneaky": ("GPL-3.0-only", "https://example.invalid/LICENSE", "2026-10-09")})
    assert license_check.check() == 1
    assert "GPL-3.0-only (из allowlist)" in capsys.readouterr().out


def test_allowlist_record_rescues_only_unknown(
    packages: InstallPackages,
    allowlist: InstallAllowlist,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packages([("dual", "UNKNOWN")])
    allowlist(
        {
            "dual": (
                "Apache-2.0 OR GPL-2.0-or-later",
                "https://example.invalid/COPYING",
                "2026-10-09",
            )
        }
    )
    assert license_check.check() == 0
    assert "ЛИЦЕНЗИЯ ВЗЯТА ИЗ ALLOWLIST" in capsys.readouterr().out


def test_allowlist_does_not_override_classified_license(
    packages: InstallPackages, allowlist: InstallAllowlist
) -> None:
    """Если инструмент лицензию определил, запись allowlist не применяется."""
    packages([("stale", "AGPL-3.0-only")])
    allowlist({"stale": ("MIT", "https://example.invalid/LICENSE", "2020-01-01")})
    assert license_check.check() == 1
