# IAM service image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install uv for dependency management.
RUN pip install --no-cache-dir uv

# Copy shared lib and service (build context is the repo root).
COPY libs/common /app/libs/common
COPY services/iam /app/services/iam

WORKDIR /app/services/iam
RUN uv pip install --system --no-cache -e /app/libs/common -e .

EXPOSE 8001

# Run migrations then start the API.
CMD ["sh", "-c", "alembic upgrade head && python -m chirala_common.runtime_role --owner-url-env IAM_MIGRATION_DATABASE_URL && uvicorn iam_service.main:app --host 0.0.0.0 --port 8001"]
