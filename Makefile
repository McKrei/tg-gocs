.PHONY: run test test-integration test-all lint format up down clean

# Запуск бота в dev-режиме
run:
	uv run python -m src.bot.main

# Запуск обычных юнит/интеграционных тестов (без внешних API вызовов)
test:
	uv run pytest -v -m "not integration"

# Запуск только интеграционных тестов (с проверкой реального OpenRouter и Drive)
test-integration:
	uv run pytest -v -m "integration"

# Запуск абсолютно всех тестов
test-all:
	uv run pytest -v


# Запуск линтеров и статического анализа
lint:
	uv run ruff check src tests
	uv run mypy src

# Автоматическое форматирование кода
format:
	uv run ruff format src tests

# Запуск контейнера в Docker Compose
up:
	docker compose up --build -d

# Остановка контейнера Docker Compose
down:
	docker compose down

# Очистка кэшей и временных файлов
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -r {} +
