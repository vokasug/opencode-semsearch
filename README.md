# opencode-semsearch

Семантический поиск по истории сессий [OpenCode](https://opencode.ai). Локально, на устройстве. Поиск по **смыслу**, не по точному слову.

Под капотом — модель эмбеддингов [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) (Apache-2.0), запускаемая через [mlx-embeddings](https://github.com/Blaizzy/mlx-embeddings) на Apple Silicon (MLX) или [sentence-transformers](https://www.sbert.net/) на других платформах.

## Что делает

Регистрирует в OpenCode тул `semsearch`. LLM может его вызывать, чтобы найти прошлые разговоры:

> «Найди мне сессию про three.js solar system»  
> «Где я спрашивал про настройку провайдера alibaba?»  
> «Покажи прошлый раз, когда я возился с Flow Moscow»

На выходе — список наиболее релевантных **сообщений** с атрибуцией к сессии (id, title, role: user/assistant, превью). LLM сам группирует/дедуплицирует результаты в финальном ответе.

## Архитектура

```
OpenCode.app
   ↓ tool call
semsearch.ts                ← OpenCode custom tool (Node)
   ↓ execFile
~/.local/share/venvs/semsearch/bin/python
   ↓
semsearch_engine.py         ← MLX / sentence-transformers + KNN
   ↓
~/.local/share/opencode/semsearch_index.db   ← sidecar (SQLite, BLOB-вектора)
```

* Исходный `opencode.db` открывается **read-only** — не блокируется работающим OpenCode.app
* Индекс — отдельный sidecar-файл, не трогает основную БД
* Эмбеддинги 1024-dim (Qwen3-Embedding-0.6B), квантизация mxfp8 на macOS

## Установка

```bash
git clone https://github.com/vokasug/opencode-semsearch.git
cd opencode-semsearch
./install.sh
```

`install.sh` автоматически:
1. Определяет платформу: `Darwin/arm64` → MLX, остальное → sentence-transformers
2. Создаёт venv в `~/.local/share/venvs/semsearch` и ставит зависимости
3. Копирует `lib/semsearch.{ts,engine.py,index.py}` в `~/.config/opencode/tools/`
4. Строит начальный индекс

После — **перезапусти OpenCode.app**. Тул `semsearch` появится автоматически.

### Опции установки

```bash
SKIP_INDEX=1 ./install.sh                      # без первичной индексации
INSTALL_DIR=~/.config/opencode/tools ./install.sh
OPENCODE_DB=/path/to/opencode.db ./install.sh
DRY_RUN=1 ./install.sh                         # только проверка
```

## Использование

### В OpenCode

Просто спроси LLM естественным языком:

> «Найди сессию где я возился с C++ игрой про скейтбординг»

LLM сам вызовет `semsearch({"query": "...", "top": 10})` и вернёт результат.

### Из терминала

После установки доступна CLI-обёртка:

```bash
oc-semsearch query "three.js solar system"
oc-semsearch query "alibaba провайдер" --role user --since 7
oc-semsearch build                    # перестроить индекс
oc-semsearch stats                    # сколько проиндексировано
oc-semsearch doctor                   # self-check
oc-semsearch upgrade                  # git pull + пересборка
oc-semsearch uninstall                # полный откат
```

## Параметры тула

| Аргумент | Тип | Default | Описание |
|---|---|---|---|
| `query` | string | (обяз.) | Поисковый запрос на естественном языке (RU/EN) |
| `top` | number | 10 | Количество результатов (max 50) |
| `role` | enum | `"any"` | Фильтр по автору: `user` / `assistant` / `any` |
| `since_days` | number | — | Искать только в сессиях последних N дней |
| `only_active` | bool | `true` | Скрывать архивные сессии |

## Ленивая догонка

При каждом вызове тула автоматически проверяется, есть ли новые сообщения, не попавшие в индекс. Если есть — догоняются **все** до выполнения поиска, поэтому поиск всегда видит полную историю. Обычно это +0 мс; первичный build (~530 сообщений) — ~2 минуты на Apple Silicon.

Также при каждом вызове из индекса вычищаются вектора сообщений и сессий, **удалённых в OpenCode** — поиск никогда не вернёт то, чего уже нет в базе.

## Переменные окружения

| Переменная | Default | Описание |
|---|---|---|
| `SEMSEARCH_TIMEOUT_MS` | 1200000 | Таймаут вызова движка из тула (20 мин) |
| `SEMSEARCH_LAZY_MAX` | 0 | Лимит эмбеддингов догонки за один вызов; 0 = без лимита (догонять всё) |
| `SEMSEARCH_BATCH` | 32 | Размер батча эмбеддингов |
| `SEMSEARCH_PYTHON` | `~/.local/share/venvs/semsearch/bin/python` | Путь к Python с зависимостями |

## Требования

- macOS arm64 (Apple Silicon) — для MLX-бэкенда
- Или любая ОС с Python 3.10+ и PyTorch (fallback через sentence-transformers)
- ~1 GB диска на модель, ~5 MB на sidecar-индекс
- OpenCode ≥ 1.17

## Производительность

| Этап | Время |
|---|---|
| Холодный старт (загрузка модели) | ~5-10 сек |
| Embedding одного запроса | ~150 ms |
| KNN по 500 сообщениям | ~80 ms |
| Догонка 1 нового сообщения | ~300 ms |
| Полный build (~530 сообщений) | ~2 мин |

## Известные ограничения

- Первые 600 символов каждого сообщения + `max_length=128` токенов — компромисс между качеством и скоростью на mxfp8
- Reasoning-части (chain-of-thought) пропускаются — они могут быть огромными и замедлять эмбеддер
- `<system-reminder>` блоки автоматически удаляются из индексируемого текста
- На mxfp8-квантизации mlx-embeddings качество ≈ bf16-версии при меньшем размере

## Лицензия

Apache-2.0 (как у Qwen3-Embedding).

## См. также

- [docs/examples.md](docs/examples.md) — примеры вызовов и типичные сценарии
- [docs/architecture.md](docs/architecture.md) — детали реализации
