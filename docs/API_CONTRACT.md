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
| GET /api/v1/conversation-versions/{version_id}/history/versions | offset, limit → items, total, offset, limit | Все версии того же диалога, начиная с новой |
| GET /api/v1/conversation-versions/{version_id}/history/evaluations | offset, limit → items, total, offset, limit | Все задания этого диалога, включая задания прежних версий |
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
Ручной JSON с повторяющимся полем отклоняется целиком с 422. При чтении диалога
кодируйте внешний ID для URL (например, `case/RU-17` как `case%2FRU-17`).
Если offset превышает размер списка, сервер возвращает пустой массив и фактический total.
При ошибке оценки клиент получает сохранённый статус failed и причину.

При импорте группы ответ организатора должен содержать `data.dataset_group_id`,
`data.total_records` и `data.records`. Backend сверяет первую величину с запрошенной
группой, а целочисленное количество — с длиной массива. Отсутствие поля или расхождение
возвращает 502 до записи. Состав предыдущего успешного импорта остаётся доступен.

Повтор импорта сравнивается с последней версией того же источника, группы и внешнего ID.
Изменение создаёт новую версию. Весь пакет и манифест группы меняются одной транзакцией.
GET по внешнему ID показывает последнюю сохранённую версию, GET по ID версии — запись,
которую видел оценщик. Исходный адрес аудио хранится в приватной БД. Для воспроизведения
задайте CQA_AUDIO_ORIGINS и настройте CQA_PUBLIC_API_BASE_URL до импорта.

Источник может вернуть прямое аудио либо HTML-плеер с одним `<audio src>` или
одним `<source src>` внутри `<audio>`. В `CQA_AUDIO_ORIGINS` явно разрешаются
оба HTTPS origin: страница и файл записи. HTML не исполняется; из него извлекается
только одна абсолютная ссылка, затем её origin проверяется независимо. Страница
ограничена 256 KiB, файл — 64 MiB; перенаправления, несколько источников и второй
HTML-плеер отклоняются. Одиночный Range проверяется и передаётся к файлу.
Подписанные адреса, HTML и служебные upstream-заголовки клиенту не возвращаются.
Привязка исходных реплик к секундам при этом не создаётся.

POST оценок принимает необязательный заголовок Idempotency-Key. Повтор с тем же телом
возвращает то же задание, с другим телом — 409. Два обработчика выбирают задания
из SQLite. После перезапуска queued продолжаются, running переходят в failed.
У апелляции одна запись на пару «оценка, критерий». Повтор того же комментария
возвращает запись, другой комментарий даёт 409. Повтор решения с тем же ключом и
телом возвращает сохранённый ответ. Для отображения исправления B читает решения
из списка апелляций и effective_score. Исходные ответы и original_score сохраняются.

**История в карточке B.** Возьмите внутренний `latest_version_id` из списка диалогов
и запросите обе коллекции `history`. На страницах по умолчанию 25 записей, максимум 100.
Сервер находит версии по тройке «источник, группа, внешний ID», поэтому одинаковые
внешние ID, в том числе с `/`, не смешивают данные. Неизвестная исходная версия даёт
404, известный диалог без заданий — 200 с пустым списком оценок.
Список диалогов возвращает отдельную карточку для каждой такой тройки.

В списке версий каждая строка содержит `id` и `imported_at`. В списке оценок —
`id`, `conversation_version_id`, `checklist_version_id`, `mode`, `status`, `created_at`.
Выбор строки оценки открывает `GET /api/v1/evaluations/{id}`. Для evidence загрузите
именно версию диалога и чек-листа из возвращённой оценки. При открытии из чистой
вкладки весь путь доступен по GET, без сохранённого состояния вкладки и постановки
новой задачи. Для апелляций передайте тот же ID оценки в существующую форму.

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

## Профиль оператора

Источник или сотрудник через карточку указывает псевдоним оператора, фактический
исход звонка и длительность в секундах. При импорте запись может содержать, например,
`"operator":{"operator_id":"Смена-02","outcome":"successful","duration_seconds":95}`.
Поле `operator` целиком заменяет эти сведения, `null` очищает их, а отсутствие поля
сохраняет предыдущие данные. У чата допустим только псевдоним. Исправление канала
на чат очищает прежние поля звонка. Эти сведения хранятся отдельно от снимка
транскрипта и входа оценщика.

| Запрос | Результат |
| --- | --- |
| `GET /api/v1/operators` | Псевдонимы с числом диалогов и число диалогов без назначения |
| `GET /api/v1/operators/{operator_id}/profile` | Нагрузка, исходы, время, оценка и список исходных диалогов |
| `GET /api/v1/conversation-versions/{version_id}/operator` | Текущее назначение диалога |
| `PUT /api/v1/conversation-versions/{version_id}/operator` | Сохранение полного назначения, только из актуальной версии (409 при переимпорте) |

Один диалог определяется источником, группой и внешним ID. В текущем профиле он
считается один раз по последней импортированной версии. Переназначение переносит
его между профилями. Статистика охватывает импортированные диалоги.

- Доля успеха относится к звонкам с известным исходом. Неизвестные учитываются отдельно.
- Длительность — указанное время звонка. В среднем участвуют только измеренные
  звонки, включая подтверждённую нулевую длительность. Отдельно рассчитывается
  интервал от первой до последней реплики звонка, если есть минимум две реплики
  с сопоставимыми последовательными метками. Этот интервал может быть короче
  звонка. Чистое время речи оператора требует данных о длительности его реплик.
- Первая реакция — время от первого обращения клиента до первой следующей реплики
  оператора при сопоставимых метках. Отдельно видны диалоги, где ответ отсутствует.
- Для качества берётся последняя успешно завершённая оценка в режиме live текущей
  версии. После решения по апелляции профиль показывает действующий балл. Повторные
  оценки не увеличивают число диалогов. Средние по критериям относятся к конкретной
  версии чек-листа, N/A исключается. Грубая ошибка показывается отдельным счётчиком.
  Из списка можно открыть оценку с исходным транскриптом и подтверждающими репликами.
