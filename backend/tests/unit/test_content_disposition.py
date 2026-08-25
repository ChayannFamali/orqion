"""BUG-021: Content-Disposition с не-ASCII именами файлов (RFC 6266/5987).

Регрессия: голый filename="СОБЕСЕДОВАНИЕ…" не кодируется в latin-1 при
отправке ответа → 500 на «открыть документ». Хелпер обязан строить
заголовок, который всегда кодируется в latin-1, и передавать полное имя
через filename*=UTF-8''.
"""

from __future__ import annotations

from urllib.parse import unquote

from app.api.routes.documents import _inline_disposition


def test_ascii_filename_kept_verbatim() -> None:
    header = _inline_disposition("report.md")
    assert header == "inline; filename=\"report.md\"; filename*=UTF-8''report.md"
    header.encode("latin-1")


def test_cyrillic_filename_encoded_rfc5987() -> None:
    name = "СОБЕСЕДОВАНИЕ - 2026-08-15_10-58-34.md"
    header = _inline_disposition(name)
    # Заголовок обязан кодироваться в latin-1 (иначе 500 при отправке)
    header.encode("latin-1")
    assert "filename*=UTF-8''" in header
    # Полное имя восстанавливается из percent-кодировки
    encoded = header.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == name
    # Фолбэк сохраняет ASCII-хвост имени
    assert 'filename="2026-08-15_10-58-34.md"' in header


def test_pure_cyrillic_filename_falls_back_to_document() -> None:
    header = _inline_disposition("Отчётность")
    header.encode("latin-1")
    assert 'filename="document"' in header
    assert "filename*=UTF-8''" in header


def test_quotes_stripped_from_fallback() -> None:
    header = _inline_disposition('bad"name.txt')
    header.encode("latin-1")
    assert '"badname.txt"' in header


def test_cjk_and_emoji_safe() -> None:
    for name in ("文档.md", "📝 notes.txt", "файл 文档 😀.md"):
        header = _inline_disposition(name)
        header.encode("latin-1")
        assert "filename*=UTF-8''" in header
