# Booking-core service image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY libs/common /app/libs/common
COPY services/booking-core /app/services/booking-core
# Operational scripts (thumbnail backfill, and anything like it) run
# inside this container so they read the service's own settings.
COPY scripts /app/scripts

WORKDIR /app/services/booking-core
RUN uv pip install --system --no-cache -e /app/libs/common -e .

EXPOSE 8002

CMD ["sh", "-c", "alembic upgrade head && python -m chirala_common.runtime_role --owner-url-env BOOKING_MIGRATION_DATABASE_URL && uvicorn booking_core.main:app --host 0.0.0.0 --port 8002"]
