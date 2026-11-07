---
name: cqa-rubric-validation
description: Проверять изменения рубрики, scoring или пересчёта апелляций CQA по общей спецификации и regression tests.
---

Применяй при изменении критериев/шкал/N/A/формулы или подключения scorer к апелляциям.

Прочитай [SCORING_SPEC](../../../docs/SCORING_SPEC.md) и затронутый критерий
в каноническом `config/checklists/default.v1.json`; типы находятся в
`backend/app/contracts/models.py`.

Проверь N/A в числителе и знаменателе, допустимые ступени, явные quality,
отдельные flags, W=0, Decimal/ROUND_HALF_UP и секции с общим W.
Override использует тот же валидатор и scorer; original AI не переписывается.
Изменённая rubric создаёт новую версию, а не меняет смысл старых результатов.

Запусти `python -m pytest backend/tests/domain backend/tests/evaluation`
из корня с project .venv; для апелляций также затронутые workflow tests.
При изменении DTO/config обнови производные artifacts штатными командами README.
Сообщи реально проверенные случаи и результат, отличая scorer regression от качества AI.
