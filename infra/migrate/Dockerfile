# One-shot migration image: every service's Alembic tree in one place.
#
# Its own image rather than a command on one of the service images, because
# migrating in order needs all three services' migrations and models, and no
# service image carries another's. It is the same base and the same installs,
# so it builds from the layer cache the service images already warmed.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY libs/common /app/libs/common
COPY services/iam /app/services/iam
COPY services/finance /app/services/finance
COPY services/booking-core /app/services/booking-core
COPY infra/migrate/migrate.sh /app/migrate.sh

RUN uv pip install --system --no-cache -e /app/libs/common \
      -e /app/services/iam -e /app/services/finance -e /app/services/booking-core \
    && chmod +x /app/migrate.sh

CMD ["/app/migrate.sh"]
