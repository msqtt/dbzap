# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

RUN pip install --no-cache-dir poetry==2.1.0

COPY pyproject.toml poetry.lock ./
COPY src ./src
RUN poetry install --no-root --without dev && rm -rf $POETRY_CACHE_DIR
RUN poetry build -f wheel

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/dist /app/dist

RUN pip install --no-cache-dir /app/dist/*.whl && rm -rf /app/dist /root/.cache/pip

EXPOSE 8000

ENTRYPOINT ["dbzap"]
CMD ["serve"]
