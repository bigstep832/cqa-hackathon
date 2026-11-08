# Офлайн-проверка качества

## Подготовка ручных эталонов

Начните с [памятки разметчика DT20](ANNOTATION_GUIDE.md): правила C01–C08,
спорные случаи, независимая проверка человеком и порядок работы с holdout.

Для работы без ручного редактирования JSON доступен [локальный инструмент разметки
в браузере](REVIEW_APP.md): выбор диалогов и групп, предпросмотр и фиксация split,
ответы с evidence, история изменений и отдельный экспорт tune/holdout.

`prepare.py` создаёт **пустой draft ручной разметки команды**, фиксирует tune/holdout
и проверяет завершённый человеческий ввод. Скрипт не вызывает модели, не импортирует
mock-ответы и не превращает DEV либо синтетический пример в готовый Ground Truth.
`origin` всегда `team_manual`; официальный `organizer` этим инструментом не создаётся.

Вход `inputs.json` — JSON-массив канонических `EvaluationInput` с полными immutable
snapshots диалога и чек-листа. Подготовьте его после разрешённого импорта. Сырой ответ
организаторского API имеет другой формат: этот инструмент не подменяет импорт C.
Настройки provider/model сохраняются как часть исходного документа, но не участвуют
в выборе split, хэше snapshots, проверке человеческих ответов или расчёте.

Отдельный `scenario-groups.json` задаёт **явные** группы для каждого conversation version ID:

```json
[
  {"conversation_version_id":"tariff-ru-v1","scenario_group":"tariff-question"},
  {"conversation_version_id":"tariff-kz-v1","scenario_group":"tariff-question"},
  {"conversation_version_id":"closing-en-v1","scenario_group":"operator-closing"}
]
```

Это иллюстративные ID, не записи DEV. Группировку задаёт человек **до настройки**:
переводы и вариации одного сценария получают одну группу. Скрипт не угадывает
сходство по ID или тексту. Проверяются отсутствующие/лишние/повторяющиеся ID,
пустые группы, повторяющиеся пары snapshots и противоречивое содержимое под одним
version ID. Для полностью совпадающего content hash дополнительно запрещены разные
группы. Эта точная проверка не обнаруживает все переводы и перефразирования.

Из корня репозитория в PowerShell:

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m evals.prepare prepare --inputs data/private/inputs.json --groups data/private/scenario-groups.json --seed 42 --holdout-groups 3 --output data/private/annotation-draft.v1.json
```

Нужны минимум две группы. `--holdout-groups` задаёт число **групп**, не диалогов.
Группы ранжируются через SHA-256 от seed и имени; входной порядок не влияет на
разбиение. Скрипт выводит фактическое число диалогов каждой части. Например, 15+9
получится для 24 отдельных групп при `--holdout-groups 9`; для групп с переводами
размеры иные. Проверьте покрытие RU/KZ/EN и каналов до экспериментов. Не меняйте
группы/seed после просмотра результатов, чтобы получить более удобный holdout.

Draft сохраняет полные snapshots, их `input_hash`, seed, split, scenario_group и
строку для **каждого** актуального критерия:

```json
{
  "criterion_id":"C04",
  "answer":null,
  "explanation":null,
  "evidence":[],
  "ambiguity_note":null,
  "resolution_note":null
}
```

Человек заполняет answer по каноническому типу (`boolean`, `scale`, `choice` либо
разрешённый `na` с причиной), объяснение и evidence с существующим диапазоном
сообщений именно этого snapshot. Оценивайте оператора, а не бота; для отсутствующего
действия указывайте проверенный участок. Не редактируйте transcript/checklist,
набор criterion IDs или split при разметке — их изменения требуют новой версии.

В `review` заполните `annotated_by`, `completed_at` с часовым поясом и
`language_understood: true` только если разметчик действительно понимает язык.
**KZ размечает человек, понимающий казахский**; флаг — явное человеческое подтверждение,
а не автоматическая проверка владения языком. Ответ другой LLM без проверки человеком
не является эталоном.

Спорный критерий получает `ambiguity_note`. Перед экспортом нужны `resolution_note`
и независимый второй человек в `reviewed_by` с `reviewed_at` не раньше завершения
разметки. Не выдумывайте тарифы/правильные ответы при отсутствии регламента: сохраните
спорный случай незавершённым до решения. По звонкам учитывайте открытые вопросы
применения чатовой рубрики. Проверка ссылок и заполненности не доказывает правильность
человеческого решения — смысл проверяет команда.

Проверить и экспортировать выбранную часть:

```powershell
.\.venv\Scripts\python.exe -m evals.prepare validate --draft data/private/annotation-draft.v1.json --split tune
.\.venv\Scripts\python.exe -m evals.prepare finalize --draft data/private/annotation-draft.v1.json --split tune --output data/private/references-tune.v1.json
```

Экспорт — массив `ReferenceRecord`, совместимый с `compare.py`; `total` вычисляет
общий domain scorer, человек его не вводит. N/A, допустимые ступени, полный набор
критериев и принадлежность evidence проверяются. Незаполненная часть не экспортируется
как нули/Нет/N/A. Finalize выбранной части не требует завершения разметки другой части.

**Сохраните заполненный draft вместе с экспортом**: он содержит полные snapshots,
объяснения, evidence, неоднозначности и сведения о людях. Компактный `ReferenceRecord`
для метрик этих полей не дублирует. Статус исходного документа остаётся `draft`, чтобы
его нельзя было по одному имени/полю принять за официальный проверенный Ground Truth.
Для holdout используйте отдельный экспорт `--split holdout` только на отложенной
проверке. Подготовка файла не обеспечивает техническую изоляцию holdout: не используйте
его ответы для постоянного изменения prompts; после такой настройки независимость утрачена.

Все CLI-входы и выходы допускаются только внутри `data/private` или `artifacts/private`
**этого репозитория**. Перезапись существующего выходного файла запрещена; выберите
новое имя версии. Выход за private-каталоги через `..` или symlink отклоняется.
Ошибки CLI не печатают содержимое невалидного приватного JSON.

Офлайн-проверки (без сети и API-ключей):

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/evaluation/test_reference_preparation.py
.\.venv\Scripts\python.exe -m ruff check evals/prepare.py backend/tests/evaluation/test_reference_preparation.py
.\.venv\Scripts\python.exe -m mypy evals/prepare.py
```

## Сравнение исходных автоматических оценок

`compare.py` — диагностический инструмент для A, без сетевых запросов.
Вход predictions — JSON-массив канонических `Evaluation`; используется только
`original_score`, до апелляций. References — массив `ReferenceRecord`, схема находится
в том же модуле. Одна пара snapshots имеет ровно одну выбранную оценку и один эталон.

Поля разметки: `conversation_version_id`, `checklist_version_id`, `language`, `channel`,
`origin` (`team_manual` или `organizer`), `split` (`tune` или `holdout`), `scenario_group`,
`answers` (criterion_id и типизированный answer), `total`. Причина N/A обязательна,
но её дословное совпадение не влияет на классификационную метрику.

Пример запуска из корня в PowerShell:

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m evals.compare --references data/private/references-tune.v1.json --predictions artifacts/private/predictions-tune.json --output artifacts/private/quality-tune.v1.json
```

macOS/Linux:

```sh
PYTHONPATH=backend .venv/bin/python -m evals.compare --references data/private/references-tune.v1.json --predictions artifacts/private/predictions-tune.json --output artifacts/private/quality-tune.v1.json
```

Входы и выход CLI разрешены только внутри `data/private` или `artifacts/private`
этого репозитория. `--output` создаёт новый файл без перезаписи и выводит только
подтверждение. Без этого параметра полный приватный отчёт выводится в stdout;
не помещайте его в публичные логи. Прямой вызов `python evals/compare.py` также поддержан.

Отчёт версии 2.0 сохраняет прежние поля и добавляет:

- таблицы ошибок `per_criterion.*.confusion`: expected/predicted/count с типами
  ответов и исходными ступенями C04/C06, без смешивания с взвешенными баллами;
- `na_detection`: ложные и пропущенные N/A, precision/recall/F1;
- `boolean_true` для критериев, где наблюдались только boolean-ответы; для исходного
  C08 положительный класс — нарушение. При нулевом знаменателе метрика null;
- разбивку `by_language`, `by_channel`, `by_checklist`, включая failed/pending;
- доли success/failure/pending от всех пар; долю полностью совпавших диалогов
  среди succeeded; несовпадения null/non-null итогов отдельно от числового MAE;
- `disagreements` с IDs оценок/версий, кодом критерия и ответами, без пояснений
  и транскриптов; `successful_prediction_identity` с mode/provider/model и версиями.

Один отчёт не смешивает tune с holdout или разные mode/provider/model/instruction/pipeline
среди succeeded. Для другой модели и для holdout нужны отдельные файлы и отчёты.
При всех failed идентичность модели здесь неизвестна, поэтому равна null.
При разных версиях рубрики смотрите `by_checklist`, а не только общий итог.

Accuracy и MAE считаются по succeeded; знаменатели и failed/pending указаны отдельно.
Проверяйте их вместе: точность 1 на одном succeeded при остальных failed не означает
успешный прогон. Отчёт mock — только техническая репетиция, не точность AI.
Смысл evidence, стоимость всех попыток и правильность ручных эталонов проверяются
отдельно по `docs/AI_EVALUATION_PLAN.md`. Формула жюри не заявляется известной.

Эталоны команды не являются Ground Truth организаторов. Готовые реальные эталоны
не включены. Храните разметку в `data/private`, результаты в `artifacts/private`.
Переводы одного сценария держите в одном split. Holdout не используйте для постоянной
настройки. Сначала сравните один baseline; Jev и перепроверку добавляйте по результатам.

Если человек проверил и подтвердил AI-черновик, укажите
`origin="team_human_confirmed_ai_draft"`. Этот отдельный источник сохраняется в
`reference_origins` отчёта; его нельзя переименовывать в независимую ручную разметку
или `organizer`. Подтверждение одного человека не заменяет второго проверяющего
спорных случаев. Строгий finalizer ручной разметки не ослаблен; сравнение с таким
черновиком — отдельная диагностическая процедура с явно описанными ограничениями.

## Входные результаты

Перед сравнением predictions должны пройти `validate_output` с соответствующими
snapshots диалога и чек-листа. Одна `Evaluation.model_validate` проверяет структуру,
но не доказывает допустимость ступеней и корректность evidence. `criterion_accuracy`,
`score_mae` и `reference_na_count` относятся к успешно завершённой сравниваемой части;
`failed` и `pending` показываются отдельно и должны быть отражены в отчёте качества.
