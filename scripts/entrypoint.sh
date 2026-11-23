#!/bin/sh
set -e
echo "Применяю миграции..."
alembic upgrade head
if [ "${SEED_DEMO:-0}" = "1" ]; then
  echo "Заполняю демо-данные..."
  python -m app.db.seed ${SEED_ARGS:-}
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
