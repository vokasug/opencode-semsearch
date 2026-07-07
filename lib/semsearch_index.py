#!/usr/bin/env python3
"""
CLI для управления индексом семантического поиска.

Usage:
  semsearch_index.py --build                         full reindex
  semsearch_index.py --reindex ses_X1 ses_X2 ...     reindex specific sessions
  semsearch_index.py --purge                         remove sidecar DB
  semsearch_index.py --stats                         show index stats
  semsearch_index.py --doctor                        self-check всего стека
  semsearch_index.py --list-uncached                 list sessions not yet indexed
"""
import os, sys, sqlite3, struct, json, time, os.path as p, traceback

DB_SRC = p.expanduser("~/.local/share/opencode/opencode.db")
IDX    = p.expanduser("~/.local/share/opencode/semsearch_index.db")
DIM    = 1024

ENGINE = p.expanduser("~/.config/opencode/tools/semsearch_engine.py")
VENV_PYTHON = p.expanduser("~/.local/share/venvs/semsearch/bin/python")

def _stderr(s): sys.stderr.write(s + "\n")
def _venv_python() -> str:
    if p.exists(VENV_PYTHON):
        return VENV_PYTHON
    return sys.executable
def _ok(s): print(f"  \033[32m✓\033[0m {s}")
def _warn(s): print(f"  \033[33m!\033[0m {s}")
def _err(s):  print(f"  \033[31m✗\033[0m {s}")

def cmd_stats():
    if not p.exists(IDX):
        print("no index: run --build first")
        return 1
    con = sqlite3.connect(IDX)
    n_msg, n_sess, by_role = con.execute("""
      SELECT COUNT(*), COUNT(DISTINCT session_id), role
      FROM message_vec
      GROUP BY role
    """).fetchall(), 0, {}
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM message_vec").fetchone()[0]
    sessions = cur.execute("SELECT COUNT(DISTINCT session_id) FROM message_vec").fetchone()[0]
    rows = cur.execute("SELECT role, COUNT(*) FROM message_vec GROUP BY role").fetchall()
    print(f"messages: {total}")
    print(f"sessions: {sessions}")
    for r, c in rows:
        print(f"  role={r}: {c}")
    meta = dict(cur.execute("SELECT key,value FROM meta").fetchall())
    if meta:
        print("meta:")
        for k in ("dim","backend","last_catchup","build_started","build_finished"):
            if k in meta:
                print(f"  {k}: {meta[k]}")
    return 0

def cmd_purge():
    if p.exists(IDX):
        p_real = p.realpath(IDX)
        os.remove(IDX)
        print(f"removed: {p_real}")
        return 0
    print("nothing to purge")
    return 0

def _run_engine_catchup():
    """Запустить engine.py — он сам сделает lazy_index."""
    if not p.exists(ENGINE):
        _err(f"engine not found: {ENGINE}")
        return 1
    n = len(sys.argv) - 1
    argv = list(sys.argv[1:])
    if len(argv) < 1:
        return 1
    payload = json.dumps({
        "query":"", "top":1, "role":"any",
        "since_days":None, "only_active":False,
        "_internal_catchup_only": True,
    })
    # the engine's lazy_index runs on every call regardless of query
    proc_env = os.environ.copy()
    proc_env["PYTHONPATH"] = p.dirname(ENGINE)
    import subprocess
    res = subprocess.run(
        [_venv_python(), ENGINE, payload],
        capture_output=True, text=True, env=proc_env,
    )
    if res.returncode != 0:
        _err(f"engine failed: {res.stderr.strip()[:300]}")
        return 1
    print(res.stdout.strip())
    return 0

def cmd_build():
    if not p.exists(DB_SRC):
        _err(f"opencode db not found: {DB_SRC}")
        return 1
    print(f"building index from {DB_SRC}...")
    t0 = time.time()
    rc = _run_engine_catchup()
    if rc != 0: return rc
    elapsed = time.time()-t0
    print(f"done in {elapsed:.1f}s")
    return 0

def cmd_reindex(sids):
    """Удалить из индекса часть конкретных сессий и догнать через engine."""
    if not sids:
        _warn("usage: --reindex ses_X1 ses_X2 ...")
        return 1
    if not p.exists(IDX):
        print("no index yet: run --build first")
        return 1
    con = sqlite3.connect(IDX)
    n = con.execute(
        f"DELETE FROM message_vec WHERE session_id IN ({','.join('?'*len(sids))})",
        sids).rowcount
    con.commit()
    print(f"removed {n} indexed messages from {len(sids)} session(s); "
          f"now re-running lazy index...")
    return cmd_build()

def cmd_list_uncached():
    """Список сообщений, которые ещё не проиндексированы."""
    if not p.exists(DB_SRC):
        _err(f"opencode db not found: {DB_SRC}")
        return 1
    src = sqlite3.connect(f"file:{DB_SRC}?mode=ro", uri=True)
    if p.exists(IDX):
        idx = sqlite3.connect(IDX)
        n_already = idx.execute("SELECT COUNT(*) FROM message_vec").fetchone()[0]
    else:
        n_already = 0
    n_todo = src.execute("""
      SELECT COUNT(*) FROM message m JOIN session s ON s.id=m.session_id
      WHERE json_extract(m.data,'$.role') IN ('user','assistant')
    """).fetchone()[0]
    print(f"indexed: {n_already}")
    print(f"indexable (user+assistant messages): {n_todo}")
    print(f"to-embed: {max(n_todo-n_already, 0)}")
    return 0

def cmd_doctor():
    print("== semsearch doctor ==")
    rc = 0
    # 1) tool files
    tools = p.expanduser("~/.config/opencode/tools")
    for fn in ("semsearch.ts", "semsearch_engine.py", "semsearch_index.py"):
        full = p.join(tools, fn)
        if p.exists(full):
            _ok(f"tool file present: {full}")
        else:
            _err(f"missing: {full}"); rc = 1
    # 2) python deps — проверяем в venv
    if p.exists(VENV_PYTHON):
        import subprocess
        r = subprocess.run(
            [_venv_python(), "-c",
             "import mlx_embeddings; import mlx.core; print('ok')"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            _ok("mlx-embeddings installed (in venv)")
        else:
            _warn(f"venv has no mlx-embeddings: {r.stderr.strip()[:200]}")
    else:
        _warn(f"venv not found at {VENV_PYTHON}; install: "
              f"python3 -m venv {VENV_PYTHON} && "
              f"{VENV_PYTHON}/bin/pip install mlx-embeddings")
    # 3) db sources
    if p.exists(DB_SRC):
        try:
            con = sqlite3.connect(f"file:{DB_SRC}?mode=ro", uri=True)
            n = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
            _ok(f"opencode.db readable ({n} messages)")
            con.close()
        except Exception as e:
            _err(f"opencode.db: {e}"); rc = 1
    else:
        _err(f"opencode.db not found: {DB_SRC}"); rc = 1
    # 4) sidecar index
    if p.exists(IDX):
        try:
            con = sqlite3.connect(IDX)
            n = con.execute("SELECT COUNT(*) FROM message_vec").fetchone()[0]
            _ok(f"index.db present ({n} indexed messages)")
            con.close()
        except Exception as e:
            _err(f"index.db unreadable: {e}"); rc = 1
    else:
        _warn(f"index.db not built yet — run --build")
    # 5) smoke-test engine
    print("\n-- smoke test: short query --")
    try:
        out = _run_engine_catchup_once("hello world")
        d = json.loads(out)
        _ok(f"engine returned valid JSON (model={d.get('model')}, "
            f"results={len(d.get('results', []))})")
    except Exception as e:
        _err(f"engine smoke-test failed: {e}"); rc = 1
    print(f"\ndoctor: {'OK' if rc==0 else 'FAILED'}")
    return rc

def _run_engine_catchup_once(query=""):
    payload = json.dumps({
        "query": query, "top": 3, "role": "any",
        "since_days": None, "only_active": True,
    })
    proc_env = os.environ.copy()
    import subprocess
    res = subprocess.run(
        [_venv_python(), ENGINE, payload],
        capture_output=True, text=True, env=proc_env, timeout=60,
    )
    if res.returncode != 0:
        raise RuntimeError(f"engine exit {res.returncode}: {res.stderr.strip()[:300]}")
    return res.stdout.strip()

def main():
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    if "--stats" in args:        return cmd_stats()
    if "--purge" in args:        return cmd_purge()
    if "--build" in args:        return cmd_build()
    if "--doctor" in args:       return cmd_doctor()
    if "--list-uncached" in args:return cmd_list_uncached()
    if "--reindex" in args:
        sids = [a for a in args[1:] if a.startswith("ses_")]
        return cmd_reindex(sids)
    print(__doc__)
    return 1

if __name__ == "__main__":
    sys.exit(main())
