"""Единый обработчик доменных исключений.

Наружу не уходят стектрейсы, пути файловой системы и имена таблиц (AGENTS.md §14).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import OrqionError


async def orqion_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Преобразует доменную ошибку в JSON-ответ.

    Зарегистрирован только для OrqionError, поэтому isinstance-проверка
    выполняется всегда. Accepts Exception для совместимости с типом Starlette.
    """
    assert isinstance(exc, OrqionError)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error_code,
            "reason": exc.reason,
            "constraint": exc.constraint,
            "hint": exc.hint,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Регистрирует единый обработчик OrqionError."""
    app.add_exception_handler(OrqionError, orqion_exception_handler)
