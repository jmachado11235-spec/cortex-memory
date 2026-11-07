#!/usr/bin/env python3
"""
Cortex Full-Text Index -- SQLite FTS5 over note bodies.

The JSON indexes carry frontmatter only, so a fact written in a note body was
unreachable by search: findable only by opening the note through its MOC. This
module adds one more index shard -- index/fts.db -- holding the full text of
every note, so smart_loader can find a note by what it actually says.

The files stay the source of truth. fts.db is derived and disposable; delete it
and the next index_builder run rebuilds it.

    notes      FTS5 virtual table: name, tags, description, body
    note_meta  ordinary table: project, type, status, updated (for filtering)

Tokenizer is "porter unicode61 remove_diacritics 2" -- English stemming plus
accent folding, so "amortizacion" finds "amortizacion" and "payments" finds
"payment". Ranking is bm25 with the body weighted below the curated fields.

Usage:
    python -m cortex.fts_index                    # rebuild the index
    python -m cortex.fts_index "search terms"     # query it directly
    python -m cortex.fts_index --stats            # what is indexed
"""
import os
import re
import sqlite3
import sys
import hashlib
import uuid
import math
from cortex.query import variants
from pathlib import Path

from cortex.config import get_vault_path, get_index_dir, get_skip_dirs, get_skip_files
from cortex.index_builder import parse_frontmatter, infer_project

VAULT = get_vault_path()
INDEX_DIR = get_index_dir()
DB_PATH = INDEX_DIR / "fts.db"

# bm25 column weights, in the FTS5 column order below. The curated surfaces
# outrank raw body text: a hit in a hand-written description means more than a
# passing mention in a paragraph.
BM25_WEIGHTS = (0.0, 8.0, 6.0, 4.0, 1.0)  # path, name, tags, description, body

TOKENIZER = "porter unicode61 remove_diacritics 2"

SCHEMA = f"""
CREATE VIRTUAL TABLE notes USING fts5(
    path UNINDEXED,
    name,
    tags,
    description,
    body,
    tokenize = "{TOKENIZER}"
);
CREATE TABLE note_meta(
    path        TEXT PRIMARY KEY,
    name        TEXT,
    project     TEXT,
    type        TEXT,
    status      TEXT,
    updated     TEXT,
    created     TEXT,
    description TEXT,
    words       INTEGER,
    sha256      TEXT
);
"""


def fts5_available() -> bool:
    """True if this Python's sqlite3 was built with FTS5."""
    try:
        con = sqlite3.connect(":memory:")
        con.execute(f'CREATE VIRTUAL TABLE t USING fts5(a, tokenize="{TOKENIZER}")')
        con.close()
        return True
    except sqlite3.Error:
        return False


def strip_frontmatter(text: str) -> str:
    """Return the note body with its YAML frontmatter removed."""
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:].lstrip("\n")


def iter_notes():
    """Yield (path, frontmatter, body) for every note in the vault."""
    skip_dirs = get_skip_dirs()
    skip_files = get_skip_files()
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in sorted(files):
            if not fname.endswith(".md") or fname in skip_files:
                continue
            fpath = Path(root) / fname
            if not fpath.exists() or fpath.is_symlink():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            yield fpath, fm, strip_frontmatter(text)


def build(verbose: bool = False) -> int:
    """Rebuild fts.db from scratch. Returns the number of notes indexed."""
    if not fts5_available():
        print("FTS5 is not available in this Python's sqlite3; skipping full-text index.",
              file=sys.stderr)
        return 0

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = DB_PATH.with_name('fts.' + uuid.uuid4().hex + '.tmp')

    con = sqlite3.connect(tmp_path)
    con.executescript(SCHEMA)

    rows_fts, rows_meta = [], []
    for fpath, fm, body in iter_notes():
        rel = str(fpath.relative_to(VAULT))
        project = fm.get("project", "") or infer_project(fpath)
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        name = fm.get("name", fpath.stem.replace("_", " "))
        desc = fm.get("description", "")
        rows_fts.append((rel, name, " ".join(tags), desc, body))
        rows_meta.append((rel, name, project, fm.get("type", "unknown"),
                          fm.get("status", "active"), fm.get("updated", ""),
                          fm.get("created", ""), desc, len(body.split()),
                          hashlib.sha256(fpath.read_bytes()).hexdigest()))

    con.executemany("INSERT INTO notes (path, name, tags, description, body) "
                    "VALUES (?, ?, ?, ?, ?)", rows_fts)
    con.executemany("INSERT INTO note_meta (path, name, project, type, status, "
                    "updated, created, description, words, sha256) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_meta)
    con.execute("INSERT INTO notes(notes) VALUES('optimize')")
    con.commit()
    con.close()

    # Swap in atomically-ish: a half-written index is worse than a stale one.
    os.replace(tmp_path, DB_PATH)

    if verbose:
        kb = DB_PATH.stat().st_size / 1024
        words = sum(r[8] for r in rows_meta)
        print(f"Full-text index: {len(rows_fts)} notes, {words:,} words -> "
              f"{DB_PATH.name} ({kb:.0f} KB)")
    return len(rows_fts)


def _quote(token: str) -> str:
    """Quote a token for an FTS5 MATCH expression."""
    return '"' + token.replace('"', '""') + '"'


def available() -> bool:
    """True if a usable full-text index exists on disk."""
    return DB_PATH.exists() and fts5_available()


def search(keywords: list[str], limit: int = 200) -> dict:
    """
    Full-text search over note bodies and curated fields.

    Returns {path: {"score": float, "snippet": str, "body_hits": set[str]}},
    where body_hits names the keywords that matched the note's *body* -- the
    part the JSON manifest cannot see. Never raises: a broken or missing index
    degrades to no results rather than taking smart_loader down with it.
    """
    if not keywords or not available():
        return {}
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}

    results: dict[str, dict] = {}
    weights = ", ".join(str(w) for w in BM25_WEIGHTS)
    try:
        # 1. Rank every note that matches any keyword anywhere.
        expr = " OR ".join(_quote(k) for kw in keywords for k in variants(kw))
        sql = (f"SELECT path, -bm25(notes, {weights}) AS rank, "
               f"snippet(notes, 4, '', '', ' ... ', 14) AS snip "
               f"FROM notes WHERE notes MATCH ? ORDER BY rank DESC LIMIT ?")
        for path, rank, snip in con.execute(sql, (expr, limit)):
            results[path] = {"score": float(rank), "snippet": (snip or "").strip(),
                             "body_hits": set()}

        # 2. Per keyword, record which notes matched in the BODY column. That is
        #    the signal the metadata scorer structurally cannot produce.
        population=con.execute('SELECT COUNT(*) FROM note_meta').fetchone()[0]
        for kw in keywords:
            try:
                rows = con.execute("SELECT path FROM notes WHERE notes MATCH ?",
                                   ('body : ('+' OR '.join(_quote(k) for k in variants(kw))+')',)).fetchall()
            except sqlite3.Error:
                continue
            for (path,) in rows:
                if path in results:
                    results[path]["body_hits"].add(kw)
                    results[path].setdefault('idf',{})[kw]=max(.15,
                        math.log((population+1)/(len(rows)+1))/math.log(population+1)) if population else 1
    except sqlite3.Error:
        return {}
    finally:
        con.close()

    # Normalise bm25 into a 0-1 band so callers can scale it into their own
    # scoring range without knowing anything about bm25's magnitudes.
    if results:
        best = max(r["score"] for r in results.values()) or 1.0
        for r in results.values():
            r["norm"] = max(0.0, min(1.0, r["score"] / best))
    return results


def stats() -> str:
    if not available():
        return "No full-text index. Run: python -m cortex.fts_index"
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    n, words = con.execute("SELECT COUNT(*), COALESCE(SUM(words), 0) FROM note_meta").fetchone()
    by_proj = con.execute("SELECT project, COUNT(*), SUM(words) FROM note_meta "
                          "GROUP BY project ORDER BY SUM(words) DESC LIMIT 8").fetchall()
    con.close()
    kb = DB_PATH.stat().st_size / 1024
    lines = [f"Full-text index: {n} notes, {words:,} words searchable, {kb:.0f} KB",
             f"  {DB_PATH}", "", "  Largest projects by indexed words:"]
    for proj, cnt, w in by_proj:
        lines.append(f"    {proj:22s} {cnt:4d} notes  {w:>7,} words")
    return "\n".join(lines)


def main():
    # Snippets carry the vault's real text, which is bilingual. The Windows
    # console defaults to cp1252 and would raise on the first accented word.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = [a for a in sys.argv[1:]]
    if "--stats" in args:
        print(stats())
        return
    query = [a for a in args if not a.startswith("-")]
    if query:
        terms = []
        for q in query:
            terms.extend(q.split())
        hits = search(terms)
        if not hits:
            print("No results.", file=sys.stderr)
            return
        ranked = sorted(hits.items(), key=lambda kv: -kv[1]["score"])[:10]
        for path, hit in ranked:
            body = ",".join(sorted(hit["body_hits"])) or "-"
            print(f"[{hit['norm']:.2f}] {path}")
            print(f"  body match: {body}")
            if hit["snippet"]:
                print(f"  ...{hit['snippet']}...")
            print()
        return
    build(verbose=True)


if __name__ == "__main__":
    main()
