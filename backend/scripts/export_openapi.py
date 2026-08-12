"""Экспорт OpenAPI-схемы в frontend/openapi.json.

Использование:
    python -m backend.scripts.export_openapi
    # или из корня проекта:
    python backend/scripts/export_openapi.py

Не требует запущенного сервера — импортирует create_app() напрямую.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def export_openapi(output_path: Path | None = None) -> Path:
    """Записывает OpenAPI-схему в JSON-файл, возвращает путь."""
    # Добавляем backend/ в sys.path, чтобы `import app.main` работал
    # независимо от того, запущен ли скрипт как модуль или как файл.
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.main import create_app

    app = create_app()
    schema = app.openapi()

    if output_path is None:
        output_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "openapi.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return output_path


if __name__ == "__main__":
    path = export_openapi()
    print(f"OpenAPI schema exported to {path}")
