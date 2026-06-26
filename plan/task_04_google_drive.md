# Задача 4 — Google Drive интеграция

## Статус: ⬜ Не начата

## Предзависимость
✅ Задача 3 выполнена

> [!IMPORTANT]
> **Нужно сделать вручную до старта задачи:**
> 1. Создать Google Cloud Project
> 2. Включить Google Drive API
> 3. Создать Service Account → скачать `credentials.json`
> 4. Положить `credentials.json` в `data/`
> 5. Создать папку в Google Drive → скопировать её ID
> 6. Дать Service Account права "Редактор" на эту папку
> 7. Добавить `GDRIVE_ROOT_FOLDER_ID=...` в `.env`
>
> Агент подготовит пошаговую инструкцию.

## Цель
Реальная интеграция с Google Drive: папки, загрузка файлов, получение ссылки.

## Что делаем

- [ ] `src/drive/client.py` — авторизация через Service Account
- [ ] `src/drive/uploader.py` — логика загрузки:
  1. Проверить существование папки по пути (напр. `Медицина/Жена`)
  2. Создать папки если не существуют
  3. Загрузить файл
  4. Вернуть `webViewLink`
- [ ] Подключить Drive к инструменту `save_to_local_and_drive` (убрать заглушку)
- [ ] `tests/test_drive.py` — интеграционный тест (с реальным Drive)
- [ ] Инструкция по настройке Service Account в `docs/google_drive_setup.md`

## Критерий готовности

```bash
# Файл появляется в Google Drive после запуска
uv run python -c "
import asyncio
from src.drive.uploader import upload_file
link = asyncio.run(upload_file('tests/fixtures/test.pdf', 'Тест/Папка'))
print('Ссылка:', link)
"
make test  # test_drive.py зелёные
```
