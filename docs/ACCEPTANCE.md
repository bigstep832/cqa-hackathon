# Приёмка: каркас и полный продукт

Статус **bootstrap** означает реализованный объём этапа 0, который требуется подтвердить
фактическими командами. **Planned** означает будущую функцию, даже если её API уже в OpenAPI.
Фактический интеграционный прогон описан в [BOOTSTRAP_REPORT](BOOTSTRAP_REPORT.md).
Он подтверждает только реализованный объём; planned-сценарии остаются задачами MVP.

| Требование | Модуль | Владелец | Проверка | Состояние |
| --- | --- | --- | --- | --- |
| Запуск health и явного synthetic demo | API/frontend | C/B | pytest + bootstrap Playwright | Bootstrap |
| Веса, шкалы, N/A, W=0, C08 | domain/config | A | domain/test_scoring.py, test_models.py | Bootstrap, ревью A |
| Секции с общим W, Decimal/rounding | domain | A | Domain regression | Bootstrap |
| Typed ответы, запрещённые значения и ссылки evidence | contracts/evaluation | A | evaluation/test_provider.py | Bootstrap |
| Mock только synthetic, live без auto fallback | evaluation/API/UI | A/C/B | Provider tests + banner smoke | Bootstrap |
| Pydantic → OpenAPI → TS, все маршруты | contracts/scripts | A/C/B | export --check, generate:api:check | Bootstrap |
| Настоящий AI анализ новых данных | evaluation | A | A-01/A-02, independent holdout | Planned |
| Импорт DEV/control по group_id и ручной JSON | services/storage/API | C | C-01 integration | Planned |
| Повтор изменённого текста создаёт новую версию | storage | C | Import unchanged/changed test | Planned |
| Evidence старой оценки открывает старый snapshot | storage/API/UI | C/B | Reimport → old evaluation → evidence | Planned |
| Checklist editor: новый criterion и изменение answer type | UI/services | B/C | B-02 + browser сценарий | Planned |
| Изменение rubric фактически меняет запрос provider | services/evaluation | C/A | Capture EvaluationInput test | Planned |
| Старая оценка после изменения rubric остаётся прежней | storage | C | Version immutability integration | Planned |
| Queued/running/succeeded/failed, restart/retry | services | C | C-03 restart test | Planned |
| Апелляция, confirm/override, reason, пересчёт | services/UI | C/B | C-04 + browser flow | Planned |
| Исходный AI immutable, decision атомарен и идемпотентен | storage/services | C | Duplicate/conflict/rollback tests | Planned |
| Аудио доступно, offset не выдумывается | import/UI | C/B | Call fixture, missing mapping | Planned |
| RU/KZ/EN без повреждения и смысловая оценка | Весь pipeline | A/B/C | Unicode import/UI + human eval | Planned |
| Новый технический ID не меняет решение | evaluation | A | Metamorphic eval | Planned |
| Инструкция в transcript не подменяет rubric | evaluation | A | Adversarial synthetic eval | Planned |
| Публичный repo, README, запуск другой машины | release | C | C-05 clean setup | Planned |
| Контроль 23ч, freeze24ч, demo ≤5мин | release | C + команда | DEMO checklist | Planned |

## Обязательная сдача JSON · P0

Спецификация и структурная схема готовы в A-04, но это не реализованный экспорт
или сданные результаты. C-06 остаётся planned; A проверяет смысл после реализации.
Точные правила — [RESULTS_SUBMISSION](RESULTS_SUBMISSION.md).

| Требование | Владелец | Проверка | Состояние |
| --- | --- | --- | --- |
| Отдельный формат 1.0 и mapping восьми критериев | A | JSON Schema + вектор PDF 85.38 | Spec-ready; интеграционное ревью впереди |
| DEV 65 (60+5), PROD 10, точные исходные ID без пропусков/дублей | C | Независимый source manifest; negative ID/count cases | Planned, C-06 |
| Типы, raw scales, N/A, числовой final_score и C08 | C + A review | Схема, общий scorer, сверка original_score | Planned, C-06/A-04 |
| Evidence 1-based по всем ролям и нужному snapshot | C + A review | Диапазоны, чужой snapshot, верхняя граница | Planned, C-06/A-04 |
| Original AI и официальный snapshot; не appeal/custom rubric | C + A review | История и несовместимая рубрика | Planned, C-06/A-04 |
| Ошибки сохраняют ID, criteria=[], score=null; нет mock/подмен | C | Failed/missing/timeout + provenance tests | Planned, C-06 |
| Отдельный офлайн-валидатор и одна команда export→validate→report | C | Репетиция synthetic вне официальных путей, без сети/платных вызовов | Planned, C-06 |
| Rationale/error без персональных данных, секретов и traces | A/C | Ручное ревью плюс public-file guard | Planned |
| Оба JSON commit + push и доступны в сдаваемой ветке до 24ч | C | Открыть оба файла по переданной ссылке и сверить публичный SHA | Planned, C-05 |

Один `check_public_files.py`, наличие двух файлов или успешная JSON Schema
не доказывают корректность результатов. Формат, полное множество ID, смысл,
происхождение, приватность и доступность проверяются отдельно.

## Обязательные сквозные сценарии полного MVP

1. Import → выбранные conversation/checklist versions → evaluate → результат.
2. Evidence → нужное сообщение; для звонка запись доступна.
3. Appeal → review с причиной → effective score; original result сохранён.
4. Новый критерий **и** изменение типа существующего через UI → новая версия →
   фактический provider input использует её → output соответствует новому набору/типам →
   старые оценки неизменны. Затем проверить вес, шкалу и N/A.
5. Новый group_id и неизвестные ID проходят тот же процесс.
6. RU/KZ/EN работают в импорте, отображении и оценке.
7. Перезапуск сохраняет данные/версии/решения и не оставляет running бесконечно.
8. Демонстрация всех обязательных шагов укладывается в пять минут.

Bootstrap Playwright покрывает demo/evidence, а не все восемь сценариев.
За новые e2e отвечает B с API-подготовкой C. A проверяет качество, а не тестирует
за участников весь UI/storage.

## Команды и отчёт

`python scripts/verify.py` — офлайн статические/модульные/контрактные/frontend проверки.
`npm --prefix frontend run test:e2e` — отдельный браузерный прогон.
`python scripts/check_public_files.py` — техническая помощь в проверке публикации,
не доказательство отсутствия всех секретов; staged diff просматривается отдельно.

В отчёте укажите команду, результат, причину пропуска, состояние функции и следующий task.
501, mock, отсутствующий API-ключ и отсутствующий браузер Playwright должны быть
отличимы от пройденной функциональной проверки.
