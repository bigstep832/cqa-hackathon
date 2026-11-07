# Инструменты и доступность

Это снимок среды этапа 0, а не требование установить все перечисленные инструменты.

| Инструмент | Состояние / назначение |
| --- | --- |
| Windows / PowerShell | Основная среда всех трёх участников |
| Python 3.13.2 | Выбран для проекта; локальная .venv |
| pip-tools / requirements.lock | Закрепление Python зависимостей в корне |
| Node 24.16.0 / npm 11.13 | Frontend; один package-lock.json |
| Git | Доступен, локальные commits/branches |
| uv | Отсутствует; проект использует venv + pip-tools |
| gh | Отсутствует; GitHub publication ещё не подтверждена |
| GitHub MCP | Подключён, identity bigstep832 подтверждена; read/push доступны, create repo отсутствует |
| pytest / ruff / mypy | Офлайн backend/domain проверки |
| Vitest / Playwright | Обычные project dependencies; это не MCP |
| PDF runtime | pypdf/pdfplumber и Poppler использованы для сверки всех PDF |
| MCP AIDesigner, Figma, Sites, Vercel, browser tools | Доступны в среде Codex, не являются runtime-зависимостями CQA |
| OpenAI Docs / PDF / frontend-design / ui-ux-pro-max / skill-creator | Skills применяются по задаче; наличие skill не означает доступ к API модели |

## Разделение уровней

MCP/CLI/skills помогают ИИ разрабатывать проект. Provider API используется
CQA-приложением при настоящей оценке. Подключённый MCP не заменяет ключ модели.
Отсутствие live API или gh не блокирует локальный bootstrap. Для создания репозитория
используется browser fallback: подключённый GitHub MCP не имеет create-repo операции.

В repo созданы только проектные skills:
`cqa-rubric-regression`, `cqa-provider-evaluation`, `cqa-release-check`
в `.agents/skills/`. Они ссылаются на общие документы и не установлены глобально.
Если среда ИИ их не обнаруживает, прочитайте нужный SKILL.md явно.

## Команды проекта

- `python scripts/export_contracts.py`: OpenAPI и synthetic demo.
- Тот же скрипт с `--check`: обнаружение устаревших производных файлов без записи.
- `npm --prefix frontend run generate:api` / `generate:api:check`: TS из OpenAPI.
- `python scripts/verify.py`: общий офлайн набор.
- `npm --prefix frontend run test:e2e`: отдельный Playwright.
- `python scripts/check_public_files.py`: проверка публичного набора файлов.
- `python scripts/freeze.py`: при сдаче записывает local git SHA и версии
  в исключённый из Git `artifacts/private/freeze.json`; сейчас freeze не выполняется.

Точные результаты команд фиксирует bootstrap-отчёт. Проверка запусков
должна выполняться установленной project .venv, а не случайным глобальным Python.

## Подключения

Пользователь поручил создать новый repo; выбрана цель `cqa-hackathon`, публичный,
под подтверждённым владельцем bigstep832. Создание и push ещё нужно подтвердить
фактическим результатом. Пароли/2FA/токены в чате не запрашиваются;
используется штатная авторизация. Не устанавливать внешние интеграции только ради списка.

Провайдер/модель, допустимость передачи данных и бюджет ещё открыты.
Пока live-бюджет 0, платных вызовов нет.
