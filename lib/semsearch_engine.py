#!/usr/bin/env python3
"""
Семантический поиск по сообщениям OpenCode (in-proc).

Backends:
  - mlx-community/Qwen3-Embedding-0.6B-mxfp8   (macOS arm64)
  - Qwen/Qwen3-Embedding-0.6B (sentence-transformers, fallback)

Usage:
    semsearch_engine.py '<JSON>'
JSON schema:
    {
      "query": str,
      "top": int,                  # default 10, max 50
      "role": "user"|"assistant"|"any",
      "since_days": int | null,
      "only_active": bool
    }
"""
import os, sys, json, sqlite3, struct, math, os.path as p, time, traceback

DB_SRC = p.expanduser("~/.local/share/opencode/opencode.db")
IDX    = p.expanduser("~/.local/share/opencode/semsearch_index.db")
DIM    = 1024
QUERY_INSTRUCT = (
    "Given a query about a user session in OpenCode, retrieve the most "
    "relevant message that matches the query's intent."
)
LAZY_BATCH = int(os.environ.get("SEMSEARCH_BATCH", "32"))
# лимит эмбеддингов догонки за один вызов; 0 = без лимита (полная
# догонка каждый раз — таймаут тула 20 мин покрывает любой реалистичный
# бэклог). Env-override оставлен для экзотических случаев
LAZY_MAX_PER_RUN = int(os.environ.get("SEMSEARCH_LAZY_MAX", "0"))

# ============ EMBED BACKENDS ============
class Embedder:
    def encode(self, texts, *, is_query=False):
        raise NotImplementedError
    @property
    def name(self):
        return type(self).__name__

class MLXEmbedder(Embedder):
    def __init__(self):
        from mlx_embeddings import load  # noqa
        self.model, self.tok = load("mlx-community/Qwen3-Embedding-0.6B-mxfp8")
    def encode(self, texts, *, is_query=False):
        from mlx_embeddings import generate
        if is_query:
            texts = [f"Instruct: {QUERY_INSTRUCT}\nQuery:{t}" for t in texts]
        # max_length=128 эмпирически достаточно для коротких
        # user-prompt / assistant-text (≈200-300 токенов после токенизации);
        # дефолт 512 даёт ~7s на 32-batch на mxfp8, 128 — ~1.5-2s.
        out = generate(self.model, self.tok, texts=texts, max_length=128)
        te = out.text_embeds
        # NB: mlx-core ленив. Без mx.eval() последующий .tolist() иногда
        # залипает на 5-15 секунд (Metal-шедулер). С eval() — 1-5 ms.
        try:
            import mlx.core as mx
            mx.eval(te)
        except Exception:
            pass
        return te.tolist()

class SentenceTransformerEmbedder(Embedder):
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
    def encode(self, texts, *, is_query=False):
        kwargs = {"prompt_name": "query"} if is_query else {}
        return [v.tolist() for v in self.m.encode(texts, **kwargs)]

_BACKEND = None
def _embedder() -> Embedder:
    global _BACKEND
    if _BACKEND is None:
        try:
            _BACKEND = MLXEmbedder()
            sys.stderr.write(f"[semsearch] loaded backend: MLX\n")
        except Exception as e:
            sys.stderr.write(f"[semsearch] mlx unavailable ({type(e).__name__}: {e}); "
                             f"fallback to sentence-transformers\n")
            _BACKEND = SentenceTransformerEmbedder()
            sys.stderr.write(f"[semsearch] loaded backend: sentence-transformers\n")
    return _BACKEND

def embed(texts, *, is_query=False):
    vecs = _embedder().encode(texts, is_query=is_query)
    return [v[:DIM] for v in vecs]

# ============ VECTOR UTILS ============
def pack(v):  return struct.pack(f"{len(v)}f", *v)
def unpack(b): return list(struct.unpack(f"{len(b)//4}f", b))
def cosine(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    return dot/(na*nb) if na and nb else 0.0

# ============ DB ============
def _ensure_index():
    new = not p.exists(IDX)
    # busy_timeout=30s: переживаем конкурентные вызовы semsearch из
    # параллельных сессий OpenCode вместо мгновенного "database is locked"
    con = sqlite3.connect(IDX, timeout=30)
    # WAL — читатели не блокируют писателя, безопаснее при параллельных
    # процессах
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    if new:
        con.executescript("""
            CREATE TABLE message_vec(
              message_id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              session_title TEXT,
              role TEXT,
              time_created INTEGER NOT NULL,
              excerpt TEXT,
              dim INTEGER NOT NULL,
              embedding BLOB NOT NULL
            );
            CREATE INDEX message_vec_session_idx ON message_vec(session_id);
            CREATE INDEX message_vec_role_idx ON message_vec(role);
            CREATE INDEX message_vec_time_idx ON message_vec(time_created);
            CREATE INDEX message_vec_session_role_idx ON message_vec(session_id, role);
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        """)
        con.commit()
    return con

def _extract_text(data_str):
    """Try multiple ways to get text out of a message.data JSON string."""
    try:
        d = json.loads(data_str)
    except Exception:
        return None
    content = d.get("content")
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = []
        for p_item in content:
            if isinstance(p_item, dict):
                t = p_item.get("text") or p_item.get("content")
                if isinstance(t, str): parts.append(t)
        joined = "\n".join(parts).strip()
        if joined: return joined
    reasoning = d.get("reasoning")
    if isinstance(reasoning, str): return reasoning
    return None


import re as _re
_SYSTEM_BLOCK_RE = _re.compile(r"<system-reminder>.*?</system-reminder>", _re.DOTALL)
def _strip_system_blocks(text):
    """Удаляет <system-reminder>...</system-reminder> блоки и схлопывает пробелы."""
    if not text: return text
    cleaned = _SYSTEM_BLOCK_RE.sub("", text)
    return _re.sub(r"\n{3,}", "\n\n", cleaned).strip()

def lazy_index(idx_con, src_con, *, only_new=True):
    cur = idx_con.cursor()

    # prune: вычищаем вектора сообщений, которых больше нет в opencode.db
    # (удалённые сессии/сообщения), иначе поиск находил бы удалённое.
    # opencode.db приатачена read-only, пишем только в message_vec
    idx_con.execute(f"ATTACH DATABASE 'file:{DB_SRC}?mode=ro' AS src")
    try:
        cur.execute("""
          DELETE FROM message_vec
          WHERE NOT EXISTS (
            SELECT 1 FROM src.message m WHERE m.id = message_vec.message_id
          )
        """)
        pruned = cur.rowcount
        idx_con.commit()
    finally:
        idx_con.execute("DETACH DATABASE src")
    if pruned:
        sys.stderr.write(f"[semsearch] pruned {pruned} orphan vectors\n")

    # attach the index DB so the NOT EXISTS subquery can find `message_vec`
    src_con.execute("ATTACH DATABASE ? AS idx", (IDX,))
    try:
        # собираем все user/assistant сообщения, для которых ещё нет записи
        # в индексе. текст берём из part-таблицы (1 message → N parts),
        # склеиваем в порядке time_created.
        todo_rows = src_con.execute(f"""
          SELECT m.id, m.session_id, s.title, m.time_created,
                 json_extract(m.data,'$.role') AS role
          FROM main.message m JOIN main.session s ON s.id = m.session_id
          WHERE json_extract(m.data,'$.role') IN ('user','assistant')
            AND EXISTS (
              SELECT 1 FROM main.part p
              WHERE p.message_id = m.id
                AND json_extract(p.data,'$.type') IN ('text','reasoning')
                AND json_extract(p.data,'$.text') IS NOT NULL
                AND length(json_extract(p.data,'$.text')) >= 20
            )
            AND NOT EXISTS (
              SELECT 1 FROM idx.message_vec v WHERE v.message_id = m.id
            )
          ORDER BY m.time_created
        """).fetchall()
    finally:
        src_con.execute("DETACH DATABASE idx")
    if not todo_rows:
        return {"added": 0, "skipped": 0, "pending": 0, "pruned": pruned}

    # для каждого сообщения — подтянуть все его text-части (НЕ reasoning,
    # reasoning может быть огромным — 50-80K символов — и здорово тормозит
    # эмбеддер) и склеить.
    msg_ids = [r[0] for r in todo_rows]
    placeholders = ",".join("?"*len(msg_ids))
    part_rows = src_con.execute(f"""
      SELECT p.message_id, json_extract(p.data,'$.text') AS txt
      FROM part p
      WHERE p.message_id IN ({placeholders})
        AND json_extract(p.data,'$.type') = 'text'
        AND json_extract(p.data,'$.text') IS NOT NULL
        AND length(json_extract(p.data,'$.text')) >= 20
      ORDER BY p.message_id, p.time_created
    """, msg_ids).fetchall()

    parts_by_msg = {}
    for mid, txt in part_rows:
        parts_by_msg.setdefault(mid, []).append(txt)

    added, skipped = 0, 0
    processed = 0
    # cap по ЭМБЕДДИНГАМ за запуск, а не по scanned-строкам: skipped
    # сообщения (без text) не получают записи в индексе и иначе
    # навсегда блокировали бы голову очереди
    for i in range(0, len(todo_rows), LAZY_BATCH):
        if LAZY_MAX_PER_RUN and added >= LAZY_MAX_PER_RUN:
            break
        batch = todo_rows[i:i+LAZY_BATCH]
        processed = i + len(batch)
        texts, rows = [], []
        for mid, sid, ttl, tc, role in batch:
            chunks = parts_by_msg.get(mid, [])
            full = "\n".join(chunks).strip()
            if not full:
                skipped += 1
                continue
            # убираем <system-reminder> блоки — это OpenCode-инжекты,
            # которые сильно тормозят эмбеддер (токенизация спанов
            # с длинными angle-bracket выражениями) и не несут
            # смысла для семантического поиска
            cleaned = _strip_system_blocks(full)
            # жёсткий cap: 600 символов. user-prompt редко длиннее
            # 200, assistant text-ответы важны по началу. C 2000 chars
            # mlx-mxfp8 тратит 15-20s на 32-batch; с 600 chars + max_length=128
            # это ~2s. Качество поиска не страдает — мы ищем по смыслу,
            # а не по точному тексту.
            texts.append(cleaned[:600])
            rows.append((mid, sid, ttl, role, tc, cleaned[:300]))
        if not texts:
            continue
        try:
            vecs = embed(texts, is_query=False)
        except Exception as e:
            sys.stderr.write(f"[semsearch] embed batch failed: {e}\n")
            continue
        new_rows = []
        for (mid,sid,ttl,role,tc,ex), v in zip(rows, vecs):
            new_rows.append((mid, sid, ttl, role, tc, ex, DIM, pack(v[:DIM])))
        cur.executemany(
            "INSERT OR REPLACE INTO message_vec VALUES (?,?,?,?,?,?,?,?)",
            new_rows)
        idx_con.commit()
        added += len(new_rows)
        sys.stderr.write(
            f"[semsearch] lazy_index: {min(i+LAZY_BATCH, len(todo_rows))}"
            f"/{len(todo_rows)} messages processed\n")
    cur.execute("INSERT OR REPLACE INTO meta VALUES "
                "('last_catchup', strftime('%s','now'))")
    cur.execute("INSERT OR REPLACE INTO meta VALUES ('backend', ?)",
                (type(_embedder()).__name__,))
    cur.execute("INSERT OR REPLACE INTO meta VALUES ('dim', ?)",  (str(DIM),))
    idx_con.commit()
    # pending = сообщения с текстом, которые не успели за этот запуск
    pending = 0
    if processed < len(todo_rows):
        pending = sum(1 for r in todo_rows[processed:]
                      if parts_by_msg.get(r[0]))
        sys.stderr.write(
            f"[semsearch] lazy_index: capped at {LAZY_MAX_PER_RUN} "
            f"embeddings, {pending} still pending\n")
    return {"added": added, "skipped": skipped, "pending": pending,
            "pruned": pruned}

def _filter_sessions(src_con, only_active, since_days):
    where, params = [], []
    if only_active: where.append("s.time_archived IS NULL")
    if since_days:
        cutoff_ms = int((time.time() - int(since_days)*86400) * 1000)
        where.append("s.time_created >= ?")
        params.append(cutoff_ms)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    return {r[0] for r in src_con.execute(
        f"SELECT id FROM session s {sql_where}", params).fetchall()}

def search(idx_con, src_con, query, *, top, role_filter, only_active, since_days):
    timings = {}
    t0 = time.time()
    info = lazy_index(idx_con, src_con)
    timings["index_catchup_ms"] = int((time.time()-t0)*1000)
    timings["indexed_new_messages"] = info["added"]
    timings["indexed_skipped"] = info.get("skipped", 0)
    timings["index_pending"] = info.get("pending", 0)
    timings["pruned_orphans"] = info.get("pruned", 0)

    t0 = time.time()
    allowed = _filter_sessions(src_con, only_active, since_days)
    timings["filter_ms"] = int((time.time()-t0)*1000)

    if not allowed:
        return {
            "results": [], "indexed_new_messages": info["added"],
            "model": _embedder().name, "dim": DIM,
            "timings_ms": timings,
        }

    t0 = time.time()
    qv = embed([query], is_query=True)[0]
    timings["embed_query_ms"] = int((time.time()-t0)*1000)

    t0 = time.time()
    if role_filter in ("user","assistant"):
        rows = idx_con.execute(
            "SELECT message_id, session_id, session_title, role, "
            "time_created, excerpt, embedding FROM message_vec WHERE role = ?",
            (role_filter,)).fetchall()
    else:
        rows = idx_con.execute(
            "SELECT message_id, session_id, session_title, role, "
            "time_created, excerpt, embedding FROM message_vec").fetchall()
    timings["fetch_ms"] = int((time.time()-t0)*1000)

    t0 = time.time()
    scored = []
    for mid, sid, ttl, role, tc, ex, blob in rows:
        if sid not in allowed: continue
        scored.append((cosine(qv, unpack(blob)),
                       {"message_id": mid,
                        "session_id": sid,
                        "session_title": ttl,
                        "role": role,
                        "time_created": tc,
                        "preview": ex}))
    scored.sort(key=lambda x: x[0], reverse=True)
    timings["knn_ms"] = int((time.time()-t0)*1000)

    out = scored[:min(int(top), 50)]
    return {
        "results": [{"score": round(s, 4), **meta} for s, meta in out],
        "indexed_new_messages": info["added"],
        "model": _embedder().name,
        "dim": DIM,
        "timings_ms": timings,
    }

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error":"missing_args_json"}))
        sys.exit(1)
    try:
        args = json.loads(sys.argv[1])
    except Exception as e:
        print(json.dumps({"error":"bad_args_json","detail":str(e)}))
        sys.exit(1)

    src = sqlite3.connect(f"file:{DB_SRC}?mode=ro", uri=True)
    idx = _ensure_index()

    try:
        result = search(
            idx, src,
            query=args["query"],
            top=args.get("top", 10),
            role_filter=args.get("role", "any"),
            only_active=bool(args.get("only_active", True)),
            since_days=args.get("since_days"),
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error":"search_failed","detail":str(e)[:400]}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
