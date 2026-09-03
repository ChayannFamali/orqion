"""Т-503: схемы реестра серверов протокола передачи контекста моделям.

Имя сервера становится неймспейсом его инструментов в едином реестре
(``<имя_сервера>.<имя_инструмента>``), поэтому формат имени проверяется
жёстко: строчные латинские буквы, цифры, дефис и подчёркивание, без
точки — коллизия с встроенными инструментами (без префикса) и между
серверами исключена построением (уточнение к решению 4 дизайн-ревью).
Транспорт — только HTTP к явному адресу (решение 1): допускаются схемы
``http`` и ``https``, локальные процессы не запускаются.

Секрет сервера (``api_key``) принимается на записи и никогда не
возвращается в ответах — по механизму ключей провайдеров (решение 3).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

NAME_MAX_LENGTH = 64

# Неймспейс инструмента строится как «имя_сервера.имя_инструмента»;
# точка в имени сервера сделала бы границу неймспейса неоднозначной.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _validate_server_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(
            "Имя сервера: строчные латинские буквы, цифры, дефис и "
            "подчёркивание; начинается с буквы; точка запрещена "
            "(имя становится неймспейсом инструментов)"
        )
    return name


def _validate_server_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            "Адрес сервера: только http:// или https:// с явным хостом "
            "(транспорт — HTTP к явному адресу, локальные процессы не запускаются)"
        )
    return url


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    url: str
    api_key: str | None = None
    enabled: bool = True

    _name = field_validator("name")(_validate_server_name)
    _url = field_validator("url")(_validate_server_url)


class McpServerUpdate(BaseModel):
    # Имя намеренно не обновляется: оно — неймспейс инструментов сервера
    # в едином реестре; переименование меняло бы имена инструментов,
    # видимые модели, и переименовывало бы записи аудита.
    url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_server_url(value)


class McpServerResponse(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool
    # Факт наличия секрета виден администратору; сам секрет не
    # возвращается (решение 3, по образцу ключей провайдеров).
    has_api_key: bool


class McpServerListResponse(BaseModel):
    servers: list[McpServerResponse]


class McpServerDeleteResponse(BaseModel):
    deleted: bool
