# AGENTS.md — opencode-semsearch

> ⚠️ **КРИТИЧЕСКОЕ ПРАВИЛО — ОБНОВЛЯЙ ЭТОТ ФАЙЛ.**
>
> Этот файл — основной onboarding-документ для любого агента (человека или ИИ), касающегося этого репозитория.
> После **каждого нетривиального изменения** в коде, схеме индекса, зависимостях, командах тула или структуре проекта — обновляй соответствующий раздел этого файла в том же коммите (или сразу следующим).
> Не оставляй AGENTS.md «застывшим» — это активный документ.
>
> **Что считается нетривиальным:** новые/удалённые файлы, изменение схемы sidecar-индекса, новые CLI-команды, изменение аргументов тула, новые зависимости, изменение поведения, новые gotchas.
> **Что НЕ требует обновления:** мелкие правки стиля, рефакторинг без изменения поведения, баг-фиксы с очевидным diff'ом.

---

## Что это за репо

OpenCode custom tool `semsearch`: семантический поиск по истории сессий, локально (MLX на Apple Silicon, sentence-transformers fallback). Реализован поверх [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).

Полное пользовательское описание — в `README.md`. Архитектурные детали — в `docs/architecture.md`.

## Структура

```
opencode-semsearch/
├── README.md              ← пользовательская документация
├── AGENTS.md              ← ЭТОТ ФАЙЛ (onboarding для агентов)
├── CHANGELOG.md           ← история версий
├── LICENSE                ← Apache-2.0
├── install.sh             ← идемпотентная установка
├── uninstall.sh           ← безопасный откат
├── bin/
│   └── oc-semsearch       ← POSIX-обёртка над CLI (query/build/stats/doctor/...)
├── lib/                   ← ИСХОДНИКИ (копируются в ~/.config/opencode/tools/)
│   ├── semsearch.ts            ← OpenCode tool wrapper (Node + child_process)
│   ├── semsearch_engine.py     ← MLX / sentence-transformers + KNN + lazy index
│   └── semsearch_index.py      ← CLI build/stats/doctor/purge
└── docs/
    ├── examples.md
    └── architecture.md
```

**Точка правды — `lib/`.** Установленные в `~/.config/opencode/tools/` копии — это snapshot. После правок в `lib/` нужно перекопировать (см. ниже).

## Ключевые файлы — что где

| Файл | За что отвечает |
|---|---|
| `lib/semsearch.ts` | Имя тула = имя файла. **`description`** — то, что LLM читает чтобы решить, звать ли тул. `args` — Zod-схема аргументов. `execute()` — как дёргать Python. **При изменении API тула для LLM — править здесь.** |
| `lib/semsearch_engine.py` | `lazy_index()` (догонка), `search()` (KNN), `embed()` (MLX/sentence-transformers). Sidecar-схема — в `_ensure_index()`. **При изменении индекса или KNN — править здесь.** |
| `lib/semsearch_index.py` | CLI: `--build/--stats/--doctor/--purge/--reindex/--list-uncached`. Использует `_venv_python()` для поиска venv. |
| `bin/oc-semsearch` | POSIX-shell обёртка. Дефолт `REPO_DIR=~/gdrive/Tools/opencode-semsearch` (можно переопределить env). |
| `install.sh` | Создаёт venv в `~/.local/share/venvs/semsearch/`, ставит deps, копирует `lib/*` в `~/.config/opencode/tools/`, копирует `bin/oc-semsearch` в `~/.local/bin/`, запускает `--build`. |

## Dev-loop

### Быстрая итерация

```bash
# 1. Правь в lib/
$EDITOR lib/semsearch_engine.py

# 2. Перекопируй в установленное место
cp lib/semsearch_engine.py ~/.config/opencode/tools/

# 3. Прогон smoke-тестов
~/.config/opencode/tools/semsearch_index.py --doctor
~/.config/opencode/tools/semsearch_index.py --build
~/.config/opencode/tools/semsearch_engine.py '{"query":"three.js","top":3}'

# 4. Перезапусти OpenCode.app (⌘Q → open -a OpenCode)
#    и проверь end-to-end в TUI: «найди сессию про X»

# 5. Коммит
cd ~/gdrive/Tools/opencode-semsearch
git add -A
git commit -m "..."
git push
```

### Через `oc-semsearch` (после установки)

```bash
oc-semsearch doctor     # self-check
oc-semsearch build      # перестроить индекс
oc-semsearch stats      # сколько проиндексировано
oc-semsearch upgrade    # git pull + reinstall
```

## Конвенции

- **Python**: только stdlib + venv-пакеты (mlx-embeddings, sentence-transformers). Не добавляй в системный Python.
- **TS**: пиши совместимо и с Bun, и с Node (Electron renderer). Используй `node:child_process`, НЕ `Bun.$` (TUI падает).
- **Имя тула** = имя файла `.ts` без расширения. Переименование тула = переименование файла.
- **Sidecar DB**: только пишем туда, никогда не читаем/пишем в `~/.local/share/opencode/opencode.db` (открываем read-only через URI).
- **Комментарии в коде**: пояснительные, но не verbose. Без шуток.
- **Без новых зависимостей без нужды**: venv растёт медленно, каждый пакет = лишний вес.
- **Sidecar schema migrations**: при изменении — bump `meta.schema_version` и делай автоматический rebuild при старте (или документируй ручной `oc-semsearch purge && build`).

## Архитектура в одном абзаце

OpenCode runtime (Bun в CLI / Node в TUI) вызывает `execute()` в `lib/semsearch.ts` → `child_process.execFile(VENV_PYTHON, [engine.py, payload])` → `lib/semsearch_engine.py`:
1. Открывает `opencode.db` read-only через URI.
2. `lazy_index()` — догоняет неиндексированные сообщения (text parts ≤600 chars, strip `<system-reminder>`, embed батчами по 32).
3. Фильтрует по `session_id` (allowed) и `role`.
4. Embed запроса с `Instruct:` prefix.
5. Cosine similarity по `message_vec.embedding` (BLOB float32).
6. Возвращает top-N с JSON-метаданными.

Полная схема — в `docs/architecture.md`.

## Common tasks

### Добавить поле в индекс

1. ALTER TABLE в `_ensure_index()` блоке `semsearch_engine.py`.
2. Обновить `INSERT OR REPLACE` запрос.
3. Обновить `SELECT` в `search()`.
4. Обновить `CHANGELOG.md` (одной строкой).
5. Обновить **этот файл** (раздел "Gotchas" если есть нюансы).

### Сменить embedding-модель

1. Поправить `EMBED_REPO` в `semsearch_engine.py`.
2. Если новая `DIM` отличается — обновить константу.
3. Переиндексировать: `oc-semsearch purge && oc-semsearch build`.
4. Обновить README + CHANGELOG + **этот файл** (модель упоминается в gotchas).

### Добавить аргумент тула

1. Добавить Zod-поле в `args` `semsearch.ts`.
2. Добавить в `payload` JSON в `execute()`.
3. Прочитать и применить в `search()` `semsearch_engine.py`.
4. Обновить README (таблица параметров).

## Gotchas

| Проблема | Фикс |
|---|---|
| macOS PEP 668 блокирует `pip install` | Используется venv через `python3 -m venv`. `install.sh` делает это. |
| `transformers 5.x` ломает mlx-embeddings 0.1.0 | Пин `transformers>=5,<5.5` в `install.sh`. |
| `out.text_embeds.tolist()` залипает 5-15 сек | `mlx-core` ленивый. Всегда `mx.eval(te)` перед tolist. |
| `<system-reminder>` блоки в индексируемом тексте | regex `_SYSTEM_BLOCK_RE.sub("", text)` перед embed. Замедляют токенизацию в 100×. |
| `reasoning`-части по 50-80K символов | Фильтр `json_extract(...,'$.type') = 'text'` — reasoning пропускаем. |
| `Bun.$` не работает в Electron-рендерере (TUI) | Используем `node:child_process/execFile`. |
| Тул умирал по таймауту 180s на большом бэклоге догонки | Таймаут 20 мин (`SEMSEARCH_TIMEOUT_MS`). Лимит догонки `SEMSEARCH_LAZY_MAX` по умолчанию 0 = без лимита (догоняем всё, иначе поиск не видит свежие сообщения; skipped в лимит не считаются). `--build` крутит цикл до `index_pending == 0` как страховка. |
| «Engine failed» без причины в TUI | Движок пишет JSON-ошибку в **stdout**, traceback — в **stderr**. Обёртка показывает оба (stdout первые 400, stderr хвост 400). |
| `database is locked` при параллельных сессиях | `index.db` в WAL + `busy_timeout=30000` (ставится в `_ensure_index()`). |
| Поиск находил удалённые в OpenCode сессии | `lazy_index()` начинается с prune: `DELETE FROM message_vec WHERE NOT EXISTS (SELECT 1 FROM src.message ...)` — opencode.db аттачится к idx read-only. Счётчик — `pruned_orphans` в timings. |
| Google Drive медленно синхронизирует `.git/` (много мелких файлов) | Репо работает; синк просто медленнее. Если мешает — добавить `.git` в Drive exclude. |

## Тестирование

| Что | Команда | Ожидание |
|---|---|---|
| Self-check | `oc-semsearch doctor` | `doctor: OK` |
| Build | `oc-semsearch build` (или `~/.config/opencode/tools/semsearch_index.py --build`) | < 5 мин для ~500 сообщений, exit 0 |
| Search | `~/.config/opencode/tools/semsearch_engine.py '{"query":"three.js","top":3}'` | JSON с top-3, score ≥ 0.5 для релевантных |
| End-to-end | В OpenCode: «найди сессию про X» | LLM вызывает `semsearch`, score ≥ 0.5 на правильных |

## Распространение

```bash
git clone https://github.com/vokasug/opencode-semsearch.git
cd opencode-semsearch
./install.sh
# перезапустить OpenCode.app
```

`install.sh` идемпотентен: при коллизиях файлов — пропускает (с warning), не затирает чужие тулы.

## См. также

- `README.md` — пользовательская инструкция
- `docs/architecture.md` — детали реализации
- `docs/examples.md` — типичные сценарии и фразы для LLM
- `CHANGELOG.md` — что менялось в каждой версии