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

# Migrations are NOT run here any more. They run once, in order, in the
# `migrate` service (infra/migrate), which this one waits for. Migrating on
# start let booking-core reach a revision that needs finance.folios before
# finance had created it, and let replicas race each other on one database.
# A service started outside compose needs `infra/migrate/migrate.sh` run
# against its database first.
CMD ["uvicorn", "iam_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
