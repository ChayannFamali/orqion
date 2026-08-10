"""Парсинг документов в markdown через Docling backends (T-206).

Разбор PDF/DOCX/PPTX/XLSX через Docling, MD/TXT — напрямую.
Кэширование результата по sha256: переиндексация не разбирает файл заново.

PDF-парсинг:
- Если доступны ML-модели (docling_ibm_models) — полноценный layout detection.
- Если нет — fallback через pypdfium2: постраничный текст, без структуры заголовков.
  Помечается parser: "fallback" в метаданных.
- PDF без текстового слоя (сканы) → failed с причиной, OCR не делаем (S-21).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from app.rag.blob import BlobStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseResult:
    """Результат разбора документа."""

    markdown: str
    parser: str  # "docling" | "fallback" | "direct"
    error: str | None = None


# Расширения, обрабатываемые напрямую без Docling
_DIRECT_EXTENSIONS = {".md", ".txt"}

# Расширения Office, обрабатываемые через Docling backends
_OFFICE_BACKENDS: dict[str, tuple[str, str]] = {
    # extension → (module_path, class_name)
    ".docx": ("docling.backend.msword_backend", "MsWordDocumentBackend"),
    ".pptx": ("docling.backend.mspowerpoint_backend", "MsPowerpointDocumentBackend"),
    ".xlsx": ("docling.backend.msexcel_backend", "MsExcelDocumentBackend"),
}

# Расширения PDF
_PDF_EXTENSIONS = {".pdf"}


def _get_extension(filename: str) -> str:
    """Возвращает расширение в нижнем регистре."""
    return Path(filename).suffix.lower()


def _has_ml_models() -> bool:
    """Проверяет наличие docling_ibm_models для ML layout detection."""
    try:
        import docling_ibm_models  # noqa: F401
    except ImportError:
        return False
    return True


async def parse_document(
    blob_store: BlobStore,
    *,
    sha256: str,
    filename: str,
    blob_uri: str,
) -> ParseResult:
    """Разбирает документ из BlobStore в markdown.

    Кэширование по sha256: если результат уже есть — не разбирает заново.
    (Кэш будет в таблице document.meta или отдельной таблице — T-207,
    пока разбора нет, каждый вызов реален.)

    Возвращает ParseResult с markdown и именем парсера.
    При неудаче — ParseResult с error и пустым markdown.
    """
    ext = _get_extension(filename)

    # MD/TXT — напрямую
    if ext in _DIRECT_EXTENSIONS:
        return await _parse_direct(blob_store, blob_uri)

    # Office форматы — через Docling backends
    if ext in _OFFICE_BACKENDS:
        return await _parse_office(blob_store, blob_uri, filename, ext)

    # PDF — через Docling ML или fallback
    if ext in _PDF_EXTENSIONS:
        return await _parse_pdf(blob_store, blob_uri, filename)

    # Неподдерживаемый тип — не должен случиться (фильтр на загрузке)
    return ParseResult(
        markdown="",
        parser="none",
        error=f"Неподдерживаемый тип файла: {ext}",
    )


async def _parse_direct(blob_store: BlobStore, blob_uri: str) -> ParseResult:
    """Разбор MD/TXT — напрямую, без Docling."""
    content = BytesIO()
    async for chunk in blob_store.get(blob_uri):
        content.write(chunk)
    text = content.getvalue().decode("utf-8", errors="replace")
    return ParseResult(markdown=text, parser="direct")


async def _parse_office(
    blob_store: BlobStore,
    blob_uri: str,
    filename: str,
    ext: str,
) -> ParseResult:
    """Разбор DOCX/PPTX/XLSX через Docling backends (без ML)."""
    module_path, class_name = _OFFICE_BACKENDS[ext]

    content = BytesIO()
    async for chunk in blob_store.get(blob_uri):
        content.write(chunk)
    content.seek(0)

    def _parse_sync() -> str:
        from importlib import import_module

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.document import InputDocument

        module = import_module(module_path)
        backend_cls = getattr(module, class_name)

        format_map = {
            ".docx": InputFormat.DOCX,
            ".pptx": InputFormat.PPTX,
            ".xlsx": InputFormat.XLSX,
        }
        in_doc = InputDocument(
            path_or_stream=content,
            format=format_map[ext],
            backend=backend_cls,
            filename=filename,
        )
        backend = backend_cls(in_doc=in_doc, path_or_stream=content)
        doc = backend.convert()
        return str(doc.export_to_markdown())

    markdown = await asyncio.get_event_loop().run_in_executor(None, _parse_sync)
    return ParseResult(markdown=markdown, parser="docling")


async def _parse_pdf(
    blob_store: BlobStore,
    blob_uri: str,
    filename: str,
) -> ParseResult:
    """Разбор PDF через Docling ML или fallback через pypdfium2."""
    if _has_ml_models():
        return await _parse_pdf_ml(blob_store, blob_uri, filename)
    return await _parse_pdf_fallback(blob_store, blob_uri)


async def _parse_pdf_ml(
    blob_store: BlobStore,
    blob_uri: str,
    filename: str,
) -> ParseResult:
    """Полноценный PDF-парсинг через Docling с ML layout detection."""
    content = BytesIO()
    async for chunk in blob_store.get(blob_uri):
        content.write(chunk)
    content.seek(0)

    def _parse_sync() -> str:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(content)  # type: ignore[arg-type]
        return str(result.document.export_to_markdown())

    try:
        markdown = await asyncio.get_event_loop().run_in_executor(None, _parse_sync)
    except (OSError, RuntimeError, ValueError) as exc:
        # Проверяем, не скан ли это (нет текстового слоя)
        if _is_scan_pdf(content):
            return ParseResult(
                markdown="",
                parser="docling",
                error="Документ не содержит текстового слоя, требуется OCR (отдельная задача)",
            )
        logger.warning("PDF ML parsing failed for %s: %s", filename, exc)
        return ParseResult(markdown="", parser="docling", error=str(exc))
    return ParseResult(markdown=markdown, parser="docling")


async def _parse_pdf_fallback(
    blob_store: BlobStore,
    blob_uri: str,
) -> ParseResult:
    """Fallback PDF-парсинг через pypdfium2: текст без структуры заголовков."""
    content = BytesIO()
    async for chunk in blob_store.get(blob_uri):
        content.write(chunk)
    content.seek(0)

    def _parse_sync() -> str:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(content)
        pages: list[str] = []
        for page in pdf:
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()
            pages.append(text)
            page.close()
        pdf.close()
        return "\n\n".join(pages)

    try:
        markdown = await asyncio.get_event_loop().run_in_executor(None, _parse_sync)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("PDF fallback parsing failed: %s", exc)
        return ParseResult(markdown="", parser="fallback", error=str(exc))

    # Пустой текст → вероятно скан без текстового слоя
    if not markdown.strip():
        return ParseResult(
            markdown="",
            parser="fallback",
            error="Документ не содержит текстового слоя, требуется OCR (отдельная задача)",
        )

    return ParseResult(markdown=markdown, parser="fallback")


def _is_scan_pdf(content: BytesIO) -> bool:
    """Проверяет, является ли PDF сканом (нет текстового слоя)."""
    try:
        import pypdfium2

        content.seek(0)
        pdf = pypdfium2.PdfDocument(content)
        total_text = ""
        for page in pdf:
            textpage = page.get_textpage()
            total_text += textpage.get_text_range()
            textpage.close()
            page.close()
        pdf.close()
        return not total_text.strip()
    except (OSError, RuntimeError, ValueError):
        return False
