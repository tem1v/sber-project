PY ?= .venv/bin/python

.PHONY: install run test migrate seed reset-db up down logs

install:            ## окружение и зависимости
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

run:                ## API на http://127.0.0.1:8000/docs
	$(PY) -m alembic upgrade head && $(PY) -m uvicorn app.main:app --reload

test:
	$(PY) -m pytest -q

migrate:
	$(PY) -m alembic upgrade head

seed:               ## справочники + демо-обращения + прогон агента
	$(PY) -m app.db.seed --analyze

reset-db:           ## сбросить демо-БД и заполнить заново
	$(PY) -m app.db.seed --reset --analyze

up:                 ## PostgreSQL + API в Docker
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api
