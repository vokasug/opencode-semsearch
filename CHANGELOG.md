# Changelog

## v0.1.0 (2026-07-07)

Initial release.

- Custom OpenCode tool `semsearch` для семантического поиска по сессиям
- Бэкенды: `mlx-community/Qwen3-Embedding-0.6B-mxfp8` (MLX, macOS arm64) и `Qwen/Qwen3-Embedding-0.6B` (sentence-transformers, fallback)
- Sidecar-индекс в `~/.local/share/opencode/semsearch_index.db` (read-only opencode.db)
- Ленивая догонка индекса при каждом вызове
- CLI: `oc-semsearch query/build/stats/doctor/purge/reindex/upgrade/uninstall`
- Apache-2.0 license
