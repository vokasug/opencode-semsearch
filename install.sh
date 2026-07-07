#!/usr/bin/env bash
# install.sh — установка opencode-semsearch
#
# Что делает:
#   1. Определяет платформу (Darwin/arm64 → MLX, иначе → sentence-transformers)
#   2. Создаёт venv и ставит python-зависимости
#   3. Копирует lib/*.ts,*.py в ~/.config/opencode/tools/
#   4. Строит начальный индекс (если не SKIP_INDEX=1)
#
# Переменные окружения:
#   INSTALL_DIR     куда копировать тулы (default: ~/.config/opencode/tools)
#   OPENCODE_DB     путь к opencode.db (default: ~/.local/share/opencode/opencode.db)
#   VENV_DIR        путь к venv (default: ~/.local/share/venvs/semsearch)
#   SKIP_INDEX=1    не строить начальный индекс
#   DRY_RUN=1       только показать, что будет сделано
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.config/opencode/tools}"
OPENCODE_DB="${OPENCODE_DB:-$HOME/.local/share/opencode/opencode.db}"
VENV_DIR="${VENV_DIR:-$HOME/.local/share/venvs/semsearch}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_INDEX="${SKIP_INDEX:-0}"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
err()  { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; }
section() { printf "\n\033[1m== %s ==\033[0m\n" "$*"; }

run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf "    [DRY] %s\n" "$*"
    else
        "$@"
    fi
}

section "platform"
UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"
ok "$UNAME_S / $UNAME_M"

case "$UNAME_S/$UNAME_M" in
    Darwin/arm64) BACKEND=mlx ;;
    *)            BACKEND=sentence-transformers ;;
esac
ok "backend: $BACKEND"

# --- 1. venv + deps ---
section "python venv ($VENV_DIR)"
if [ ! -d "$VENV_DIR" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        err "python3 not found in PATH"
        exit 1
    fi
    run python3 -m venv "$VENV_DIR"
    ok "venv created"
else
    ok "venv exists"
fi
VENV_PY="$VENV_DIR/bin/python"
[ -x "$VENV_PY" ] || { err "venv python not executable: $VENV_PY"; exit 1; }

# install deps
case "$BACKEND" in
    mlx)
        run "$VENV_PY" -m pip install --quiet --upgrade pip
        # transformers 5.x ломает mlx-embeddings 0.1.0 — пиним <5.5
        run "$VENV_PY" -m pip install --quiet "transformers>=5,<5.5" mlx-embeddings
        ;;
    sentence-transformers)
        run "$VENV_PY" -m pip install --quiet --upgrade pip
        run "$VENV_PY" -m pip install --quiet "transformers>=4.51" \
            sentence-transformers torch
        ;;
esac
ok "deps installed"

# --- 2. copy tool files ---
section "copy tools → $INSTALL_DIR"
run mkdir -p "$INSTALL_DIR"
for f in semsearch.ts semsearch_engine.py semsearch_index.py; do
    src="$REPO_ROOT/lib/$f"
    if [ ! -f "$src" ]; then err "missing source: $src"; exit 1; fi
    if [ -f "$INSTALL_DIR/$f" ] && \
       ! cmp -s "$src" "$INSTALL_DIR/$f"; then
        warn "$INSTALL_DIR/$f уже существует и отличается — пропускаю (используй --force для перезаписи)"
    else
        run cp "$src" "$INSTALL_DIR/$f"
    fi
done
run chmod +x "$INSTALL_DIR/semsearch_engine.py" "$INSTALL_DIR/semsearch_index.py"
ok "tools copied"

# --- 3. CLI wrapper ---
section "oc-semsearch CLI"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
run mkdir -p "$BIN_DIR"
run cp "$REPO_ROOT/bin/oc-semsearch" "$BIN_DIR/oc-semsearch"
run chmod +x "$BIN_DIR/oc-semsearch"
ok "oc-semsearch installed → $BIN_DIR/oc-semsearch"

# --- 4. build initial index ---
section "index"
if [ ! -f "$OPENCODE_DB" ]; then
    warn "opencode db not found: $OPENCODE_DB — пропускаю build (создай позже через 'oc-semsearch build')"
elif [ "$SKIP_INDEX" = "1" ]; then
    ok "SKIP_INDEX=1 — не строю"
else
    run "$VENV_PY" "$INSTALL_DIR/semsearch_index.py" --build
    ok "index built"
fi

section "done"
cat <<EOF

✅ Установлено.
   Тул:       $INSTALL_DIR/semsearch.{ts,*}
   venv:      $VENV_DIR
   Модель:    $BACKEND
   Sidecar:   $HOME/.local/share/opencode/semsearch_index.db

Следующий шаг: перезапусти OpenCode.app (⌘Q → open -a OpenCode).

Проверка:
  $ oc-semsearch doctor
  $ oc-semsearch query "three.js solar system"

EOF
