# Finance service image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY libs/common /app/libs/common
COPY services/finance /app/services/finance

WORKDIR /app/services/finance
RUN uv pip install --system --no-cache -e /app/libs/common -e .

EXPOSE 8003

CMD ["sh", "-c", "alembic upgrade head && python -m chirala_common.runtime_role --owner-url-env FINANCE_MIGRATION_DATABASE_URL && uvicorn finance_service.main:app --host 0.0.0.0 --port 8003"]
