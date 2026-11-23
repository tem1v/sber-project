# CivicFlow

Прототип интеллектуальной системы маршрутизации обращений граждан (командный магистерский pet-проект).
ТЗ: [`docs/CivicFlow_TZ_po_uchastnikam_i_modulyam.docx`](docs/CivicFlow_TZ_po_uchastnikam_i_modulyam.docx).

В репозитории сейчас реализованы **backend, БД, тесты и деплой**. Для AI-части (LLM и агентный цикл)
подготовлены контракты и встроенная заглушка на правилах (`rule_based`), поэтому сквозной сценарий
работает уже сейчас, без API-ключей.

## Быстрый старт

### Docker (PostgreSQL + API)

```bash
docker compose up -d --build
```

* Swagger: http://localhost:8000/docs
* при старте применяются миграции и заливаются демо-данные (12 вымышленных обращений, прогнанных агентом);
  отключить: `SEED_DEMO=0 docker compose up -d`
* полный сброс: `docker compose down -v`

### Локально (SQLite)

```bash
make install        # .venv + зависимости
cp .env.example .env
make reset-db       # схема + справочники + демо-обращения
make run            # http://127.0.0.1:8000/docs
make test
```

## Сквозной сценарий через API

```bash
# 1. создать обращение
curl -X POST localhost:8000/api/v1/requests -H 'Content-Type: application/json' \
  -d '{"raw_text": "Не работает лифт по адресу ул. Гагарина, д. 7", "source_channel": "web"}'
# 2. запустить агента: статус станет needs_review / needs_clarification / urgent_escalation / duplicate_candidate
curl -X POST localhost:8000/api/v1/requests/1/analyze
# 3. решение сотрудника: готовится черновик регистрации (ready_for_confirmation)
curl -X POST localhost:8000/api/v1/requests/1/review -H 'Content-Type: application/json' \
  -d '{"decision": "accept", "reviewer": "operator-1"}'
# 4. явное подтверждение (повторный вызов идемпотентен)
curl -X POST localhost:8000/api/v1/requests/1/confirm-registration -H 'Content-Type: application/json' \
  -d '{"confirmed_by": "operator-1"}'
# трассировка агента
curl localhost:8000/api/v1/requests/1/trace
```

## API (`/api/v1`)

| Метод | Endpoint | Назначение |
|---|---|---|
| POST | `/requests` | Создать обращение |
| GET | `/requests` | Список; фильтры `status` (можно несколько), `topic`, `department_id`, `priority`, `date_from`, `date_to`, `q`, `limit`, `offset` |
| GET | `/requests/{id}` | Карточка: анализ, проверка маршрута, кандидаты-дубли, срок, черновики, решения, `allowed_actions` |
| POST | `/requests/{id}/analyze` | Запустить или повторить анализ агентом (никогда не регистрирует) |
| POST | `/requests/{id}/review` | Решение: `accept` / `correct` / `reject` / `request_clarification` / `escalate` |
| POST | `/requests/{id}/confirm-registration` | Подтвердить регистрацию (только после черновика, идемпотентно) |
| POST | `/requests/{id}/clarification` | Подтвердить отправку уточнения (идемпотентно) |
| PATCH | `/requests/{id}/links/{link_id}` | Подтвердить или отклонить связь-дубликат (обращения не объединяются) |
| GET | `/requests/{id}/trace` | Запуски агента и шаги: инструмент, аргументы, результат, время, ошибки |
| GET | `/departments` | Справочник ведомств |
| GET | `/analytics/summary` | Сводка по статусам, темам, ведомствам, срочным, дублям, доля исправлений и техошибок |
| GET | `/health`, `/health/ready` | Liveness и проверка БД |

Все ошибки приходят в одном формате: `{"code", "message", "details", "request_id"}`.
`request_id` дублируется в заголовке `X-Request-ID`.

## Статусы

```
new → analyzing → needs_review | needs_clarification | urgent_escalation | duplicate_candidate
                ↘ failed (LLM/агент упал; можно повторить analyze)
needs_review | … → (review: accept/correct) → ready_for_confirmation → (confirm) → registered
любое рабочее → (review: reject) → rejected
```

Переход `analyzing → registered` запрещён, а `registered` достижим **только** из `ready_for_confirmation`.
Таблица переходов: `app/services/statuses.py`.

## Гарантии безопасности

* Анализ ничего не регистрирует и не отправляет: агент только заполняет карточку.
* После агента backend **повторно** прогоняет guardrails (`validate_route`) и не доверяет выводу LLM.
* Срочность, низкая уверенность, противоречия или адрес, которого нет в тексте, останавливают авто-сценарий.
* Исходный ответ модели (`request_analysis`) не перезаписывается: исправления сотрудника лежат в `reviews`.
* Дубликаты хранятся только как связи-кандидаты.
* Подтверждение регистрации защищено от дублей и в коде, и уникальным частичным индексом в БД.
* Лимит шагов и таймаут агента (`AGENT_MAX_STEPS`, `AGENT_TIMEOUT_S`). Ошибка инструмента возвращается агенту и не роняет запуск.
* В логах только метод, путь, статус и время, без тел запросов и персональных данных.

## Структура

```
app/
  api/            # FastAPI: routes/, schemas.py (контракт с frontend), errors.py
  agent/          # state.py, tools_registry.py, tools.py, base.py (протокол Agent), rule_based_agent.py
  ai/             # schemas.py (контракт анализа, п. 5.3 ТЗ), rule_based.py (заглушка без LLM)
  guardrails/     # validators.py — проверка маршрута
  services/       # статусы, анализ, решения, маршрутизация, дубли, сроки, аналитика
  db/             # models.py, session.py, seed.py, migrations/ (Alembic)
tests/            # pytest: API, агентный цикл, решения, БД/миграции, юниты
```

## БД

PostgreSQL (в Docker) или SQLite (локально и в тестах). Таблицы из п. 9.1 ТЗ: `requests`, `request_analysis`,
`departments`, `routing_rules`, `request_links`, `deadlines`, `reviews`, `agent_runs`, `agent_trace`,
`action_drafts`. Внешние ключи, CHECK-ограничения на статусы и значения, индексы по `received_at`, `status`,
`department_id`, `topic`, `priority`.

* миграции: `alembic upgrade head` / `alembic downgrade base`
* новая миграция после изменения моделей: `alembic revision --autogenerate -m "..."`
* сброс демо-БД: `python -m app.db.seed --reset --analyze`

Ограничения SQLite: нет `SELECT … FOR UPDATE`, поэтому конкурентные подтверждения защищены только
уникальным индексом. Для многопользовательского режима нужен PostgreSQL.

## Тесты и CI

`pytest -q`: 69 тестов, включая неизвестный ID, некорректный ввод, недоступность LLM, падение агента,
зацикливание, повторное подтверждение и сохранность данных после перезапуска. Отдельный тест сверяет
миграции с моделями (`alembic check`).

GitHub Actions (`.github/workflows/ci.yml`) запускает тесты на SQLite и на PostgreSQL, гоняет миграции
вверх и вниз и делает смоук-тест `docker compose`.

## Как подключить LLM и агента (участникам 1 и 2)

* **Участник 1:** `app/ai/extraction.py` с функцией `extract(text: str) -> AnalysisResult` (схема в
  `app/ai/schemas.py`), затем `EXTRACTOR=llm`.
* **Участник 2:** `app/agent/agent.py` с классом `LLMAgent` по протоколу `app/agent/base.py`.
  Описание инструментов для function calling: `tools.specs()`, вызов: `tools.call(name, args, decision=..., token_usage=...)`.
  Трассировка, лимиты и запись в `state` уже сделаны. Недоступность модели сообщается через
  `raise AgentUnavailableError(...)`, тогда API вернёт 503 `LLM_UNAVAILABLE`. Затем `AGENT_MODE=llm`.
* **Участник 3:** guardrails расширяются в `app/guardrails/validators.py` с сохранением контракта `ValidationResult`.
