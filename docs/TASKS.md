# Задачи

Статусы: **bootstrap** — входит в этап 0, подтверждается его отчётом проверок;
**planned** — продуктовая функция ещё не готова. Публикация GitHub не считается
выполненной до подтверждённого push. Команды ниже запускаются из корня с Python .venv.

## Уже выполненная в bootstrap область для ревью A

**BOOT-01 · A/C · P0 · bootstrap.** Общая rubric, DTO, scorer, output validation,
synthetic provider/fixture, typed routes, health/demo, генерация OpenAPI/TS,
минимальный UI, команды офлайн-проверок и handoff.
Область: согласованный общий каркас.
Приёмка: отчёт запуска проверок, отсутствие секретов, явное разделение mock и live.
Scorer реализован отдельно от настоящего AI; A обязан проверить смысл и regression cases.
Не входит: live adapter, импорт/storage/jobs/апелляции и полный интерфейс.
Команды: `python scripts/verify.py`, `npm --prefix frontend run test:e2e`.
Не переносить статус bootstrap на все строки приёмки MVP.

## A-01 · A · P0 · planned — Baseline и первый воспроизводимый eval

- Цель: один настоящий генеративный EvaluationProvider, соответствующий контракту.
- Каталоги: `backend/app/evaluation/`, `backend/tests/evaluation/`, `evals/`;
  исправление domain/config только в рамках согласованного review scorer.
- Зависимости: BOOT-01; provider/model, право передачи данных и бюджет нужны только для live.
- Приёмка: динамическая передача всех критериев/ступеней/N/A; typed output;
  реальное evidence; timeout и ограниченный retry; usage всех вызовов;
  ошибка не превращается в mock/Нет/0. Офлайн adapter tests используют stub transport.
- Команды: `python -m pytest backend/tests/domain backend/tests/evaluation`,
  `python -m ruff check backend/app/evaluation evals`.
  Live-команду реализовать отдельно с лимитом запросов, затем согласовать бюджет.
- Не входит: UI, storage, API workflow, тесты модулей B/C, Jev ensemble.
- Первый небольшой блок: review scorer → схема разметки → adapter skeleton и один офлайн
  валидный/невалидный ответ. До разрешений не выполнять платный прогон.

## B-01 · B · P0 · planned — Список, фильтры и рабочий API client

- Каталоги: `frontend/`.
- Зависимости: generated TS, synthetic demo; настоящий provider не нужен.
- Приёмка: список/карточка, transcript, ответы/веса/баллы/N/A/секции/flags,
  переход к диапазону evidence именно оценённого snapshot, аудио-ссылка при наличии;
  загрузка/пустота/ошибка/501 и явный mock banner. Итог берётся с сервера.
- Команды: `npm --prefix frontend run typecheck`, `lint`, `test`, `build`,
  `test:e2e` (каждая — через `npm --prefix frontend run <script>`).
- Не входит: пересчёт итогов, hand-written DTO, изменение backend/domain.
- Уже готово в bootstrap: одна synthetic карточка и переход evidence. Используй её как основу.
- Первый блок: typed клиент списка/версий/оценок, выбор диалога и фильтры языка/канала;
  тесты через явный stub transport. До готовности C покажи 501 честно, без подмены live mock.

## C-01 · C · P0 · planned — Импорт и версии

- Каталоги: `backend/app/api/`, `storage/`, `services/`,
  `backend/tests/api/`, `backend/tests/workflow/`, `scripts/`.
- Зависимости: SourceConversation/ConversationVersion DTO. Ручной JSON не требует сети.
- Приёмка: ручной импорт → SQLite → список/карточка; повтор неизменённых данных не
  создаёт лишнюю версию; изменённый текст создаёт новую; старый snapshot доступен;
  timestamps и порядок сохранены. Затем настроенный organizer GET по group_id,
  без раскрытия адреса и без произвольного proxy.
- Команды: `python -m pytest backend/tests/api backend/tests/workflow`,
  `python scripts/export_contracts.py --check`.
- Не входит: выбор модели, семантика rubric, полные апелляции.
- Первый блок: SQLite repository + manual import + повторный импорт в интеграционном тесте.

## A-02 · A · P0 · planned — Калибровка качества

- Каталоги: `evals/`, evaluation и его tests.
- Зависимости: A-01, разрешённые локальные данные; live после бюджета.
- Приёмка: ручные эталоны команды с проверкой спорных случаев; разбиение до настройки;
  отчёт по критериям/языкам/каналам, N/A/C04/C06/C08/evidence;
  сравнение исходных автоматических ответов до апелляций.
- Команды: офлайн `python -m pytest backend/tests/evaluation`;
  конкретная eval CLI и reproducible command фиксируются при реализации A-01.
- Не входит: постоянная настройка по holdout и обещание официальной точности.

## B-02 · B · P0 · planned — Редактор и апелляции UI

- Каталоги: `frontend/`. Зависимости: B-01, версии checklist API, C-04.
- Приёмка: добавление критерия и изменение типа/шкалы/веса/N/A создают новую версию;
  форма апелляции и отдельное действие проверяющего, reason и effective score;
  исходная оценка остаётся видимой. Пустые/ошибочные/конфликтные состояния проверены.
- Команды: frontend typecheck/lint/test/build и браузерные сценарии редактора/апелляции.
- Не входит: локальная имитация успешного server mutation, расчёт итогов.

## C-02 · C · P0 · planned — Версии чек-листа

- Каталоги: api/storage/services и API/workflow tests.
- Зависимости: C-01, ChecklistVersion DTO и общая validation.
- Приёмка: создание и список checklist, новая immutable версия после изменения
  формулировки/типа/шкалы/веса/N/A; предыдущая версия доступна; невалидная
  конфигурация отвергается. B получает рабочие endpoints для редактора.
- Команды: API/workflow pytest, `python scripts/export_contracts.py --check`.
- Не входит: jobs, выбор модели и изменение смысла рубрики.

## C-03 · C · P0 · planned — Jobs и запуск оценки

- Каталоги: api/storage/services и API/workflow tests.
- Зависимости: C-01/C-02, EvaluationProvider; mock только synthetic в тестах.
- Приёмка: запуск по двум version IDs, queued/running/succeeded/failed,
  ограниченный worker и timeout; restart не оставляет running навсегда;
  запрос использует выбранный snapshot рубрики. Старые результаты не меняются.
- Команды: API/workflow pytest, `python scripts/verify.py`.
- Не входит: новый scorer, выбор LLM, внешняя очередь.

## C-04 · C · P0 · planned — Апелляции и атомарный пересчёт

- Каталоги: api/storage/services и API/workflow tests.
- Зависимости: C-03, общий scorer.
- Приёмка: confirm/override с reason, общая validation, исходный AI immutable,
  атомарное решение+пересчёт; одинаковый idempotency_key не дублирует событие,
  другое содержание с тем же ключом даёт 409.
- Команды: API/workflow pytest, `python scripts/verify.py`.
- Не входит: замена оригинальной AI-оценки ручным результатом в метриках.

## C-05 · C, B и A проверяют свою часть · P0 · planned — Сдача

- Каталоги C: scripts/.github/docs запуска; B: frontend tests; A: eval отчёт.
- Зависимости: обязательные A/B/C задачи; GitHub identity и доступ.
- Приёмка: все P0 строки ACCEPTANCE выполнены; запуск на другой машине;
  репозиторий cqa-hackathon публично доступен, безопасный commit, README;
  ссылка заранее передана организаторам уполномоченным участником;
  demo ≤ 5 минут, freeze SHA и versions записаны.
- Команды: `python scripts/verify.py`, `npm --prefix frontend run test:e2e`,
  `python scripts/check_public_files.py`, `python scripts/freeze.py` при реальном freeze.
- Не входит: редактирование кода после стоп-кода, самостоятельные внешние сообщения без поручения.

## A-03 · A · P1 · planned — Jev эксперимент

- Каталоги: evaluation/evals/tests. Зависимости: готовый P0, A-02, отдельный бюджет.
- Приёмка: одинаковая выборка/snapshots, Choice по допустимым вариантам,
  отдельный проверяемый evidence pipeline, сравнение точности/стоимости/задержки.
  При отсутствии улучшения baseline остаётся основным.
- Команды: evaluation tests и отдельная согласованная live-eval CLI.
- Не входит: бездоказательное объявление ансамбля лучшим, локальное обучение Kev.
