# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir poetry==2.1.0

COPY pyproject.toml poetry.lock ./
COPY src ./src
RUN poetry build -f wheel

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /app/dist /app/dist

RUN pip install --no-cache-dir /app/dist/*.whl && rm -rf /app/dist /root/.cache/pip

EXPOSE 8000

ENTRYPOINT ["dbzap"]
CMD ["serve"]
