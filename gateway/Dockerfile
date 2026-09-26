# API gateway image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# libs/common for the shared request-id middleware and logging.
COPY libs/common /app/libs/common
COPY gateway /app/gateway

WORKDIR /app/gateway
RUN uv pip install --system --no-cache -e /app/libs/common -e .

EXPOSE 8000

CMD ["uvicorn", "gateway_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
