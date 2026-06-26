# Инструкция по настройке интеграции с Google Drive

Для интеграции бота с Google Drive используется авторизация по протоколу **OAuth 2.0 (рекомендуется для личных дисков `@gmail.com`)** или через **Сервисный Аккаунт (Service Account)**.

> [!WARNING]
> **Важно:** У сервисных аккаунтов на личных Google-дисках квота хранения по умолчанию равна 0. Попытка загрузить файлы через сервисный аккаунт приведет к ошибке `storageQuotaExceeded`. Поэтому для личных дисков необходимо использовать **OAuth 2.0**.

---

## 🛠️ Переход на OAuth 2.0 (для тех, у кого уже настроен Service Account)
Если вы ранее настраивали бота через Service Account и столкнулись с ошибкой квоты, выполните следующие шаги:
1. Выполните **Шаг 3** (Создание учетных данных OAuth Client ID) ниже.
2. Скачайте JSON-файл и сохраните его поверх старого файла в `data/credentials.json`.
3. Добавьте свой email в список тестовых пользователей (**Шаг 3, пункт 6**).
4. Запустите скрипт генерации токена (**Шаг 4**): `uv run python scripts/auth_gdrive.py`.
5. Перезапустите бота.

---

## Вариант 1: Настройка через OAuth 2.0 (Основной метод)

### Шаг 1: Создание проекта в Google Cloud
1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/).
2. Войдите под своим Google-аккаунтом.
3. Нажмите на выпадающий список проектов в левом верхнем углу и нажмите **New Project** (Новый проект).
4. Укажите название проекта (например, `family-docs-bot`) и нажмите **Create** (Создать).
5. Выберите созданный проект.

### Шаг 2: Включение Google Drive API
1. В левом меню выберите **APIs & Services** (API и службы) → **Library** (Библиотека).
2. В поиске введите **Google Drive API**.
3. Кликните по нему и нажмите **Enable** (Включить).

### Шаг 3: Создание учетных данных OAuth Client ID
1. Перейдите в **APIs & Services** → **OAuth consent screen** (Экран согласия OAuth).
2. Выберите тип **External** и нажмите **Create**.
3. Заполните обязательные поля:
   - **App name**: `Family Docs Bot`
   - **User support email**: ваш email
   - **Developer contact information**: ваш email
4. Нажмите **Save and Continue** на всех шагах, кроме шага **Test users**.
5. На шаге **Test users** нажмите **+ Add Users** и введите свой email (это обязательно, пока приложение находится в режиме тестирования). Нажмите **Save and Continue** и вернитесь к панели управления (Back to Dashboard).
6. Перейдите в раздел **APIs & Services** → **Credentials** (Учетные данные).
7. Нажмите **+ Create Credentials** → **OAuth client ID**.
8. В выпадающем списке **Application type** выберите **Desktop app** (Десктопное приложение).
9. Назовите его (например, `Family Docs Desktop Client`) и нажмите **Create**.
10. Скачайте JSON-файл созданных учетных данных (кнопка со стрелкой вниз в списке учетных данных).
11. Переименуйте скачанный файл в `credentials.json` и положите в директорию `data/` в корне проекта.

### Шаг 4: Генерация файла токена (token.json)
1. В терминале запустите скрипт авторизации:
   ```bash
   uv run python scripts/auth_gdrive.py
   ```
2. Откроется окно браузера. Выберите ваш Google-аккаунт, предоставьте доступ приложению (если появится предупреждение "Google hasn't verified this app", нажмите *Advanced* → *Go to Family Docs Bot (unsafe)*).
3. После успешной авторизации на экране появится сообщение "The authentication flow has completed", а в терминале — "Успешно! Токен сохранен в data/token.json".

### Шаг 5: Настройка переменных окружения (.env)
Откройте файл `.env` в корне проекта и убедитесь, что указаны следующие параметры:
```env
# Путь к файлу OAuth client secrets
GDRIVE_CREDENTIALS_PATH=data/credentials.json
# Путь к файлу токена авторизации (создается автоматически после Шага 4)
GDRIVE_TOKEN_PATH=data/token.json
# ID корневой папки вашего Google Drive, куда будут сохраняться документы
GDRIVE_ROOT_FOLDER_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ
```
*(ID папки можно скопировать из адресной строки браузера при открытии этой папки в Google Drive).*

---

## Вариант 2: Настройка через Service Account (Альтернативный метод)
*Используйте этот метод только если вы загружаете файлы в Shared Drive (Общие диски) организации Google Workspace, так как на личных дисках квота сервисного аккаунта равна 0.*

### Шаг 1-2: Выполните Шаги 1 и 2 из Варианта 1.
### Шаг 3: Создание Сервисного Аккаунта
1. Перейдите в **APIs & Services** → **Credentials**.
2. Нажмите **+ Create Credentials** → **Service Account**.
3. Заполните имя (например, `drive-uploader`) и нажмите **Create and Continue**, а затем **Done**.
### Шаг 4: Экспорт ключа
1. В списке **Service Accounts** нажмите на созданный аккаунт.
2. Перейдите на вкладку **Keys** → **Add Key** → **Create new key** (формат JSON).
3. Скачанный файл переименуйте в `credentials.json` и сохраните в `data/`.
### Шаг 5: Настройка Shared Drive
1. Поделитесь нужной папкой на Google Drive с email-адресом сервисного аккаунта (роль **Editor**).
### Шаг 6: Настройка переменных (.env)
Удалите или закомментируйте строку `GDRIVE_TOKEN_PATH`, чтобы бот переключился на авторизацию через Service Account:
```env
GDRIVE_CREDENTIALS_PATH=data/credentials.json
GDRIVE_ROOT_FOLDER_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ
```
