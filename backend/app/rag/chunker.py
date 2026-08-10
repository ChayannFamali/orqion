"""Чанкинг документов по заголовкам (T-207, S-22).

Разбивает markdown на чанки по границам заголовков, целевой размер 400–800 токенов,
перекрытие 10–15%. Путь заголовков — в метаданных каждого чанка.
Таблицы не разрываются — выносятся целиком отдельным чанком.

Для fallback-документов (PDF без ML, нет заголовков) — путь заголовков пустой
с явной пометкой heading_path_source: "none".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import tiktoken

CHUNKER_VERSION = "1.0"

# Целевые размеры в токенах
MIN_TOKENS = 400
MAX_TOKENS = 800
OVERLAP_RATIO = 0.125  # 12.5% — середина 10–15%


@dataclass(frozen=True)
class DocChunk:
    """Чанк документа."""

    text: str
    ordinal: int
    meta: dict[str, object] = field(default_factory=dict)


# Регулярные выражения для разбора markdown
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_TABLE_RE = re.compile(r"^\|.+\|$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$", re.MULTILINE)


def _count_tokens(text: str, encoder: tiktoken.Encoding) -> int:
    """Считает количество токенов в тексте."""
    return len(encoder.encode(text))


def _split_by_headings(markdown: str) -> list[tuple[list[str], str]]:
    """Разбивает markdown по заголовкам.

    Возвращает список (heading_path, section_text).
    heading_path — список заголовков от корня до текущего уровня.
    """
    lines = markdown.split("\n")
    sections: list[tuple[list[str], str]] = []

    # Текущий путь заголовков по уровням
    current_path: list[str] = []
    current_level = 0
    current_lines: list[str] = []

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            # Сохраняем предыдущую секцию
            if current_lines:
                sections.append((list(current_path), "\n".join(current_lines)))

            level = len(match.group(1))
            title = match.group(2).strip()

            # Обновляем путь заголовков
            if level <= current_level:
                # Откатываемся до нужного уровня
                current_path = current_path[: level - 1]
            current_path = current_path[: level - 1] + [title]
            current_level = level
            current_lines = [line]
        else:
            current_lines.append(line)

    # Последняя секция
    if current_lines:
        sections.append((list(current_path), "\n".join(current_lines)))

    return sections


def _is_table_block(text: str) -> bool:
    """Проверяет, является ли текст блоком таблицы markdown."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return False
    return bool(_TABLE_RE.match(lines[0])) and bool(_TABLE_SEPARATOR_RE.match(lines[1]))


def _extract_tables(text: str) -> tuple[list[str], list[str]]:
    """Выделяет таблицы из текста.

    Возвращает (table_blocks, text_blocks).
    table_blocks — строки таблиц (целиком).
    text_blocks — оставшийся текст без таблиц.
    """
    lines = text.split("\n")
    table_lines: list[str] = []
    other_lines: list[str] = []
    i = 0
    while i < len(lines):
        # Проверяем, начинается ли таблица с этой строки
        if (
            i + 1 < len(lines)
            and _TABLE_RE.match(lines[i])
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            # Собираем весь блок таблицы
            block = [lines[i], lines[i + 1]]
            i += 2
            while i < len(lines) and _TABLE_RE.match(lines[i]):
                block.append(lines[i])
                i += 1
            table_lines.append("\n".join(block))
        else:
            other_lines.append(lines[i])
            i += 1

    return table_lines, ["\n".join(other_lines)] if other_lines else []


def _split_section(
    section_text: str,
    heading_path: list[str],
    encoder: tiktoken.Encoding,
    heading_path_source: str,
) -> list[DocChunk]:
    """Разбивает секцию на чанки по размеру.

    Если секция меньше MIN_TOKENS — один чанк.
    Если больше MAX_TOKENS — разбивается по параграфам с перекрытием.
    Таблицы выделяются отдельными чанками, не разрываются.
    Таблица крупнее MAX_TOKENS остаётся одним чанком целиком —
    осознанный компромисс: целостность таблицы важнее равномерности размера
    (аналог ADR-9 для кода: функция/класс целиком).
    """
    # Выделение таблиц из секции — таблицы отдельными чанками
    table_blocks, text_blocks = _extract_tables(section_text)

    chunks: list[DocChunk] = []
    for table_text in table_blocks:
        chunks.append(
            DocChunk(
                text=table_text,
                ordinal=0,
                meta={
                    "heading_path": heading_path,
                    "heading_path_source": heading_path_source,
                    "chunker_version": CHUNKER_VERSION,
                    "is_table": True,
                },
            )
        )

    # Оставшийся текст (без таблиц)
    remaining_text = "\n\n".join(text_blocks)
    if not remaining_text.strip():
        return chunks

    token_count = _count_tokens(remaining_text, encoder)

    # Секция в пределах лимита — один чанк
    if token_count <= MAX_TOKENS:
        chunks.append(
            DocChunk(
                text=remaining_text.strip(),
                ordinal=0,
                meta={
                    "heading_path": heading_path,
                    "heading_path_source": heading_path_source,
                    "chunker_version": CHUNKER_VERSION,
                },
            )
        )
        return chunks

    # Секция больше лимита — разбиваем по параграфам
    paragraphs = _split_by_paragraphs(remaining_text)
    current_text = ""
    current_tokens = 0
    overlap_tokens = int(MIN_TOKENS * OVERLAP_RATIO)
    ordinal = 0

    for para in paragraphs:
        para_tokens = _count_tokens(para, encoder)

        # Если параграф сам по себе больше MAX_TOKENS — разбиваем жёстко
        if para_tokens > MAX_TOKENS:
            if current_text:
                chunks.append(
                    DocChunk(
                        text=current_text.strip(),
                        ordinal=ordinal,
                        meta={
                            "heading_path": heading_path,
                            "heading_path_source": heading_path_source,
                            "chunker_version": CHUNKER_VERSION,
                        },
                    )
                )
                ordinal += 1
                current_text = ""
                current_tokens = 0

            # Жёсткое разбиение большого параграфа
            hard_chunks = _hard_split(para, encoder)
            for hc in hard_chunks:
                chunks.append(
                    DocChunk(
                        text=hc,
                        ordinal=ordinal,
                        meta={
                            "heading_path": heading_path,
                            "heading_path_source": heading_path_source,
                            "chunker_version": CHUNKER_VERSION,
                        },
                    ),
                )
                ordinal += 1
            continue

        if current_tokens + para_tokens > MAX_TOKENS and current_text:
            # Сохраняем текущий чанк
            chunks.append(
                DocChunk(
                    text=current_text.strip(),
                    ordinal=ordinal,
                    meta={
                        "heading_path": heading_path,
                        "heading_path_source": heading_path_source,
                        "chunker_version": CHUNKER_VERSION,
                    },
                )
            )
            ordinal += 1

            # Перекрытие: берём последние overlap_tokens из current_text
            overlap_text = _take_last_tokens(current_text, overlap_tokens, encoder)
            current_text = overlap_text + "\n\n" + para
            current_tokens = _count_tokens(current_text, encoder)
        else:
            if current_text:
                current_text += "\n\n" + para
            else:
                current_text = para
            current_tokens += para_tokens

    if current_text:
        chunks.append(
            DocChunk(
                text=current_text.strip(),
                ordinal=ordinal,
                meta={
                    "heading_path": heading_path,
                    "heading_path_source": heading_path_source,
                    "chunker_version": CHUNKER_VERSION,
                },
            )
        )

    return chunks


def _split_by_paragraphs(text: str) -> list[str]:
    """Разбивает текст по параграфам (двойной перевод строки)."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _hard_split(text: str, encoder: tiktoken.Encoding) -> list[str]:
    """Жёсткое разбиение по предложениям, если параграф больше MAX_TOKENS."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if _count_tokens(current + " " + sent, encoder) > MAX_TOKENS and current:
            chunks.append(current.strip())
            current = sent
        else:
            current = (current + " " + sent).strip() if current else sent

    if current:
        chunks.append(current.strip())

    return chunks


def _take_last_tokens(
    text: str,
    n_tokens: int,
    encoder: tiktoken.Encoding,
) -> str:
    """Возвращает последние n_tokens токенов текста как строку."""
    tokens = encoder.encode(text)
    if len(tokens) <= n_tokens:
        return text
    return encoder.decode(tokens[-n_tokens:])


def chunk_document(
    markdown: str,
    *,
    parser: str = "docling",
) -> list[DocChunk]:
    """Разбивает markdown документ на чанки.

    Args:
        markdown: текст документа в markdown.
        parser: имя парсера, создавшего markdown ("docling" | "fallback" | "direct").

    Returns:
        Список чанков с путями заголовков и метаданными.
    """
    encoder = tiktoken.encoding_for_model("gpt-4")

    sections = _split_by_headings(markdown)

    # Если заголовков нет (fallback, plain .txt без #) — весь документ одна секция
    has_headings = any(path for path, _ in sections)
    if not sections or (len(sections) == 1 and not sections[0][0]):
        sections = [([], markdown)]

    # heading_path_source: "none" если нет реальных заголовков в тексте,
    # независимо от парсера (fallback по определению без заголовков,
    # direct без # — тоже без заголовков)
    heading_path_source = "markdown" if has_headings else "none"

    all_chunks: list[DocChunk] = []
    global_ordinal = 0

    for heading_path, section_text in sections:
        if not section_text.strip():
            continue

        section_chunks = _split_section(
            section_text,
            heading_path,
            encoder,
            heading_path_source,
        )

        for chunk in section_chunks:
            all_chunks.append(
                DocChunk(
                    text=chunk.text,
                    ordinal=global_ordinal,
                    meta=chunk.meta,
                )
            )
            global_ordinal += 1

    return all_chunks
