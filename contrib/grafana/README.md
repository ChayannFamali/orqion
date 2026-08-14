# Orqion — пример дашборда Grafana

> **Это справочный материал, а не часть поставки.**
>
> Дашборд поставляется в `contrib/` как пример (arch.md ADR-16).
> Он не тестируется в CI, не является зависимостью orqion, и не
> гарантируется актуальным при изменении метрик. Если метрики
> добавлены/переименованы/удалены — обновите дашборд вручную.

## Установка

1. Откройте Grafana → Dashboards → Import
2. Загрузите `orqion.json`
3. Выберите datasource: Prometheus (endpoint `GET /metrics` orqion)
4. Импортируйте

## Панели

| # | Панель | Метрика | Тип |
|---|---|---|---|
| 1 | Chat Request Rate | `orqion_chat_requests_total` | Time series (by status) |
| 2 | Error Rate | `orqion_chat_requests_total{status="error"}` | Stat |
| 3 | Chat Request Duration (p50/p95/p99) | `orqion_chat_request_duration_seconds` | Time series (histogram_quantile) |
| 4 | Provider Probe Rate | `orqion_provider_probe_total` | Time series (by kind+status) |
| 5 | Available Models per Provider | `orqion_provider_available_models` | Gauge |
| 6 | Last Probe Age | `orqion_provider_last_probe_timestamp_seconds` | Stat (time() - gauge) |
| 7 | RAG Query Rate | `orqion_rag_queries_total` | Time series (by status) |

## Требования

- orqion с `metrics_enabled=True` (extras `orqion[metrics]`)
- Prometheus scraping `GET /metrics` endpoint
- Grafana 10+
