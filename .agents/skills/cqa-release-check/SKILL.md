---
name: cqa-release-check
description: Проверять готовность CQA перед merge, release или demo, различая bootstrap и полный MVP и фиксируя сдаваемую версию.
---

Прочитай [ACCEPTANCE](../../../docs/ACCEPTANCE.md),
[DEMO](../../../docs/DEMO.md) и границы текущей задачи.
Выбери относящиеся к изменению проверки; не объявляй planned функции готовыми
по наличию DTO или HTTP 501. Synthetic mock всегда явно отличается от live.

Для интеграции используй `python scripts/verify.py` и отдельный
`npm --prefix frontend run test:e2e`. Укажи реальные результаты и причины пропусков.
До публикации проверь `python scripts/check_public_files.py`, tracked files
и staged diff; не добавляй PDF, секретные URL/.env, DEV/control, аудио, traces или SQLite.
Не используй слепой git add -A в неизвестном checkout.

При фактической сдаче зафиксируй commit и выполни `python scripts/freeze.py` с параметрами из DEMO;
не запускай freeze при обычном merge. После стоп-кода код не меняется.
Подготовь и проверь ссылку на согласованный публичный repo; внешнюю отправку
организаторам выполняет участник или отдельно уполномоченный инструмент.
