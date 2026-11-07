# A-01: оценщик и воспроизводимая офлайн-проверка

Пользователь A разрешил офлайн-реализацию универсального адаптера. Облачная модель,
бюджет и передача диалогов внешнему провайдеру ещё не согласованы. Реальные запросы
не выполнялись. Эта работа не подтверждает accuracy и не завершает серверный workflow C.

## Общий интерфейс

`EvaluationProvider.evaluate(EvaluationInput) -> EvaluationOutput` остаётся прежним.
Новые DTO, ручные TS-типы и изменения UI/API не нужны. В `evaluation/factory.py`
`create_provider(ProviderConfig(...))` выбирает реализацию:

| provider | Реализация | Настройка |
| --- | --- | --- |
| `fixture` (по умолчанию) | `MockEvaluationProvider` | Только bundled synthetic example, mode=mock |
| `gemini` | Google generateContent | API key + точный model в settings |
| `openai` | OpenAI Chat Completions с JSON Schema | API key + совместимый model в settings |
| `local` | OpenAI-совместимый локальный сервер | Явный loopback base URL, model; JSON Schema или явно выбранный JSON object |

Пример создания **без сетевого запроса**:

```python
from app.evaluation.factory import ProviderConfig, create_provider

offline = create_provider()  # fixture; никаких облачных defaults
local = create_provider(ProviderConfig(
    provider="local", local_base_url="http://127.0.0.1:11434/v1",
    local_response_format="json_object",
))
# await local.evaluate(input) уже является реальной оценкой и здесь не запускался.
```

Локальный сервер должен поддерживать выбранный формат OpenAI API. Это адаптер протокола,
а не установка Ollama/vLLM и не обещание совместимости с любой моделью. Для облака
необходимо отдельно проверить поддержку structured output выбранным model ID.
Автоматического discovery, скачивания моделей и перехода в mock при ошибках нет.

`structured.py` содержит одну общую инструкцию, динамическую схему и parser.
Передаются все текущие критерии, значения, описания ступеней и условия N/A.
Диалог находится отдельно как недоверенные данные; его команды не меняют рубрику.
Модель не получает внешние conversation/dataset IDs, audio URL или готовые ответы.
Ответ содержит только ответы, краткие объяснения и message IDs; snapshot evidence
привязывается сервером. Полнота, типы, значения и диапазоны проверяются до scorer.
Проверка существования evidence **не доказывает его смысловую корректность**.

Адаптеры учитывают попытки, timeout, ограниченные повторы временных ошибок и доступный
token usage. Общая ошибка `ProviderExecutionError` сохраняет calls даже при неуспехе;
сообщения ошибок не включают ключи и ответы провайдера. Неизвестные usage/cost — null.
Невалидный JSON, refusal и недопустимые ответы дают failed, без нулевых оценок и mock.

## Результат review scorer

При сверке исходной рубрики с PDF расхождений не обнаружено; 46 существующих доменных
регрессионных тестов прошли. Веса, шкалы C04/C06, N/A и отдельный C08 соответствуют
согласованной спецификации. Изменять scorer или исходный snapshot не понадобилось.
В C07 требуется `(итог ИЛИ дальнейшая помощь) И прощание` — это смысловое правило для
разметки, а не дополнительная ветка формулы. Для новых критериев действует текущая
конфигурация, а не hardcoded C01–C08. Чатовая рубрика для звонков и отсутствующие
регламенты проверки C04 остаются внешними неопределённостями.

## Ручные эталоны

См. [evals/README](../evals/README.md): подготовка пустых форм, разделение по группам
сценариев и проверка завершённой ручной разметки. Это эталоны команды, не официальный
Ground Truth. Результат другой LLM не становится эталоном автоматически.

## Будущий live runner

`evals/live.py` по умолчанию только проверяет локальные файлы, не создаёт HTTP-клиент,
не требует ключ и не отправляет диалоги. Вход — JSON-массив канонических EvaluationInput;
модель, provider и mode=live должны совпадать с отдельным планом. Файл плана содержит
LivePlan: provider/model, `data_transfer_approved`, dataset_group_ids, max_cases,
max_requests, max_attempts, max_output_tokens, budget_usd и pricing. Для local также
local_base_url и при необходимости local_response_format.

В pricing вручную фиксируются input_token_limit/output_token_limit выбранной модели,
максимальные input_usd_per_million/output_usd_per_million для подходящих тарифных
ступеней, официальный source_url, verified_on и valid_until. Для thinking-модели цена
и выходной лимит должны учитывать reasoning tokens. Пустые/просроченные сведения
не дополняются предположениями. Сначала согласовать модель, бюджет и передачу данных
с пользователем; наличие полей в JSON само по себе такого согласования не заменяет.

Резерв на каждую HTTP-попытку:

```text
(полный input_token_limit × максимальная цена input
 + max_output_tokens × максимальная цена output) / 1 000 000
```

План принимается, только если бюджет покрывает все max_requests по этому верхнему
расчёту. Это консервативная оценка при корректных вручную проверенных ценах/лимитах,
не банковский лимит и не гарантия биллинга провайдера. Реальная стоимость остаётся null.
Запросы считаются строго, включая retries. Резерв пишется на диск **до** запроса;
при timeout/ошибке он не возвращается. Используются только текстовые запросы,
без tools, поиска, caching, загрузки аудио и дополнительных оплачиваемых функций.

```powershell
$env:PYTHONPATH = 'backend'
# Только проверка без сети; файлы сначала создаются локально после согласования.
.\.venv\Scripts\python.exe -m evals.live --inputs data/private/inputs.json --plan data/private/live-plan.json --output artifacts/private/baseline-001
```

Реальный запуск дополнительно требует `--execute`, `LIVE_EVAL_ENABLED=true` и локального
`MODEL_API_KEY` для облака. Ключ не сохраняется в плане или результатах. Эта команда
не запускалась. Результаты: immutable inputs, plan, ledger, predictions и calls всех
попыток, включая failed. Каждый прогон создаёт новый каталог под artifacts/private;
перезапись и автоматический resume запрещены. Повторный прогон — отдельный расход.
API/jobs C должны сохранять failed calls отдельно, поскольку общий Evaluation DTO
не содержит output у failed. Подключение runner к API в эту задачу не входит.

## Проверки и следующий шаг

Проверки адаптеров используют `httpx.MockTransport`; это не реальные оценки и не
доказательство правильности ответов LLM. MockEvaluationProvider остаётся ограничен
синтетическим примером. Проверки роли A:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/domain backend/tests/evaluation
.\.venv\Scripts\python.exe -m ruff check backend/app/evaluation backend/tests/evaluation evals
.\.venv\Scripts\python.exe -m mypy backend/app
```

На интегрированной ветке A-01 выполнено: **202 pytest tests passed**, Ruff без замечаний,
`mypy backend/app evals` — 25 source files без ошибок. CLI `--help` проверен с
`PYTHONPATH=backend`; тест runner по умолчанию подтвердил отсутствие сетевого вызова.
Все HTTP-ответы в тестах синтетические. Каталоги frontend, API, services и storage,
а также общие DTO, рубрика и scorer не изменены.

Перед первым реальным прогоном: помочь A сравнить доступные модели по цене и возможностям,
согласовать лимит и право передачи данных, затем выполнить малый baseline и измерить
совпадение с независимо подготовленной ручной разметкой. Решение о модели принимается
по качеству и цене на нашем наборе, а не по обещанному проценту accuracy.

Документация протоколов:
[Gemini generateContent](https://ai.google.dev/api/generate-content),
[Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output),
[Gemini thinking/token limits](https://ai.google.dev/gemini-api/docs/generate-content/thinking),
[OpenAI structured output](https://platform.openai.com/docs/guides/structured-outputs),
[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create).
