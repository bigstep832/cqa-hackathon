# Стартовый промпт участника B

Ты помогаешь участнику B реализовать frontend CQA. Общайся по-русски.

Сначала явно прочитай AGENTS.md (если ИИ не подхватывает его, это всё равно нужно),
docs/START_HERE.md, docs/API_CONTRACT.md, роль B в docs/TEAM_PLAN.md,
B-01 в docs/TASKS.md и docs/UI_DESIGN.md, если он присутствует.
Посмотри текущее frontend, generated TS и fixtures/synthetic/demo.json.
До правок кратко подтверди границы роли, Git/состояние файлов и первый результат.

Работай от актуального main в feat/b-dialogue-card; параллельному агенту —
отдельный clone/worktree. Разрешён frontend/ и его собственные tests.
Используй текущий bootstrap; не пересоздавай всё приложение без причины.
Не меняй DTO, backend, rubric и scorer самовольно. Предложение изменения API
передай A/C, затем обнови generated TS через штатный генератор.

Первый task B-01: API client на общих типах и карточка synthetic demo.
Покажи transcript, ответы, веса/баллы/N/A, общий итог/секции/flags.
Ссылка evidence открывает сообщения конкретной оценённой версии.
Если audio_url есть, предоставь плеер или ссылку; без mapping не выдумывай seek.
Итог берётся с сервера, отдельной frontend-формулы нет.

Настоящий AI пока не требуется: работай с GET /api/v1/demo или согласованным fixture.
Баннер «Тестовые данные — не AI-оценка» обязателен. Не подменяй failed live mock-ответом.
Покажи loading/empty/error и честный ответ 501 для будущего workflow.
Секреты и provider API не должны попадать в браузер.

Проверь собственные изменения:
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
Bootstrap e2e покрывает demo/evidence; новые сценарии добавляй по реализации B-01/B-02,
а не изображай ещё не существующие импорт/апелляции готовыми.

В отчёте: что изменено и почему, как проверено, ограничения, состояние B-01,
вопросы к API и следующий небольшой task. Сделай локальный commit и PR
при согласованном доступе. Не публикуй реальные transcripts/ключи и не устанавливай
внешние интеграции без соответствующего поручения. Тестирование UI — твоя часть,
не перекладывай его на A.
