FROM python:3.13-slim AS builder

# Установка uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Копирование файлов зависимостей
COPY pyproject.toml uv.lock ./

# Синхронизация зависимостей (без пакетов разработки)
RUN uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime

WORKDIR /app

# Копирование виртуального окружения
COPY --from=builder /app/.venv /app/.venv
# Копирование исходного кода
COPY src /app/src

# Настройка путей
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.bot.main"]
