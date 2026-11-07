# Офлайн-проверка качества

`compare.py` — минимальный диагностический инструмент для A, без сетевых запросов.
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
.\.venv\Scripts\python.exe evals/compare.py --references data/private/references.json --predictions artifacts/private/predictions.json
```

macOS/Linux:

```sh
PYTHONPATH=backend .venv/bin/python evals/compare.py --references data/private/references.json --predictions artifacts/private/predictions.json
```

Отчёт содержит совпадение ответов, MAE итогового балла, N/A, число failed/pending
и разбивку по критериям. Метрики рассчитаны только по завершённым валидным оценкам;
знаменатели и неуспешные попытки указаны отдельно. Это не формула жюри.
Смысл evidence, ошибки по языкам/каналам, стоимость всех вызовов и спорные разметки
разбираются по плану `docs/AI_EVALUATION_PLAN.md`, а не объявляются этим скриптом проверенными.

Эталоны команды не являются Ground Truth организаторов. Готовые реальные эталоны
не включены. Храните разметку в `data/private`, результаты в `artifacts/private`.
Переводы одного сценария держите в одном split. Holdout не используйте для постоянной
настройки. Сначала сравните один baseline; Jev и перепроверку добавляйте по результатам.

## Входные результаты

Перед сравнением predictions должны пройти `validate_output` с соответствующими
snapshots диалога и чек-листа. Одна `Evaluation.model_validate` проверяет структуру,
но не доказывает допустимость ступеней и корректность evidence. `criterion_accuracy`,
`score_mae` и `reference_na_count` относятся к успешно завершённой сравниваемой части;
`failed` и `pending` показываются отдельно и должны быть отражены в отчёте качества.
