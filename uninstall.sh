#!/usr/bin/env bash
# uninstall.sh — полный откат установки opencode-semsearch
#
# Удаляет (только если файлы совпадают с эталонными из репо):
#   - $INSTALL_DIR/semsearch.{ts,engine.py,index.py}
#   - $BIN_DIR/oc-semsearch
#   - $VENV_DIR
#   - $HOME/.local/share/opencode/semsearch_index.db  (если --purge-index)
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.config/opencode/tools}"
VENV_DIR="${VENV_DIR:-$HOME/.local/share/venvs/semsearch}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
INDEX="${INDEX:-$HOME/.local/share/opencode/semsearch_index.db}"
PURGE_INDEX="${PURGE_INDEX:-0}"
FORCE="${FORCE:-0}"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }

# удаляем файл, только если он совпадает с эталонным ИЛИ FORCE=1
safe_rm() {
    local f="$1"
    if [ ! -e "$f" ]; then return 0; fi
    if [ "$FORCE" = "1" ]; then rm -f "$f"; ok "removed (forced): $f"; return 0; fi
    local ref="$2"
    if [ -f "$ref" ] && cmp -s "$f" "$ref"; then
        rm -f "$f"
        ok "removed: $f"
    else
        warn "skip (file differs from repo or no reference): $f"
    fi
}

safe_rm "$INSTALL_DIR/semsearch.ts"          "$REPO_ROOT/lib/semsearch.ts"
safe_rm "$INSTALL_DIR/semsearch_engine.py"   "$REPO_ROOT/lib/semsearch_engine.py"
safe_rm "$INSTALL_DIR/semsearch_index.py"    "$REPO_ROOT/lib/semsearch_index.py"
safe_rm "$BIN_DIR/oc-semsearch"              "$REPO_ROOT/bin/oc-semsearch"

if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    ok "removed venv: $VENV_DIR"
fi

if [ "$PURGE_INDEX" = "1" ] && [ -f "$INDEX" ]; then
    rm -f "$INDEX"
    ok "purged index: $INDEX"
fi

cat <<EOF

✅ Uninstall complete.

Что осталось:
- кэш модели в ~/Library/Caches/huggingface/ — удалить вручную:
    rm -rf ~/Library/Caches/huggingface/hub/models--mlx-community--Qwen3-Embedding-0.6B-mxfp8
- index (если не указан --purge-index):
    $INDEX
EOF
