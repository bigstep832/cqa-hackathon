# CQA — Conversation Quality Analytics

Локальный каркас для команды из трёх участников на 24-часовом хакатоне Freedom Broker.
Система должна оценивать действия оператора по изменяемому чек-листу и показывать
проверяемые доказательства. Главный ориентир отбора — близость исходной AI-оценки
к Ground Truth на 10 новых диалогах. Формула сравнения жюри неизвестна.

**Состояние: этап 0, bootstrap.** Это общий фундамент для параллельной разработки.
В поставку входят scorer с regression tests, исходная рубрика, канонические DTO,
OpenAPI/TS-типы, EvaluationProvider, синтетический MockEvaluationProvider,
`/health` и `/api/v1/demo`, тестовый экран и офлайн-проверки.
Остальные согласованные API-маршруты возвращают типизированный HTTP 501
`NOT_IMPLEMENTED`. Настоящий AI, импорт, SQLite storage, jobs, апелляции и полный UI
ещё предстоит реализовать. Результаты выполненных проверок — в
[отчёте bootstrap](docs/BOOTSTRAP_REPORT.md).

Начните с [AGENTS.md](AGENTS.md) и [START_HERE](docs/START_HERE.md).
Роли: **A — AI и scoring**, **B — frontend**, **C — backend/workflow и интеграция**.
Готовый общий scorer отдельно передаётся A на ревью; он не заменяет задачу настоящего оценщика.

## Требования

Использованные инструменты среды: Python **3.13.2**, Node **24.16.0**, npm **11.13**.
Python-зависимости закреплены в корневом `requirements.lock` (pip-tools),
frontend — в `frontend/package-lock.json`. Docker, uv и глобальные установки не нужны.

## Запуск в Windows / PowerShell

Из корня репозитория:

Если репозиторий ещё не скачан:

```powershell
git clone https://github.com/bigstep832/cqa-hackathon.git
cd cqa-hackathon
```

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe scripts/export_contracts.py
npm --prefix frontend ci
npm --prefix frontend run generate:api
```

Первый терминал:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Второй терминал:

```powershell
cd frontend
npm run dev
```

Адрес frontend смотрите в выводе Vite. Backend: `http://127.0.0.1:8000`,
схема API: `http://127.0.0.1:8000/docs`.

## Запуск в Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/export_contracts.py
npm --prefix frontend ci
npm --prefix frontend run generate:api
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

В другом терминале: `cd frontend`, затем `npm run dev`.

## Проверки

После установки зависимостей, из корня:

```powershell
.\.venv\Scripts\python.exe scripts/verify.py
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
npm --prefix frontend run generate:api:check
```

Для браузерного теста сначала запустите backend из раздела запуска. Playwright
сам поднимает только Vite. На Windows с установленным Edge:

```powershell
$env:CQA_BROWSER_CHANNEL = 'msedge'
npm --prefix frontend run test:e2e
```

На POSIX замените `.\.venv\Scripts\python.exe` на `.venv/bin/python`.
`verify.py` объединяет ruff, mypy, pytest, проверку экспорта и frontend
typecheck/lint/test/build. Playwright запускается отдельно и проверяет только
реализованный bootstrap: открытие demo и переход к evidence. Сквозная приёмка
импорта и апелляций — будущие задачи, см. [ACCEPTANCE](docs/ACCEPTANCE.md).
Для Chromium выполните `cd frontend`, `npx playwright install chromium`, затем
`npm run test:e2e` без переменной `CQA_BROWSER_CHANNEL`. В CI используется Chromium.

## Mock и live

Экран и результаты mock явно помечены: **«Тестовые данные — не AI-оценка»**.
Mock обрабатывает только созданные командой synthetic fixtures.
DEV/control никогда не заменяются готовыми ответами. Live ещё не реализован;
отсутствие ключа или ошибка провайдера не переключает режим в mock.

API-ключи и адрес организаторов вводятся локально по `.env.example`, не в браузере,
Git или чате. Реальные данные — только `data/private/`, результаты —
`artifacts/private/`; оба каталога исключены из Git. Исходные PDF не публикуются.

Создан публичный репозиторий [bigstep832/cqa-hackathon](https://github.com/bigstep832/cqa-hackathon).
Код отправлен в `main`; результаты локальных проверок — в отчёте bootstrap,
текущий CI — во вкладке Actions. Live-бюджет 0 до согласования
провайдера и разрешённой суммы. Хакатон начался, точный стоп-код ещё не назван. См. [DECISIONS](docs/DECISIONS.md).

- [Первый task A](prompts/A_AI_LEAD.md): baseline и воспроизводимый eval.
- [Первый task B](prompts/B_FRONTEND.md): карточка и API client.
- [Первый task C](prompts/C_BACKEND_WORKFLOW.md): импорт и неизменяемые версии.
