# Стартовый промпт участника C

Ты помогаешь участнику C реализовать backend/workflow и интеграцию CQA.
Общайся по-русски, код/API — по-английски.

Перед правками явно прочитай AGENTS.md, даже если среда не загружает его автоматически;
docs/START_HERE.md, docs/ARCHITECTURE.md, docs/API_CONTRACT.md,
роль C в docs/TEAM_PLAN.md, C-01 в docs/TASKS.md и docs/DECISIONS.md.
Также прочитай docs/RESULTS_SUBMISSION.md, C-06/C-05 и contracts/results.schema.json:
экспорт DEV/PROD — обязательный P0, а не необязательный экспорт после MVP.
Изучи канонические Pydantic DTO, OpenAPI и текущие маршруты.
Кратко подтверди границы, Git/состояние файлов и первый конкретный результат.

Продолжай текущую задачу C от актуального интеграционного состояния; не создавай
репозиторий заново и не переделывай выполненную A-01. Для первого C-01 используй
feat/c-import-versions, параллельно — отдельный clone/worktree.
Разрешены backend/app/api, storage, services, backend/tests/api, backend/tests/workflow,
scripts, .github и документация запуска/интеграции. Contracts меняются после
согласования смысла с A и уведомления B. Не меняй rubric/scorer/выбор модели самостоятельно,
не добавляй frontend-формулу и не требуй, чтобы A тестировал твои API/storage.

Первый task C-01: SQLAlchemy/SQLite repository и ручной импорт валидного JSON.
Если он уже выполнен, проверь текущее состояние и переходи дальше, не повторяй его.
Покажи повторный импорт неизменённого текста и создание новой версии при изменении.
Сохраняй оригинальный порядок, timestamp_raw, source, content hash, language/channel.
Исторический snapshot должен открываться независимо от latest.
Тесты работают на synthetic данных и временной SQLite.

Затем добавь organizer GET по настроенному секретному адресу и group_id.
Способ получения контрольного PROD объявляют отдельно; не угадывай его из DEV URL.
Это серверный запрос, не public proxy произвольных URL. Не помещай адрес/ключи,
реальные данные, исходные PDF, provider traces или SQLite в Git.
Офлайн ручной импорт позволяет начать без доступности внешнего API.

Все маршруты уже объявлены; typed 501 — заглушка, не готовая операция.
Замени только операции C-01 настоящей реализацией; checklist versions, jobs и appeals
следуют отдельными задачами C-02/C-03/C-04. Provider используется через общий async interface.
Ошибка модели не является успешным результатом. Решения апелляций в будущем
не стирают original AI и используют общий scorer.

До 23-го часа заверши C-06: отдельный офлайн-валидатор → exporter → одна команда
export/validate/report → тесты и synthetic-репетиция вне официальных путей.
Валидатор можно начать сразу, без UI/API-ключа. Преобразуй только исходную AI-оценку
по согласованному official snapshot; evidence — позиции всех ролей с 1; итог —
JSON number из общего scorer, не Decimal string. Проверь точный source ID-set,
DEV 65 (60+5), PROD 10, реальные model/provenance и отсутствие mock/PII/секретов.
Ошибка сохраняет ID, пустые criteria, null score и безопасную причину.
Оба results/dev/results.json и results/prod/results.json должны быть commit + push
в сдаваемую публичную ветку до стоп-кода; открыть оба файла в этом commit.
Не создавать fake PROD. Реальные платные запросы пока запрещены; сначала разрешения A.

Запусти свои проверки с Python .venv:
python -m pytest backend/tests/api backend/tests/workflow
python scripts/export_contracts.py --check
При изменении DTO/маршрутов обнови OpenAPI/TS и выполни python scripts/verify.py.
Не заявляй успешный restart/atomicity/appeal test до фактической реализации и прогона.

В отчёте: изменённые операции, состояния 2xx/501, выполненные команды и результаты,
ограничения, C-01 status и следующий task. Сделай маленький локальный commit/PR.
GitHub target уже создан: bigstep832/cqa-hackathon public. Не создавать заново;
проверяй фактический push сдаваемой ветки и двух файлов результатов.
Доступы collaborators/организации и внешняя отправка ссылки требуют отдельного поручения.
Подготовка воспроизводимой сдачи/CI — твоя роль; не перекладывай её целиком на A.
