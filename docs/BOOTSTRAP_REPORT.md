# Отчёт этапа 0

Дата проверки: 7 ноября 2026, Asia/Qyzylorda. Пользователь — участник A.

## Реализовано

- Pydantic DTO для всех 15 согласованных операций и дополнительного synthetic demo.
- Каноническая рубрика из 8 критериев; boolean/scale/choice/N/A, явный mapping в quality.
- Единый точный scorer, N/A, W=0, секции с общим знаменателем, отдельные flags.
- Immutable DTO snapshots, проверка состава ответов и evidence соответствующей версии.
- Async EvaluationProvider и MockEvaluationProvider, ограниченный synthetic fixture.
- `/health`, `/api/v1/demo`, единый формат ошибок. 14 workflow операций — typed HTTP 501.
- Один тестовый React-экран с результатом, transcript, N/A, секциями, evidence,
  loading/error/empty. Он явно сообщает, что это не AI-оценка.
- Pydantic → OpenAPI → TypeScript, синтетические fixtures, lockfiles, офлайн CI.
- Документы команды, три ролевых промпта, три project-only skills, freeze metadata tool.
- Минимальный офлайн compare по original_score и ручным эталонам команды.

## Проверки

Финальный общий прогон `python scripts/verify.py`: **74 pytest, 4 Vitest**,
Ruff, mypy (17 backend source files), проверка OpenAPI/TS и production build — успешно.
После независимого review исправлены две ошибки числовой точности изменяемой рубрики;
5 добавленных regression tests входят в указанные 74 теста.

`CQA_BROWSER_CHANNEL=msedge` + `npm --prefix frontend run test:e2e`: 3/3 успешно:
реальный backend → demo → evidence; HTTP 503 без подмены результатом;
узкий viewport 390px и клавиатурный переход к evidence.

Установленные версии: Python 3.13.2, Node 24.16.0, npm 11.13.0;
FastAPI 0.162.1, Pydantic 2.14.3, pytest 9.2.0; React 19.3.1,
Vite 8.3.6, TypeScript 5.9.3. Полные зависимости — в двух lockfiles.

После исправления dev-only minimatch `npm ci`: 0 уязвимостей по npm audit.
Временный override и его проверка описаны в UI_DESIGN. Один warning тестового
клиента Starlette о будущем переходе с httpx на httpx2 не скрывался; тесты прошли.

## Источники и ограничения

Все 12 страниц трёх исходных PDF прочитаны и визуально проверены.
Блокирующих расхождений с TXT не найдено. Отдельный GET DEV подтвердил 65 записей;
данные и секретный URL не входят в Git. Аудиоссылки присутствуют, воспроизведение не проверено.

Реальные модели/Jev не вызывались. AI accuracy, стоимость и latency не измерялись.
Storage, импорт, persistent jobs, редактор критериев, апелляции и полный UI ещё planned.
На другой физической машине команды запуск пока не проверен. Актуальный результат CI
после публикации доступен в [GitHub Actions](https://github.com/bigstep832/cqa-hackathon/actions).
Локальные проверки и GitHub Actions — отдельные результаты.

## Передача

Scorer уже выполнен и передаётся A для смыслового ревью. A начинает A-01 — настоящий
baseline и eval. B начинает B-01 — развитие карточки/списка и API client.
C начинает C-01 — импорт JSON и хранение неизменяемых версий. Каждый тестирует свой модуль.

Репозиторий: https://github.com/bigstep832/cqa-hackathon (создан, public).
Отправку кода и CI требуется подтвердить фактическим результатом. Ссылка организаторам
не отправлялась; это действие C/уполномоченного участника до стоп-кода.

Остаются внешние решения: provider/model, бюджет и разрешение передачи диалогов;
точный стоп-код; handles B/C для выдачи доступа. Они не мешают офлайн-старту команды.
