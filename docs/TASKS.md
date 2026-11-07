# Задачи

Статусы: **bootstrap** — входит в этап 0, подтверждается его отчётом проверок;
**planned** — продуктовая функция ещё не готова. Публикация GitHub не считается
выполненной до подтверждённого push. Команды ниже запускаются из корня с Python .venv.

Обновление сдачи: [RESULTS_SUBMISSION](RESULTS_SUBMISSION.md). A-01 завершена в
согласованном офлайн-объёме; её не начинать заново. Новый обязательный объём —
A-04 (спецификация готова, ревью C впереди) и C-06 (экспорт/валидатор, ещё planned).
До контрольного часа подготовить C-06, не откладывать его за необязательные функции.

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

## A-01 · A · P0 · offline-ready — Baseline и первый воспроизводимый eval

Офлайн-часть реализована: общий structured pipeline, фабрика mock/Gemini/OpenAI/local,
транспортные тесты, формы ручной разметки, split/finalize и отдельный ограниченный runner.
См. [A01_OFFLINE_HANDOFF](A01_OFFLINE_HANDOFF.md). Реальные вызовы и accuracy ещё не
проверены; live ожидает согласования модели, бюджета и передачи данных.
Это не статус готовности всего MVP или API workflow.

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
- Зависимости: обязательные A/B/C задачи, включая C-06; GitHub identity и доступ.
- Приёмка: все P0 строки ACCEPTANCE выполнены; запуск на другой машине;
  репозиторий cqa-hackathon публично доступен, безопасный commit, README;
  ссылка заранее передана организаторам уполномоченным участником;
  demo ≤ 5 минут, freeze SHA и versions записаны; валидные DEV 65 и PROD 10
  опубликованы по двум точным путям до стоп-кода. Проверить оба файла в публичном
  commit сдаваемой ветки; commit без push не принимается.
- Команды: `python scripts/verify.py`, `npm --prefix frontend run test:e2e`,
  `python scripts/check_public_files.py`, `python scripts/freeze.py` при реальном freeze.
- Не входит: редактирование кода после стоп-кода, самостоятельные внешние сообщения без поручения.

## A-04 · A · P0 · spec-ready — Контракт сдачи и смысловая проверка

- Готово: `docs/RESULTS_SUBMISSION.md`, `contracts/results.schema.json`, mapping
  C01–C08, raw scale, evidence 1-based, original AI, единый scorer и тестовые сценарии.
  Это отдельный внешний контракт; API DTO, scorer и выполненная A-01 не меняются.
- Осталось после C-06: проверить реализацию преобразования, исходный официальный
  snapshot, N/A/C08/округление и отсутствие персональных данных в пояснениях.
- Зависимости: C-06 для интеграционного ревью; реальные прогоны — только после
  согласования провайдера, бюджета и передачи данных.
- Приёмка: вектор PDF даёт 85.38, C06=7 остаётся 7, C08 не меняет итог;
  appeals/custom rubric не подменяют официальный исходный AI, evidence всех ролей.
- Не входит: реализация C exporter/CLI/storage/release и frontend B.

## C-06 · C · P0 · planned — Экспорт DEV/PROD и офлайн-валидатор

- Спецификация: [RESULTS_SUBMISSION](RESULTS_SUBMISSION.md); схема A в
  `contracts/results.schema.json`. Схема описывает структуру, не весь валидатор.
- Каталоги: services/storage, scripts, backend/tests/workflow; отдельный экспортный
  DTO при необходимости согласовать с A, существующий API без причины не менять.
- Зависимости: A-04 для контракта; C-01/C-03 и A-01 для реальных результатов.
  Валидатор и synthetic-репетицию можно делать сразу, без ключей/сети/готового UI.
- Первый блок: отдельный офлайн-валидатор структуры и семантики, затем преобразование
  сохранённых оценок и CLI «экспорт → проверка → отчёт». Синтетическая репетиция
  только в `work/`, не в официальных путях; платные запросы сейчас запрещены.
- Приёмка: DEV ровно 65 (60+5), PROD ровно 10 чатов; точный исходный ID-set без
  изменения/дублей/пропусков; восемь кодов, типы/шкалы/N/A; evidence по всем исходным
  ролям; общий scorer и original_score; критический флаг; безопасные error-записи.
  Проверять immutable official snapshot, происхождение запуска, отсутствие mock,
  персональных данных и секретов. Сравнение с независимым source manifest обязательно.
- Проверки: positive/negative backend tests по таблице спецификации; офлайн-прогон
  export+validate для обоих размеров; имитация timeout/failed/неполного набора.
  Добавить фактическую команду в RESULTS_SUBMISSION/README только после реализации.
- Срок: инструмент и репетиция до 23-го часа; настоящий DEV заранее после разрешённого
  AI-прогона; PROD получить/оценить/проверить/commit/push до 24-го часа вместе с C-05.
- Не входит: вторая формула баллов, настройка модели на контроле, fake PROD,
  взятие на себя AI-калибровки A или изменение фронтенда B.

## A-03 · A · P1 · planned — Jev эксперимент

- Каталоги: evaluation/evals/tests. Зависимости: готовый P0, A-02, отдельный бюджет.
- Приёмка: одинаковая выборка/snapshots, Choice по допустимым вариантам,
  отдельный проверяемый evidence pipeline, сравнение точности/стоимости/задержки.
  При отсутствии улучшения baseline остаётся основным.
- Команды: evaluation tests и отдельная согласованная live-eval CLI.
- Не входит: бездоказательное объявление ансамбля лучшим, локальное обучение Kev.
