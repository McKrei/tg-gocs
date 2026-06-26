# Задача 9 — Docker и деплой

## Статус: ✅ Выполнена

## Предзависимость
✅ Задача 8 выполнена

## Цель
Упаковать приложение в Docker-контейнер для production-запуска одной командой.

## Что делаем

- [x] `Dockerfile` — multi-stage сборка (builder → runtime), минимальный образ
- [x] `docker-compose.yml`:
  - Сервис `bot`
  - Volume `./data:/app/data` — БД, файлы, credentials.json
  - Передача `.env` переменных
- [x] `.dockerignore` — исключить лишнее из контекста
- [x] Проверка что `make up` запускает всё с нуля
- [x] Проверка что данные сохраняются между рестартами контейнера
- [x] Обновить `README.md` — секция "Деплой через Docker"

## Структура Docker

```
FROM python:3.12-slim AS builder
# установка uv, зависимостей

FROM python:3.12-slim AS runtime
# только runtime зависимости, исходники
CMD ["python", "-m", "src.bot.main"]
```

## Критерий готовности

```bash
make up  # контейнер запускается, бот отвечает
# Рестарт контейнера → данные в SQLite сохранились
docker logs tg-gocs-bot  # логи выводятся корректно
```
