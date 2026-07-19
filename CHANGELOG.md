# Changelog

## v0.1.2 (2026-07-20)

- `SEMSEARCH_LAZY_MAX` по умолчанию 0 = без лимита: догонка индекса всегда полная, поиск видит всю историю. Таймаут тула 20 мин покрывает любой реалистичный бэклог (~300 сообщений ≈ 2 мин)

## v0.1.1 (2026-07-20)

Фикс «Engine failed» в TUI (вызов умирал по таймауту 180s без понятной причины).

- `semsearch.ts`: таймаут тула 180s → 20 минут (env `SEMSEARCH_TIMEOUT_MS`); в ошибке теперь показываются JSON из stdout движка (там `detail`), хвост stderr и явный маркер таймаута
- `semsearch_engine.py`: `sqlite3.connect(IDX, timeout=30)` + `PRAGMA journal_mode=WAL` + `busy_timeout=30000` — переживает конкурентные сессии
- `semsearch_engine.py`: лимит догонки `SEMSEARCH_LAZY_MAX` (default 300 эмбеддингов на вызов, skipped не считаются) + прогресс в stderr + `index_pending` в timings
- `semsearch_index.py`: `--build` крутит engine в цикле до `index_pending == 0`

## v0.1.0 (2026-07-07)

Initial release.

- Custom OpenCode tool `semsearch` для семантического поиска по сессиям
- Бэкенды: `mlx-community/Qwen3-Embedding-0.6B-mxfp8` (MLX, macOS arm64) и `Qwen/Qwen3-Embedding-0.6B` (sentence-transformers, fallback)
- Sidecar-индекс в `~/.local/share/opencode/semsearch_index.db` (read-only opencode.db)
- Ленивая догонка индекса при каждом вызове
- CLI: `oc-semsearch query/build/stats/doctor/purge/reindex/upgrade/uninstall`
- Apache-2.0 license
