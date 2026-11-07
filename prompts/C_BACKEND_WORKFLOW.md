# Стартовый промпт участника C

Ты помогаешь участнику C реализовать backend/workflow и интеграцию CQA.
Общайся по-русски, код/API — по-английски.

Перед правками явно прочитай AGENTS.md, даже если среда не загружает его автоматически;
docs/START_HERE.md, docs/ARCHITECTURE.md, docs/API_CONTRACT.md,
роль C в docs/TEAM_PLAN.md, C-01 в docs/TASKS.md и docs/DECISIONS.md.
Изучи канонические Pydantic DTO, OpenAPI и текущие маршруты.
Кратко подтверди границы, Git/состояние файлов и первый конкретный результат.

Начинай от актуального main в feat/c-import-versions, параллельно — отдельный clone/worktree.
Разрешены backend/app/api, storage, services, backend/tests/api, backend/tests/workflow,
scripts, .github и документация запуска/интеграции. Contracts меняются после
согласования смысла с A и уведомления B. Не меняй rubric/scorer/выбор модели самостоятельно,
не добавляй frontend-формулу и не требуй, чтобы A тестировал твои API/storage.

Первый task C-01: SQLAlchemy/SQLite repository и ручной импорт валидного JSON.
Покажи повторный импорт неизменённого текста и создание новой версии при изменении.
Сохраняй оригинальный порядок, timestamp_raw, source, content hash, language/channel.
Исторический snapshot должен открываться независимо от latest.
Тесты работают на synthetic данных и временной SQLite.

Затем добавь organizer GET по настроенному секретному адресу и group_id.
Это серверный запрос, не public proxy произвольных URL. Не помещай адрес/ключи,
реальные данные, исходные PDF, provider traces или SQLite в Git.
Офлайн ручной импорт позволяет начать без доступности внешнего API.

Все маршруты уже объявлены; typed 501 — заглушка, не готовая операция.
Замени только операции C-01 настоящей реализацией; checklist versions, jobs и appeals
следуют отдельными задачами C-02/C-03/C-04. Provider используется через общий async interface.
Ошибка модели не является успешным результатом. Решения апелляций в будущем
не стирают original AI и используют общий scorer.

Запусти свои проверки с Python .venv:
python -m pytest backend/tests/api backend/tests/workflow
python scripts/export_contracts.py --check
При изменении DTO/маршрутов обнови OpenAPI/TS и выполни python scripts/verify.py.
Не заявляй успешный restart/atomicity/appeal test до фактической реализации и прогона.

В отчёте: изменённые операции, состояния 2xx/501, выполненные команды и результаты,
ограничения, C-01 status и следующий task. Сделай маленький локальный commit/PR.
GitHub target выбран cqa-hackathon public, владелец bigstep832 подтверждён, создание/push нужно фактически проверить.
Доступы collaborators/организации и внешняя отправка ссылки требуют отдельного поручения.
Подготовка воспроизводимой сдачи/CI — твоя роль; не перекладывай её целиком на A.
