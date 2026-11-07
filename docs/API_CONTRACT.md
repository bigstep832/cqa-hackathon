# Контракты API и оценщика

Источники истины:
[канонические DTO](../backend/app/contracts/models.py) →
[OpenAPI](../contracts/openapi.json) →
`frontend/src/api/generated/`.
Документ объясняет семантику; точные поля, required/nullability, ограничения
и response schemas берутся из DTO/OpenAPI, а не из вручную скопированных TS-интерфейсов.

## Маршруты

Маршруты ниже работают с локальным хранилищем. Форма каждого ответа доступна в OpenAPI.

| Метод и путь | Request → успешный response DTO | Целевая операция |
| --- | --- | --- |
| GET /health | — → HealthResponse | Проверка запуска, реализована |
| GET /api/v1/demo | — → DemoPayload | Synthetic mock, реализован |
| POST /api/v1/datasets/import | DatasetImportRequest → ImportResponse | Импорт настроенного источника по group_id |
| POST /api/v1/conversations/import | ConversationImportRequest → ImportResponse | Ручной JSON |
| GET /api/v1/conversations | query → ConversationList | Список, offset/limit |
| GET /api/v1/conversations/{conversation_id} | path → ConversationVersion | Последняя версия внешнего диалога |
| GET /api/v1/conversation-versions/{version_id} | path → ConversationVersion | Конкретный исторический snapshot |
| GET /api/v1/conversation-versions/{version_id}/audio | одиночный Range → audio/* | Аудио из разрешённого источника, HTTP 200/206 |
| POST /api/v1/checklists | ChecklistCreateRequest → ChecklistVersion | Новый чек-лист |
| GET /api/v1/checklists | — → ChecklistList | Список последних версий |
| POST /api/v1/checklists/{checklist_id}/versions | ChecklistVersionCreateRequest → ChecklistVersion | Новая immutable версия |
| GET /api/v1/checklist-versions/{version_id} | path → ChecklistVersion | Snapshot |
| POST /api/v1/evaluations | EvaluationCreateRequest → Evaluation | Постановка задачи оценки |
| GET /api/v1/evaluations/{evaluation_id} | path → Evaluation | Статус и готовый результат |
| POST /api/v1/evaluations/{evaluation_id}/appeals | AppealCreateRequest → Appeal | Апелляция |
| GET /api/v1/evaluations/{evaluation_id}/appeals | path → AppealList | Список апелляций |
| POST /api/v1/appeals/{appeal_id}/decision | DecisionCreateRequest → DecisionResponse | Решение и эффективный результат |

Успешные статусы контракта: GET и импорт — 200; создание чек-листа, версии и апелляции —
201; запуск оценки — 202; решение — 200. Некорректный request — 422 VALIDATION_ERROR;
неизвестная сущность — 404, конфликт решения — 409. Ненастроенный источник возвращает
503, повреждённый внешний набор — 502. Для POST действует предел тела 16 МиБ.
При ошибке оценки клиент получает сохранённый статус failed и причину.

Повтор импорта сравнивается с последней версией того же источника, группы и внешнего ID.
Изменение создаёт новую версию. Весь пакет и манифест группы меняются одной транзакцией.
GET по внешнему ID показывает последнюю сохранённую версию, GET по ID версии — запись,
которую видел оценщик. Исходный адрес аудио хранится в приватной БД. Для воспроизведения
задайте CQA_AUDIO_ORIGINS и настройте CQA_PUBLIC_API_BASE_URL до импорта.

POST оценок принимает необязательный заголовок Idempotency-Key. Повтор с тем же телом
возвращает то же задание, с другим телом — 409. Два обработчика выбирают задания
из SQLite. После перезапуска queued продолжаются, running переходят в failed.
У апелляции одна запись на пару «оценка, критерий». Повтор того же комментария
возвращает запись, другой комментарий даёт 409. Повтор решения с тем же ключом и
телом возвращает сохранённый ответ. Для отображения исправления B читает решения
из списка апелляций и effective_score. Исходные ответы и original_score сохраняются.

## Формат данных

`ConversationVersion` хранит внешний `conversation_id`, собственный `id`,
`dataset_group_id`, `source`, `language`, `channel`, `content_hash`,
`imported_at`, `messages`, `audio_url`, `synthetic`.
Message: `message_id/speaker/text/timestamp_raw/order/audio_offset_seconds`.
Порядок messages сохраняет исходный массив. ID сообщения уникален внутри версии.

`ChecklistVersion`: `id/checklist_id/version/name/created_at/sections/criteria`.
Критерий задаёт `kind: scored|flag`, `answer_type: boolean|scale|choice`,
варианты с определениями, weight, allow_na и na_condition.
`options[].quality` хранит явную долю выполнения; flag использует flagged.

Decimal в результатах передаётся **строкой**. Общий результат `ScoreResult`:
`total/applicable_weight/earned_points/reason/criteria/sections/flags`.
Не преобразовывайте исходный scorer в frontend-формулу.

## Минимальные JSON-примеры

Импорт из настроенного источника:

```json
{"group_id":"DEV"}
```

Ручной импорт, только синтетический пример:

```json
{
  "dataset_group_id":"MANUAL_SAMPLE",
  "records":[{
    "conversation_id":"sample-ru-001",
    "channel":"agent chat",
    "language":"RU",
    "transcript":[{"timestamp":"00:02","speaker":"operator","text":"Здравствуйте, я Анна."}],
    "audio_url":null
  }]
}
```

Запуск будущей live-оценки после согласования provider/model/budget:

```json
{
  "conversation_version_id":"conversation-version-example",
  "checklist_version_id":"checklist-version-example",
  "mode":"live"
}
```

Фрагмент типизированного ответа оценщика:

```json
{
  "criterion_id":"C04",
  "answer":{"type":"scale","value":15},
  "explanation":"Основной запрос закрыт, одна второстепенная деталь пропущена.",
  "evidence":[{
    "conversation_version_id":"conversation-version-example",
    "start_message_id":"m2",
    "end_message_id":"m3"
  }]
}
```

Это иллюстрация формы; example IDs не существуют в fixture.
Ответы других типов: `{"type":"boolean","value":true}`,
`{"type":"choice","value":"configured-option"}`,
`{"type":"na","reason":"Запрос не требует идентификации"}`.

Override:

```json
{
  "action":"override",
  "reason":"В исходной оценке пропущена подтверждённая реплика.",
  "override_answer":{"type":"boolean","value":true},
  "idempotency_key":"review-example-001"
}
```

Для confirm override_answer отсутствует или null. Для override он обязателен.
Повтор ключа с тем же запросом должен вернуть сохранённое решение; тот же ключ
с другим содержимым — конфликт. Это правило реализации C-04.

Общий error envelope:

```json
{"error":{"code":"SOURCE_NOT_CONFIGURED","message":"Источник импорта не настроен.","details":[]}}
```

Текст message может различаться; клиент маршрутизирует ошибки по code/status.
В details используется массив `{field,message}`, секреты/полные транскрипты исключены.

## EvaluationProvider

Единый асинхронный интерфейс:

```python
async def evaluate(evaluation_input: EvaluationInput) -> EvaluationOutput:
    ...
```

Input = immutable `conversation/checklist/settings`.
Settings = `mode/provider/model/timeout_seconds`.
Output = ответы по всем активным критериям + metadata; **итога в output нет**.
Pipeline вызывает `app.evaluation.validation.validate_output(input, output)`,
затем общий scorer. Metadata включает mode, provider, model, версии инструкции/pipeline
и calls; каждый call учитывает purpose, attempt, latency, usage и известную стоимость.
Неизвестная стоимость — null, а не ноль.

Evaluation: `queued|running|succeeded|failed`.
Только succeeded содержит output, original_score и effective_score;
failed содержит failure и не показывает финальный итог.
Исходные output/original_score неизменяемы. Повторная оценка создаёт новую Evaluation.

## Изменение и генерация

C предлагает изменение, A согласует смысл, B получает обновлённые типы и fixtures.
Из корня: `python scripts/export_contracts.py`,
`npm --prefix frontend run generate:api`.
Проверка без записи: экспорт с `--check` и `generate:api:check`.
Fixture `fixtures/synthetic/demo.json` генерируется тем же Python-экспортом.
Нельзя редактировать производные файлы вручную или тайно выбирать альтернативные поля.
