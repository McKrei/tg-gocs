# CI/CD — Автодеплой через GitHub Actions

## Обзор

При каждом push в ветку `main` запускается workflow [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), который:

1. Подключается к серверу по SSH
2. Делает `git pull origin main`
3. Перезапускает бота через `make up` (docker-compose)
4. Отправляет уведомление в Telegram об успехе или ошибке

---

## Workflow: `deploy.yml`

```
push → main
   └── job: deploy
         ├── Checkout code
         ├── SSH → сервер: git pull + make up
         ├── [success] → Telegram: 🚀 Деплой завершён!
         └── [failure] → Telegram: ❌ Ошибка деплоя!
```

---

## Требуемые Secrets в GitHub

Перейди в **Settings → Secrets and variables → Actions** репозитория и добавь:

| Secret | Описание |
|---|---|
| `SSH_HOST` | IP-адрес или домен сервера |
| `SSH_USERNAME` | Имя пользователя для SSH (например, `root`) |
| `SSH_PRIVATE_KEY` | Приватный SSH-ключ (содержимое файла `~/.ssh/id_rsa`) |
| `TELEGRAM_BOT_TOKEN` | Токен бота для уведомлений (может быть тот же `BOT_TOKEN`) |
| `TELEGRAM_CHAT_ID` | chat_id, куда слать уведомления |

---

## Требования к серверу

На сервере должно быть:
- Git-репозиторий склонирован в `/root/tg-gocs`
- Docker и Docker Compose установлены
- Файл `.env` заполнен (не хранится в репозитории, настраивается вручную)
- Файл `data/credentials.json` на месте (Google Drive OAuth/SA)
- SSH-ключ добавлен в `~/.ssh/authorized_keys`

---

## Триггер деплоя

```bash
git push origin main  # → автоматический деплой
```

---

## Логи деплоя

Посмотреть логи контейнера после деплоя:

```bash
docker logs -f tg-gocs-bot
```
