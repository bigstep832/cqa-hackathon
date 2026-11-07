# CQA — Conversation Quality Analytics

Локальный каркас для команды из трёх участников на 24-часовом хакатоне Freedom Broker.
Система должна оценивать действия оператора по изменяемому чек-листу и показывать
проверяемые доказательства. Главный ориентир отбора — близость исходной AI-оценки
к Ground Truth на 10 новых диалогах. Формула сравнения жюри неизвестна.

Backend сохраняет диалоги и версии чек-листов в SQLite, выполняет задания оценки,
принимает апелляции и выгружает исходные оценки в официальный формат. В интерфейсе
доступны диалоги, чек-листы, апелляции и профили операторов. Инструкции по запуску заданий и
проверке результатов приведены в [контракте API](docs/API_CONTRACT.md),
[инструкции по экспорту](docs/EXPORT.md) и [отчёте участника A](docs/A01_OFFLINE_HANDOFF.md).
Работа с настоящими данными и точность относительно Ground Truth требуют отдельной приёмки.

Начните с [AGENTS.md](AGENTS.md) и [START_HERE](docs/START_HERE.md).
Роли: **A — AI и scoring**, **B — frontend**, **C — backend/workflow и интеграция**.
Смысловое ревью общего scorer завершено A; accuracy модели требует отдельного прогона.

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

Первый терминал. Для работы с частными настройками создайте локальный `.env` по
`.env.example` и добавьте к команде `--env-file .env`. Без настроек доступны
ручной импорт, хранение, синтетический пример и его оценка. Используйте один процесс
uvicorn с доступом только с локального компьютера. Тот же порядок применим в Linux/macOS.

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

## Профиль оператора

Чтобы увидеть профиль на примере, откройте «Диалоги» и вставьте содержимое
[учебного JSON](fixtures/synthetic/operator-profile.json) в «Ручной импорт синтетического JSON».
Подтвердите импорт и откройте «Операторы» → «Учебный-01». В профиле будут три звонка
(один успешный, один неуспешный и один с неизвестным исходом), один чат и три минуты
известной длительности. Средняя длительность двух измеренных звонков — полторы минуты.
В третьем звонке клиент обратился, но в транскрипте нет ответа оператора.
Данные существующего диалога можно дополнить из его карточки.
[Расчёт показателей](docs/API_CONTRACT.md#профиль-оператора).

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
экран demo и переход к evidence. Pytest проходит работу API с SQLite,
перезапуск и апелляции. Состояние UI-приёмки отражено в [ACCEPTANCE](docs/ACCEPTANCE.md).
Для Chromium выполните `cd frontend`, `npx playwright install chromium`, затем
`npm run test:e2e` без переменной `CQA_BROWSER_CHANNEL`. В CI используется Chromium.

## Mock и live

Экран и результаты mock явно помечены: **«Тестовые данные — не AI-оценка»**.
Mock обрабатывает только созданные командой synthetic fixtures.
DEV/control никогда не заменяются готовыми ответами. Адаптеры A-01 проверены офлайн;
реальные вызовы требуют утверждённого приватного плана и доступа к источнику. Отсутствие ключа
или ошибка провайдера не переключает режим в mock.

API-ключи и адрес организаторов вводятся локально по `.env.example`, не в браузере,
Git или чате. Реальные данные — только `data/private/`, результаты —
`artifacts/private/`; оба каталога исключены из Git. Исходные PDF не публикуются.
Исключение — обязательная обезличенная итоговая выжимка по
[RESULTS_SUBMISSION](docs/RESULTS_SUBMISSION.md): `results/dev/results.json` (65)
и `results/prod/results.json` (10). Оба файла нужно commit + push до стоп-кода.
Экспортёр и валидатор сверяют оценки с сохранёнными версиями, вызовами модели и
составом группы. Для офлайн-репетиции запустите `python scripts/submission.py rehearse`.

Создан публичный репозиторий [bigstep832/cqa-hackathon](https://github.com/bigstep832/cqa-hackathon).
История каркаса зафиксирована в отчёте bootstrap,
текущий CI — во вкладке Actions. Live-бюджет 0 до согласования
провайдера и разрешённой суммы. Команда **DT20**, стоп-код **08.11.2026, 12:00 по Астане**
(подтверждено пользователем). См. [DECISIONS](docs/DECISIONS.md).

- [Первый task A](prompts/A_AI_LEAD.md): baseline и воспроизводимый eval.
- [Первый task B](prompts/B_FRONTEND.md): карточка и API client.
- [Первый task C](prompts/C_BACKEND_WORKFLOW.md): импорт и неизменяемые версии.
