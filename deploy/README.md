# Развёртывание в Docker

Стек: `backend` (FastAPI + uvicorn, порт 8000 внутри сети) и `web` (nginx со статикой фронтенда и прокси `/api/` на backend). Снаружи доступен только `web` на порту 8080.

## Быстрый старт

```bash
cp backend/.env.example backend/.env.local   # затем отредактируйте
docker compose up -d --build
```

Приложение: <http://localhost:8080>. Остановка: `docker compose down` (данные сохраняются в томе).

### Настройка `backend/.env.local`

Файл не попадает ни в Git, ни в образы (исключён в `.dockerignore`) — он передаётся контейнеру только при запуске.

```env
XMLDT_OPENAI_API_KEY=sk-...           # ключ оператора (необязательно)
# XMLDT_DEFAULT_MODEL=gpt-5.4-mini
# XMLDT_MAX_UPLOAD=52428800           # лимит загрузки, байт (nginx пропускает до 55 МБ)
```

Если файла нет, сервис всё равно стартует (`required: false`, нужен Docker Compose >= 2.24; на более старых версиях создайте пустой `backend/.env.local`).

## Где хранятся данные

- Справочные данные (`schemas`, `norms`, `kb`, `derived`, `samples`) вшиты в образ backend в `/app/data` и доступны только для чтения. Тяжёлые каталоги `data/dl_fts`, `data/raw` и архивы `*.zip` в образ не входят.
- Изменяемые данные (сессии, загрузки и т.п.) лежат в именованном томе `xmldt_var`, смонтированном в `/var/lib/xmldt` (`XMLDT_VAR`).

```bash
docker volume inspect xmldt_var
# резервная копия тома
docker run --rm -v xmldt_var:/v -v "$PWD":/b alpine tar czf /b/xmldt_var.tgz -C /v .
```

Том удаляется только командой `docker compose down -v`.

## Вход по e-mail (SMTP)

Для отправки писем со ссылкой/кодом входа добавьте в `backend/.env.local` параметры SMTP и перезапустите backend (`docker compose up -d`):

```env
XMLDT_SMTP_HOST=smtp.example.com
XMLDT_SMTP_PORT=587
XMLDT_SMTP_USER=mailer@example.com
XMLDT_SMTP_PASSWORD=...
XMLDT_SMTP_FROM=XML-DT <mailer@example.com>
XMLDT_SMTP_STARTTLS=1
```

Примечание: в текущей версии кода backend переменные `XMLDT_SMTP_*` ещё не читаются; имена приведены по принятому соглашению `XMLDT_*` — сверьтесь с кодом той версии, в которой реализован e-mail-вход.

## Локальная ИИ (on-prem, Ollama)

Только для установки у клиента. В `backend/.env.local`:

```env
XMLDT_ALLOW_LOCAL_LLM=1
```

Ollama должна работать на хосте (по умолчанию порт 11434, слушать нужно не только loopback: `OLLAMA_HOST=0.0.0.0`). Из контейнера хост доступен как `host.docker.internal` (в `docker-compose.yml` для Linux добавлен `extra_hosts: host-gateway`), например `http://host.docker.internal:11434`.

Для режима «ИИ в браузере» (relay) CSP разрешает `connect-src` на `http://localhost:*` и `http://127.0.0.1:*`; WebSocket `/api/relay` проксируется nginx с заголовками Upgrade.

## Пересборка базы знаний

Каталог `data/` в образе только для чтения и неполный (нет `data/dl_fts` с XSD-архивами), поэтому для пересборки смонтируйте локальный `./data` поверх `/app/data`. Результаты запишутся в `./data/derived` на хосте. Контейнер работает от пользователя с uid 10001 — дайте ему запись в `data/derived`:

```bash
chmod -R a+rwX data/derived
docker compose run --rm -v "$PWD/data:/app/data" backend \
  python -m xmldt.kb.build --data /app/data --version 1.2.0 --date 2026-11-06
```

В образе нет `uv` и dev-зависимостей, поэтому вместо `uv run python -m ...` используется `python -m ...` (виртуальное окружение уже в `PATH`). Если нужен именно `uv run`, запускайте сборку на хосте из `backend/`: `uv run python -m xmldt.kb.build --data ../data`.

Чтобы постоянно использовать внешний `./data` (без пересборки образа после обновления базы), добавьте в сервис `backend` в `docker-compose.yml` том `- ./data:/app/data:ro` и перезапустите: `docker compose up -d`.

## Полезные команды

```bash
docker compose ps                  # состояние и healthcheck
docker compose logs -f backend
docker compose config              # проверка файла без запуска
docker compose up -d --build       # пересборка после обновления кода
```

## Заметки

- Образы собираются с корневым контекстом (`context: .`), исключения заданы в корневом `.dockerignore`.
- backend запускается с `--proxy-headers --forwarded-allow-ips=*`, поэтому порт 8000 не публикуется наружу — только `expose` внутри сети compose. Для HTTPS поставьте перед `web` внешний TLS-терминатор (Caddy/Traefik/nginx).
- Базовые образы зафиксированы: `python:3.12.12-slim`, `node:20.18.3-alpine`, `nginx:1.27.3-alpine`, `ghcr.io/astral-sh/uv:0.11.32`.

## Безопасность входа и cookie

- Без SMTP вход по e-mail отключён (ответ 503). Режим разработки, в котором код пишется в журнал сервера, включается только явно: `XMLDT_AUTH_DEV=1` (и `XMLDT_AUTH_DEV_ECHO=1`, чтобы код возвращался в ответе API). Не включайте эти переменные на сервере с реальными пользователями.
- Если TLS завершается на балансировщике перед nginx, передавайте `X-Forwarded-Proto: https` или задайте `XMLDT_COOKIE_SECURE=1`, чтобы cookie получали флаг `Secure`.
- Эксперты, которые могут подтверждать правила на странице `/review`, задаются списком: `XMLDT_REVIEWERS=a@example.com,b@example.com`.

## Vercel (развёрнуто: https://xml-dt-analyzer.vercel.app)

- Фронтенд собирается из `frontend/` в статические файлы, API — Python-функция `api/index.py` (тот же FastAPI-сервис). Настройки — `vercel.json`, исключения — `.vercelignore`, зависимости функции — `requirements.txt`.
- Данные: Postgres Neon (интеграция Vercel Marketplace, переменная `DATABASE_URL`). В базе хранятся загруженные XML, история, пользователи и коды входа, решения экспертов, настройки ИИ (без ключей). Собственный ключ пользователя хранится только в памяти функции.
- Ключ ChatGPT: переменная `XMLDT_OPENAI_API_KEY` (Sensitive). После перевыпуска ключа: `vercel env rm XMLDT_OPENAI_API_KEY production`, затем `vercel env add XMLDT_OPENAI_API_KEY production --sensitive` и `vercel deploy --prod`.
- Ограничения платформы: загрузка до 4 МБ на файл (лимит тела запроса функций); WebSocket не поддерживается, поэтому локальный ИИ через браузер на Vercel отключён; вход по e-mail работает только после настройки SMTP (`XMLDT_SMTP_HOST`, `XMLDT_SMTP_PORT`, `XMLDT_SMTP_USER`, `XMLDT_SMTP_PASSWORD`, `XMLDT_SMTP_FROM`) — без него вход и страница экспертной проверки недоступны.
- Повторный деплой: `vercel deploy --prod` из корня проекта.
