# Architecture

## Слои

```
┌─────────────────────────────────────────────┐
│  OpenCode.app (Electron + Bun runtime)      │
│                                             │
│  Custom tool: lib/semsearch.ts              │
│    • описано через @opencode-ai/plugin      │
│    • execute() вызывает python через        │
│      child_process.execFile                 │
└────────────────┬────────────────────────────┘
                 │  execFile
                 ▼
┌─────────────────────────────────────────────┐
│  venv: ~/.local/share/venvs/semsearch/      │
│                                             │
│  Python: lib/semsearch_engine.py            │
│    1. lazy_index()  — догоняет новые msg    │
│    2. embed(query, is_query=True)           │
│    3. KNN по message_vec (cosine sim)       │
│    4. возврат top-N результатов             │
└────────────────┬────────────────────────────┘
                 │
       ┌─────────┴──────────┐
       ▼                    ▼
┌──────────────┐    ┌────────────────────┐
│ opencode.db  │    │ semsearch_index.db │
│  (read-only) │    │  (sidecar)         │
│              │    │                    │
│ session      │    │ message_vec        │
│ message      │    │  message_id PK     │
│ part         │    │  embedding BLOB    │
│ todo, ...    │    │  session_id, role  │
└──────────────┘    │  excerpt, time     │
                    └────────────────────┘
```

## Хранилища

### `~/.local/share/opencode/opencode.db` (read-only в туле)

Открывается через URI `file:...?mode=ro` — это гарантирует, что мы не конфликтуем с OpenCode.app, который держит БД открытой в WAL-режиме.

Читаем таблицы:
- `session` — метаданные (id, title, time_*, time_archived)
- `message` — метаданные сообщений (id, session_id, time_*, role в JSON)
- `part` — собственно текст (message_id, type='text'|'reasoning'|'compaction', text в JSON)

### `~/.local/share/opencode/semsearch_index.db`

Sidecar-таблица:

```sql
CREATE TABLE message_vec(
  message_id     TEXT PRIMARY KEY,
  session_id     TEXT NOT NULL,
  session_title  TEXT,
  role           TEXT,          -- 'user' | 'assistant'
  time_created   INTEGER NOT NULL,
  excerpt        TEXT,          -- первые 300 символов превью
  dim            INTEGER NOT NULL,
  embedding      BLOB NOT NULL  -- 1024 × float32 = 4096 bytes
);
CREATE INDEX message_vec_session_idx ON message_vec(session_id);
CREATE INDEX message_vec_role_idx    ON message_vec(role);
CREATE INDEX message_vec_time_idx    ON message_vec(time_created);
CREATE INDEX message_vec_session_role_idx ON message_vec(session_id, role);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
```

`meta` хранит: `dim`, `backend`, `last_catchup`, `model_version` — для миграций.

## Модель эмбеддингов

`mlx-community/Qwen3-Embedding-0.6B-mxfp8` на Apple Silicon.

- 0.6B параметров, BF16→mxfp8 квантизация
- 1024-dim выход
- Context length 32K, мы используем max_length=128 (компромисс скорость/качество)
- Поддерживает instruction-aware prompting для queries (используем в `embed(is_query=True)`)
- Лицензия: Apache-2.0

Загрузка через `mlx_embeddings.load()` — модель скачивается при первом запуске (~900 MB), кладётся в `~/Library/Caches/huggingface/`.

### Почему mxfp8 на Apple Silicon

- bf16 версия (`mlx-community/Qwen3-Embedding-0.6B-bf16`) **не существует** в HF
- mxfp8 — 8-битная квантизация с micro-scaling, доступная на Apple Silicon через MLX
- На macOS arm64 даёт ~1.5× меньше RAM, чем bf16, при сравнимом качестве

### Почему max_length=128

Эмпирически (см. профилирование в `docs/dev-notes.md`):
- max_length=512 (default): 17s на 32-batch
- max_length=256: 13s
- max_length=128: 7.5s
- max_length=64: 3.5s

128 — sweet spot: 95% коротких user-prompts и assistant-text-ответов укладываются, embedding всё равно L2-нормируется (cosine focus на семантику, не на детали).

## Поток при вызове тула

```
LLM вызывает semsearch({"query": "...", "top": 10})
   │
   ▼
OpenCode runtime → execute(args, ctx)
   │
   ▼
semsearch.ts: pExecFile(VENV_PY, [ENGINE_SCRIPT, JSON_payload])
   │
   ▼
semsearch_engine.py:
   1. open opencode.db (read-only URI)
   2. open semsearch_index.db
   3. lazy_index():
      - ATTACH index.db as 'idx'
      - SELECT user+assistant messages, NOT EXISTS in idx
      - для каждого: concat text parts, strip <system-reminder>
      - embed texts батчами (32)
      - INSERT OR REPLACE
      - DETACH idx
   4. embed query с инструкцией
   5. SELECT from message_vec WHERE session_id IN allowed AND role=?
   6. cosine sim, sort desc, top N
   7. return JSON {results, timings_ms, ...}
   │
   ▼
stdout → LLM
```

## Hot-path

Первый вызов в сессии OpenCode:
- 5-10 сек cold model load
- 0-2 сек догонка индекса (если есть новые сессии)
- 200-500 мс embed + KNN

Последующие:
- 0 мс (модель в кэше Metal)
- 0 мс (нет новых сообщений)
- 200-500 мс embed + KNN

## Ключевые оптимизации (выловленные при профилировании)

| Проблема | Фикс |
|---|---|
| MLX выдаёт ленивый view → `.tolist()` залипает на 5-15 сек | `mx.eval(te)` перед tolist |
| `Bun.$` не работает в Electron-рендерере (TUI) | переход на `node:child_process/execFile` |
| `Bun.$` шаблон с `${JSON.stringify(...)}` теряет кавычки | обёртка через `bash -lc` ИЛИ `execFile` (последний — лучше) |
| `<system-reminder>` блоки замедляют токенизацию в 100× | regex-strip перед embed |
| reasoning-parts могут быть 50-80K символов | фильтр `type='text'` (reasoning пропускаем) |
| Первая партия `mx.eval` после `mlx_embeddings.load` дорогая | warmup-вызов в lazy_index перед основным циклом |

## Известные ограничения

- **Размер sidecar**: ~2 MB на 500 сообщений
- **Модель**: ~900 MB на диске (один раз скачивается)
- **MLX**: только macOS arm64. На Linux/Windows — fallback на sentence-transformers (медленнее на CPU)
- **Concurrency**: один writer в semsearch_index.db (если тул вызывается параллельно, SQLite сериализует транзакции)
- **Не индексируются**: `part.type='tool'`, `part.type='step-start'`, `part.type='step-finish'`, `part.type='file'`, `part.type='compaction'`
