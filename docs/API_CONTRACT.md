# Контракты API и оценщика

Источники истины:
[канонические DTO](../backend/app/contracts/models.py) →
[OpenAPI](../contracts/openapi.json) →
`frontend/src/api/generated/`.
Документ объясняет семантику; точные поля, required/nullability, ограничения
и response schemas берутся из DTO/OpenAPI, а не из вручную скопированных TS-интерфейсов.

## Маршруты

Все пути объявлены в bootstrap. Только health и demo выполняют полезную операцию;
остальные возвращают **HTTP 501 / NOT_IMPLEMENTED** после проверки запроса.

| Метод и путь | Request → успешный response DTO | Целевая операция |
| --- | --- | --- |
| GET /health | — → HealthResponse | Проверка запуска, реализована |
| GET /api/v1/demo | — → DemoPayload | Synthetic mock, реализован |
| POST /api/v1/datasets/import | DatasetImportRequest → ImportResponse | Импорт настроенного источника по group_id |
| POST /api/v1/conversations/import | ConversationImportRequest → ImportResponse | Ручной JSON |
| GET /api/v1/conversations | query → ConversationList | Список, offset/limit |
| GET /api/v1/conversations/{conversation_id} | path → ConversationVersion | Последняя версия внешнего диалога |
| GET /api/v1/conversation-versions/{version_id} | path → ConversationVersion | Конкретный исторический snapshot |
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
заглушка — 501 NOT_IMPLEMENTED. Неизвестная сущность — 404, конфликт решения — 409;
точные response schemas фиксирует OpenAPI. Будущие 2xx schemas описывают соглашение,
а не текущую готовность workflow. Ошибка провайдера не должна давать фиктивный успех.

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
{"error":{"code":"NOT_IMPLEMENTED","message":"Операция ещё не реализована.","details":[]}}
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
