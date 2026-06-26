# Задача 5 — Telegram бот: базовая структура и FSM

## Статус: ⬜ Не начата

## Предзависимость
✅ Задача 4 выполнена

## Цель
Запустить скелет бота с aiogram 3.x: FSM, команды, приём файлов, whitelist пользователей.

## Что делаем

- [ ] `src/bot/main.py` — точка входа, инициализация Bot + Dispatcher + Storage
- [ ] `src/bot/states.py` — FSM состояния:
  - `idle` — ожидание
  - `processing_document` — идёт работа с файлами
  - `confirming` — ожидание подтверждения от пользователя
- [ ] `src/bot/handlers/commands.py` — `/start`, `/cancel`, `/reset`
- [ ] `src/bot/handlers/files.py` — приём фото и PDF (пока просто логируем)
- [ ] `src/bot/middleware/auth.py` — проверка что user_id в списке ALLOWED_USER_IDS
- [ ] `src/bot/keyboards.py` — inline-кнопки (заготовки)
- [ ] `tests/test_bot_handlers.py` — тесты хэндлеров через aiogram TestClient

## Критерий готовности

```bash
make run  # бот запускается, отвечает на /start
# Отправить фото — бот принимает и пишет "получил файл"
# Посторонний user_id — бот игнорирует
make test  # test_bot_handlers.py зелёные
```

## Заметки для агента

- FSM Storage — MemoryStorage для dev, Redis опционально в будущем
- Middleware должен работать ДО хэндлеров
- `ALLOWED_USER_IDS` — список int через запятую в `.env`
- Логировать все входящие файлы (имя, размер, тип)
