# project-doc-agent

CLI-агент для сборки документации проекта из **DOCX/MD/TXT** в Markdown,
удобный для **Obsidian** и **RAG**. Пользователь задаёт папку исходников или
цель, агент сам читает документы, извлекает факты, группирует их в карточки,
формирует отчёты, проверяет стандарт и показывает diff.

Репозиторий: `Week1Task34-project-doc-agent`.

## Назначение

Приложение решает три сценария:

1. **Папка документов → документация.** `docagent ingest` строит карточки,
   umbrella проекта, индекс и отчёты об источниках, вопросах, конфликтах и
   ручной проверке.
2. **Поиск компонента/API/метода по нескольким файлам.** `docagent search`
   индексирует DOCX/MD/TXT в SQLite FTS5, поддерживает regex и контекст,
   опционально ищет по сгенерированным карточкам.
3. **Проверка карточек и подготовка diff.** `docagent validate` проверяет
   front matter, slug, обязательные H2, пустые разделы, placeholder-текст,
   wiki-ссылки, дубли ID, umbrella и противоречия; `--apply` применяет
   безопасные исправления после успешной валидации.

Дополнительно `docagent run` — агентный режим: пользователь формулирует цель,
планировщик возвращает строгий JSON-план, исполнитель валидирует шаги и
выполняет только инструменты из белого списка.

## Что не входит в MVP

- анализ кода 1С, BSL, XML конфигураций и расширений;
- PDF, сканы, OCR, XLSX, PPTX;
- распознавание изображений и диаграмм;
- embeddings, векторный поиск и RAG-индексатор;
- MCP-сервер, Streamlit и веб-интерфейс;
- многопользовательская работа и аутентификация;
- автоматическая публикация в GitHub;
- совместимость с форматом `docgen-1c` — формат самостоятельный.

## Требования

- Python **3.11+** (проверено на Python 3.14).
- Доступ к облачному OpenAI-совместимому провайдеру. Живьём тестируется
  **DeepSeek**; OpenRouter и Mistral реализованы как адаптеры и покрыты
  mock-тестами.
- Локальные модели не используются.

## Установка

```powershell
cd C:\DocumentWork\project-doc-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Заполните в `.env` только нужный ключ:

```text
DEEPSEEK_API_KEY=...
# OPENROUTER_API_KEY=...
# MISTRAL_API_KEY=...
```

`.env`, `docagent.yaml`, `cards/`, `reports/`, `.docagent/` и исходные
документы клиента **не коммитятся**.

Проверка:

```powershell
docagent --version
docagent --help
```

Если `docagent` не найден в PATH:

```powershell
python -m project_doc_agent --help
```

## Быстрый старт

Все пользовательские данные лежат в `C:\DataProject`. Vault Obsidian:

```text
C:\DataProject\project-doc-agent
```

Рабочая папка проекта:

```text
C:\DataProject\project-doc-agent\marta\toplog-rc
```

### `docagent init`

Интерактивно:

```powershell
docagent init --workspace "C:\DataProject\project-doc-agent\marta\toplog-rc"
```

Без вопросов:

```powershell
docagent init `
  --workspace "C:\DataProject\project-doc-agent\marta\toplog-rc" `
  --client marta `
  --client-name "ООО «Марта»" `
  --project toplog-rc `
  --project-name "Интеграция TopLog WMS — РЦ" `
  --base ut11-prod `
  --profile integration `
  --provider deepseek `
  --source-dir "C:\DataProject\Пример задания" `
  --non-interactive
```

Создаются `docagent.yaml`, `cards/`, `reports/` и `.docagent/`.
Исходные документы не копируются и не изменяются.

### `docagent ingest`

```powershell
docagent ingest --workspace "C:\DataProject\project-doc-agent\marta\toplog-rc"
```

Полезные режимы:

```powershell
# только диагностика, без записи в cards/ и reports/
docagent ingest --dry-run

# вопросы по открытым местам задаются в CLI
docagent ingest --interactive

# другой источник или провайдер
docagent ingest --source-dir "C:\DataProject\Другой пакет" --provider deepseek
```

Пайплайн:

```text
inventory (SHA-256)
  → парсинг DOCX/MD/TXT (heading_path, block_id, таблицы)
  → outline + логические единицы
  → чанки по иерархии заголовков
  → LLM extract (JSON, Pydantic, один ремонтный запрос)
  → группировка фактов и конфликты
  → карточки + umbrella + index + отчёты
  → changes.diff → атомарная запись
```

При отсутствии данных факт получает `confidence: low`, вопрос попадает в
`reports/open_questions.md`, карточка — `review_status: insufficient_data` или
`needs_review`. Выдумывать методы, объекты и цифры запрещено промтом и
валидацией `source_refs`.

### `docagent search`

```powershell
docagent search `
  --workspace "C:\DataProject\project-doc-agent\marta\toplog-rc" `
  --query "createShipmentOrder" `
  --context 3 `
  --include-cards
```

Результат: `reports/search_report.md` и
`.docagent/runs/<run_id>/matches.json`. Для каждого совпадения возвращаются
файл, `heading_path`, тип блока, `block_id`, фрагмент и номер.

`--context N` — число соседних блоков вокруг совпадения (0…10).
`--regex` включает регулярное выражение. Поиск не использует embeddings.

### `docagent validate`

```powershell
# отчёт reports/validation.md и changes.diff
docagent validate --workspace "C:\DataProject\project-doc-agent\marta\toplog-rc"

# показать diff в консоли
docagent validate --diff

# применить только безопасные исправления после успешной валидации
docagent validate --apply
```

Проверяются:

- обязательные поля front matter и slug-формат;
- минимум один `source_ref`;
- обязательные H2 профиля и пустые разделы;
- placeholder-текст (`TODO`, `TBD`, `FIXME`, «заполнить», многоточие);
- битые wiki-ссылки;
- дубли ID и отсутствие umbrella;
- `review_status != ok` без `review_note`;
- противоречия, записанные в `reports/conflicts.md`.

Перед `--apply` создаётся backup в
`.docagent/runs/<run_id>/snapshot/`. Если после предлагаемых исправлений
остались ошибки, diff **не применяется**.


### `docagent run`

```powershell
docagent run `
  --workspace "C:\DataProject\project-doc-agent\marta\toplog-rc" `
  --goal "Разбери документы по интеграции TopLog и собери документацию для Obsidian и RAG"
```

Инструменты: `list_files`, `read_file`, `read_docx`, `outline`, `search`,
`extract`, `write_card`, `write_file`, `apply_patch`, `make_diff`, `validate`,
`diff`. Планировщик возвращает JSON-план; неизвестный инструмент — ошибка
плана. Лимиты шагов и токенов задаются в `docagent.yaml`.

```powershell
docagent run --goal "Найди createShipmentOrder" --dry-run
docagent run --goal "Продолжи сбор документации" --resume 20261018T150000Z-a1b2c3
```

При ошибке подготовленные карточки не записываются: они остаются в
`.docagent/runs/<run_id>/staged/`, а статус прогона — `failed`. `--dry-run`
запрещает запись в `cards/` и `reports/`.

### `docagent projects`

```powershell
docagent projects --root "C:\DataProject\project-doc-agent"
```

Показывает клиента, slug проекта, профиль, провайдера и число карточек.

## Конфигурация проекта

`docagent.yaml` создаётся `docagent init`:

```yaml
client: "marta"
client_name: "ООО «Марта»"
project: "toplog-rc"
project_name: "Интеграция TopLog WMS — РЦ"
bases:
  - "ut11-prod"
profile: "integration"
provider: "deepseek"
source_dir: "C:/DataProject/Пример задания"
output_dir: "cards"
reports_dir: "reports"
obsidian_folder: "marta/toplog-rc/cards"
language: "ru"
max_questions: 20
max_file_size_mb: 50
max_chunk_chars: 6000
max_steps: 12
max_tokens_total: 300000
txt_detect_headings: false
save_llm_raw: false
```

Валидация: `client`, `project`, `bases` — slug; `bases` непустой список;
`source_dir` существует; `output_dir` и `reports_dir` — относительные пути
внутри workspace; `obsidian_folder` — относительный путь внутри vault;
`provider` есть в `config/providers.yaml`.

## Провайдеры

`config/providers.yaml`:

```yaml
providers:
  - name: "deepseek"
    base_url: "https://api.deepseek.com/v1"
    api_key_env: "DEEPSEEK_API_KEY"
    model: "deepseek-chat"
    max_tokens: 8192
    temperature: 0.1
    capabilities:
      json_mode: true
      tools: false
  - name: "openrouter"
    base_url: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"
    model: "deepseek/deepseek-chat"
  - name: "mistral"
    base_url: "https://api.mistral.ai/v1"
    api_key_env: "MISTRAL_API_KEY"
    model: "mistral-large-latest"
```

- единый OpenAI-совместимый клиент;
- ключи только из `.env`/окружения, в логи и run.json не попадают;
- JSON-режим и валидация Pydantic;
- при невалидном JSON — один ремонтный запрос, затем ошибка/пропуск чанка;
- кэш ответов: хэш промта + модели + входного блока;
- токены, стоимость и длительность сохраняются в `run.json`;
- нативный tool calling не используется.



## Структура данных

```text
C:\DataProject\project-doc-agent\<client>\<project>\
├── docagent.yaml
├── cards\
│   ├── index.md
│   ├── <project-id>.md
│   └── <card-id>.md
├── reports\
│   ├── sources.md
│   ├── open_questions.md
│   ├── conflicts.md
│   ├── manual_review.md
│   ├── validation.md
│   └── search_report.md
└── .docagent\
    ├── runs\<run_id>\
    │   ├── run.json
    │   ├── inventory.json
    │   ├── outline.json
    │   ├── facts.json
    │   ├── changes.diff
    │   ├── state.json
    │   └── snapshot\
    └── cache\
```

Front matter карточки содержит `id`, клиента, проект, базы, тип, профиль,
риск, `sources`, `review_status`, `review_note`, теги и даты. `id` имеет вид
`<client>-<project>-<topic-slug>`. Карточки ссылаются на umbrella через
`parent`.

## Obsidian и Dataview

`obsidian_folder` используется в Dataview-блоке umbrella-карточки:

```dataview
TABLE title AS "Карточка", type AS "Тип", review_status AS "Проверка", updated AS "Обновлено"
FROM "marta/toplog-rc/cards"
WHERE project = "toplog-rc" AND type != "project"
SORT title ASC
```

`cards/index.md` — статический список карточек и проектов. Служебные файлы
`.docagent` в индекс не попадают.

## Воспроизводимость и прогоны

`run.json` содержит `run_id`, время, версию приложения, хэши исходников и
промтов, провайдера/модель/температуру/лимиты, вызовы инструментов, токены,
стоимость, хэши выходных файлов и статус
`completed|failed|interrupted`. Кэш LLM-ответов позволяет повторному запуску
воспроизвести те же файлы и не пересчитывать завершённые чанки. Порядок файлов
и ID стабильны. `changes.diff` — unified diff.

## Безопасность и приватность

- исходные документы **read-only**, приложение их не изменяет;
- запись разрешена только в `cards/`, `reports/` и `.docagent/`;
- пути нормализуются, выход за workspace запрещён, symlink’и за пределы
  разрешённых каталогов отклоняются;
- удаление файлов не реализовано;
- перезапись атомарная, перед `--apply` — backup;
- секреты и API-ключи не логируются;
- сырые ответы LLM не сохраняются по умолчанию; для отладки можно включить
  `save_llm_raw: true` в `docagent.yaml` или `DOCAGENT_SAVE_LLM_RAW=1`;
- в репозиторий не попадают документы клиента, `output/`, `.docagent/`,
  `docagent.yaml` и `.env`.

## Пример с синтетическими данными

В тестах используются только синтетические документы:

```powershell
python -m pytest -q
```

Фикстуры создают DOCX с заголовками, таблицей и изображением, а также MD/TXT с
противоречием между источниками. Данные клиента в тестах отсутствуют.

## Тесты и качество

```powershell
python -m pytest -q
python -m ruff check project_doc_agent tests
python -m mypy project_doc_agent
```

Покрыты: парсинг DOCX/MD/TXT, `heading_path` и `block_id`, inventory и SHA-256,
чанкинг, Pydantic-валидация, поиск по нескольким файлам, front matter и H2,
diff/`--apply`, защита от path traversal, mock-провайдеры DeepSeek/OpenRouter/
Mistral, кэш и повторный запуск, CLI `init`/`ingest`/`search`/`validate`/`run`/
`projects`, агентный режим и resume.

## Ограничения

- изображения, диаграммы и OLE-объекты не анализируются; они перечисляются в
  `reports/manual_review.md`;
- номера страниц DOCX не определяются: используются `heading_path` и
  `block_id`;
- качество карточек зависит от качества исходных документов и LLM;
- живые вызовы проверены только для DeepSeek; OpenRouter и Mistral покрыты
  mock-тестами;
- при отсутствии данных создаются открытые вопросы, а не выдуманные факты.

## Лицензия

MIT. См. `LICENSE`.

