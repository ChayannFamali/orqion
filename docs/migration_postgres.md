# Миграция на PostgreSQL

Это пользовательская документация, часть продукта. Описывает переход с SQLite
на PostgreSQL как реляционное хранилище orqion.

## Архитектура: split stack (Вариант A)

Профиль `standard` (arch.md §10.1) поддерживает PostgreSQL для реляционных данных.
Векторный store — отдельная ось выбора (N-6):

| Компонент | SQLite (minimal) | PostgreSQL (standard) |
|---|---|---|
| Реляционная БД | SQLite (основной файл) | PostgreSQL |
| Векторный store | sqlite-vec (отдельный `./data/vec.db`) | sqlite-vec (отдельный `./data/vec.db`) |
| Альтернатива | — | Qdrant (`vector_store=qdrant`) |

Ключевой момент: **векторный store всегда использует отдельный SQLite-файл**
(для `sqlite-vec`) или Qdrant — независимо от `database_url`. PostgreSQL
хранит только реляционные таблицы. Это split stack: две независимые оси.

## Переход

### 1. Установка PostgreSQL-зависимостей

```bash
pip install -e ".[postgres]"
```

Это установит `asyncpg` — async-драйвер PostgreSQL для SQLAlchemy.

### 2. Настройка

```bash
export ORQION_DATABASE_URL="postgresql://user:password@localhost:5432/orqion"
```

`orqion` автоматически преобразует `postgresql://` в `postgresql+asyncpg://`
(см. `app/db/engine.py:create_engine`).

### 3. Миграция схемы

```bash
orqion migrate
```

Выполняет `alembic upgrade head` на пустой PostgreSQL. Создаёт все таблицы.

**Внимание:** миграция 0013 (FTS5/vec_chunk_map) пропускается на PostgreSQL
(dialect guard, BUG-005). FTS5 и vec0 живут в отдельном SQLite-файле
векторного store, не в основной БД.

### 4. Перенос данных (если уже используете SQLite)

```bash
python backend/scripts/migrate_sqlite_to_postgres.py \
  --source "sqlite:///./orqion.db" \
  --dest "postgresql://user:password@localhost:5432/orqion"
```

Скрипт:
- Проверяет, что целевая PostgreSQL БД пуста (idempotent).
- Копирует данные всех таблиц в порядке FK-зависимостей (`metadata.sorted_tables`).
- Не переносит FTS5/vec0 таблицы (они в отдельном SQLite-файле векторного store).

### 5. Запуск

```bash
orqion serve
```

## Ограничения

- **JSON, не JSONB.** SQLAlchemy `JSON` type используется без `JSONB`. На PostgreSQL
  работает (как `json`), но без индексов GIN. Будущее: JSONB + GIN (§14.2).
- **batch_alter_table** на PostgreSQL использует `ALTER TABLE` напрямую (не
  пересоздаёт таблицу, как на SQLite). Эффективнее, но поведение отличается.
- **FTS5** доступен только в SQLite vector store. На PostgreSQL полнотекстовый
  поиск реляционных таблиц не поддерживается (не нужен — FTS5 работает в
  отдельном SQLite-файле векторного store).
- **sqlite-vec** требует загрузки extension. На PostgreSQL векторный store
  использует отдельный aiosqlite connection (`./data/vec.db`), независимый от
  основного engine — это работает без изменений кода.

## Будущее (§14.2)

- **pgvector** — нативный PostgreSQL vector store. Требует новую реализацию
  `PgVectorStore` (класс `VectorStore` Protocol). Условие начала: явный запрос
  от пользователей standard-профиля на отказ от split stack.
- **JSONB + GIN** — оптимизация JSON-колонок (meta, payload, capabilities).
- **Multi-tenant** — `workspace_id` уже в каждой таблице (ADR-3), но
  изоляция на уровне схемы/БД — будущая задача.

## Конкурентность и нагрузка

Профиль `standard` (arch.md §10.1) заявляет поддержку команды до 50 человек.
Нагрузочное тестирование (T-410, mock OpenAI-провайдер, PostgreSQL 16) показало:

| Concurrent | Total | Errors | Error type |
|---|---|---|---|
| 2 | 20 | 0% | — |
| 5 | 50 | 2% | http_500 (uvicorn HTTP layer) |
| 10 | 100 | 3-8% | http_500 (uvicorn HTTP layer) |

**Важно:** ошибки при concurrency≥5 возникают в uvicorn HTTP layer, не в
application code. Подтверждено: ASGI in-process transport (тот же app, тот же
engine, тот же pool) — 100/100 OK при 10 concurrent в 10 раундах. См. BUG-007
в planning.md.

**Рекомендации для production:**
- Для команды до 5 concurrent users — uvicorn single-process достаточно.
- Для 5+ concurrent — рекомендуется `uvicorn --workers N` (multi-process)
  или `gunicorn -k uvworker` (production process manager).
- Альтернатива: nginx reverse proxy перед orqion для connection buffering.
- Тюнинг `pool_size`/`max_overflow` в `create_async_engine` (engine.py) —
  не помогает (проблема в HTTP layer, не в DB pool).

T-505 (профили развёртывания) должен включить load test с реальными числами
после выбора production deployment.
