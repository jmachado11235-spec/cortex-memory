#!/usr/bin/env python3
"""
Cortex Smart Loader -- Query-driven memory retrieval.

Instead of loading the entire vault, loads only atoms relevant to the current
query. Uses multi-dimensional JSON indexes, keyword scoring with temporal
layer bonuses, and status-based filtering.

Scoring algorithm:
    name match:        +10 per keyword
    tag match:          +8 per keyword
    project match:      +5 per keyword
    description match:  +4 per keyword
    body match:         +3 per keyword   (full-text, stemmed, accent-folded)
    path match:         +3 per keyword
    body relevance:     +0..1.5 (bm25, ranks body hits against each other)
    multi-keyword bonus: x1.5 if ALL keywords match
    temporal bonus:     +2 (hot), +1 (warm), +0 (cold)
    status penalty:     x0.3 (archived), x0.5 (superseded)

Body matching comes from index/fts.db (see cortex.fts_index). Curated fields
still lead: a hand-written description (+4) beats an average body mention
(+3), and only a densely relevant body hit (+3 plus the full bm25 bonus) can
edge past it. What changed is that a fact existing only inside a note is no
longer invisible. If the full-text index is missing, search silently falls
back to metadata only.

Usage:
    python -m cortex.smart_loader "deploy risk"           # keyword search
    python -m cortex.smart_loader "deploy risk" --no-fts  # metadata only
    python -m cortex.smart_loader --project myproject      # filter by project
    python -m cortex.smart_loader --project myproject --hot # only last 48h
    python -m cortex.smart_loader --type rule               # filter by type
    python -m cortex.smart_loader --tag risk --top 5        # top 5 by tag
    python -m cortex.smart_loader --summary                 # vault summary
"""
import argparse
import json
import sys
import re
from datetime import datetime

from cortex.config import get_vault_path, get_index_dir, get_layer_thresholds
from cortex.tracker import log_retrieval
from cortex.query import terms, fold, automatic_query, variants


VAULT = get_vault_path()
INDEX_DIR = get_index_dir()


def load_index(name: str) -> dict:
    path = INDEX_DIR / f"{name}.json"
    if not path.exists():
        print(f"Index {name} not found. Run index_builder first.", file=sys.stderr)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest() -> list[dict]:
    path = INDEX_DIR / "manifest.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def temporal_layer(updated_str: str) -> str:
    """Classify atom into hot/warm/cold based on updated date."""
    thresholds = get_layer_thresholds()
    try:
        updated = datetime.strptime(updated_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return "cold"
    delta = (datetime.now() - updated).days
    if delta <= thresholds["hot"]:
        return "hot"
    elif delta <= thresholds["warm"]:
        return "warm"
    return "cold"


def score_atom(atom: dict, keywords: list[str], fts_hit: dict = None) -> float:
    """
    Score an atom against query keywords. Higher = more relevant.

    fts_hit, when given, is this note's entry from cortex.fts_index.search():
    which keywords matched its body and how well it ranked against the others.
    """
    keyword_score = 0.0
    name_lower = fold(atom.get("name", ""))
    desc_lower = fold(atom.get("description", ""))
    tags = [t.lower() for t in atom.get("tags", [])]
    path_lower = atom.get("path", "").lower()
    project = atom.get("project", "").lower()
    body_hits = {k.lower() for k in (fts_hit or {}).get("body_hits", set())}

    for kw in keywords:
        kw = kw.lower()
        aliases = variants(kw)
        if any(k in name_lower for k in aliases):
            keyword_score += 10.0
        if any(k in tags for k in aliases):
            keyword_score += 8.0
        if kw == project:
            keyword_score += 5.0
        if any(k in desc_lower for k in aliases):
            keyword_score += 4.0
        if kw in body_hits:
            keyword_score += 3.0 * (fts_hit or {}).get('idf', {}).get(kw, 1.0)
        if any(k in path_lower for k in aliases):
            keyword_score += 3.0

    # bm25 relevance, so the strongest body match outranks a passing mention.
    # Capped at 1.5: an average body hit (3.0) stays below a description hit
    # (4.0), while a densely relevant one (4.5) is allowed to edge past it.
    if body_hits:
        keyword_score += 1.5 * float((fts_hit or {}).get("norm", 0.0))

    # Multi-keyword bonus: reward atoms matching ALL keywords
    if len(keywords) > 1:
        matched = sum(1 for kw in keywords if _metadata_matched(atom,[kw])
                      or kw.lower() in body_hits)
        if matched == len(keywords):
            keyword_score *= 1.5

    # Layer bonus as tiebreaker (only if there's already a keyword match)
    if keyword_score > 0:
        layer = temporal_layer(atom.get("updated", ""))
        layer_bonus = {"hot": 2.0, "warm": 1.0, "cold": 0.0}
        keyword_score += layer_bonus.get(layer, 0.0)

    # Status penalty
    if atom.get("status") == "archived":
        keyword_score *= 0.3
    elif atom.get("status") == "superseded":
        keyword_score *= 0.5

    return keyword_score


def _metadata_matched(atom: dict, keywords: list[str]) -> bool:
    """True if any keyword appears in the atom's frontmatter surfaces."""
    name_lower = fold(atom.get("name", ""))
    desc_lower = fold(atom.get("description", ""))
    tags = [t.lower() for t in atom.get("tags", [])]
    path_lower = atom.get("path", "").lower()
    project = atom.get("project", "").lower()
    return any(k in name_lower or k in desc_lower or k in path_lower or k in tags
               or k == project for kw in keywords for k in variants(kw))


def _fts_hits(keywords: list[str], use_fts: bool) -> dict:
    """Full-text hits keyed by path. Never fatal: no index means no hits."""
    if not use_fts:
        return {}
    try:
        from cortex import fts_index
        return fts_index.search(keywords)
    except Exception:
        return {}


def search_keywords(manifest: list[dict], keywords: list[str], top_n: int = 10,
                    use_fts: bool = True, status: str = 'active',
                    project: str = None, atom_type: str = None, tag: str = None,
                    hot_only: bool = False, min_match: int = 1,
                    current_state: bool = None) -> list[dict]:
    """Search manifest by keywords, return top N scored results."""
    flat_keywords = terms(keywords)
    if current_state is None:
        current_state = status == 'active' and bool(re.search(
            r'\b(current|authoritative|actual|vigente)\b', ' '.join(keywords), re.I))
    if not flat_keywords:
        return []
    manifest = filter_atoms(manifest, project, atom_type, tag, hot_only, status)

    hits = _fts_hits(flat_keywords, use_fts)

    scored = []
    for atom in manifest:
        hit = hits.get(atom.get("path", ""))
        matched = sum(_metadata_matched(atom, [kw]) or kw in (hit or {}).get('body_hits', [])
                      for kw in flat_keywords)
        if matched < min(min_match, len(flat_keywords)):
            continue
        s = score_atom(atom, flat_keywords, hit)
        if s > 0:
            body_hits = sorted(hit["body_hits"]) if hit else []
            scored.append({**atom, "_score": s,
                           "_layer": temporal_layer(atom.get("updated", "")),
                           "_body_hits": body_hits,
                           "_snippet": (hit or {}).get("snippet", ""),
                           # True when the body is the only reason this note
                           # surfaced -- the case the old index could not serve,
                           # and the only case worth spending a snippet on.
                           "_body_only": bool(body_hits)
                                         and not _metadata_matched(atom, flat_keywords)})
    def priority(row):
        path=row.get('path','').replace('\\','/')
        if not current_state: return 0
        if path.startswith('state/claude/'): return 2
        if path.startswith('state/projects/'): return 1
        return 0
    scored.sort(key=lambda x: (-priority(x), -x["_score"], x.get('path','').lower()))
    return scored[:top_n]


def retrieval_diagnostics(manifest: list[dict], keywords: list[str],
                          results: list[dict], top_n: int, use_fts: bool,
                          **filters) -> dict:
    """
    Re-run the search with the body index switched off and report the difference.

    This is the counterfactual that makes the full-text index measurable: for
    every real query we record what the answer would have been without it. It
    has to be captured at query time -- no amount of log analysis later can
    reconstruct it -- so it is computed on every search and costs one extra
    in-memory scoring pass (~7ms over 468 notes).
    """
    baseline = search_keywords(manifest, keywords, top_n, use_fts=False, **filters)

    top_now = results[0]["path"] if results else None
    top_before = baseline[0]['path'] if baseline else None
    body_only = [r["path"] for r in results if r.get("_body_only")]

    return {
        "version": 2,
        "fts": bool(use_fts),
        "baseline_n": len(baseline),
        "baseline_top": top_before,
        # The three outcomes worth counting, in increasing order of value:
        "top_changed": bool(use_fts and top_now != top_before),
        "top_body_only": bool(results and results[0].get("_body_only")),
        "rescued": bool(results and not baseline),
        "n_body_only": len(body_only),
        "body_only_paths": body_only[:10],
    }


def filter_atoms(manifest: list[dict], project: str = None, atom_type: str = None,
                 tag: str = None, hot_only: bool = False, status: str = "active") -> list[dict]:
    """Filter manifest by structured criteria."""
    results = []
    for atom in manifest:
        if project and atom.get("project") != project:
            continue
        if atom_type and atom.get("type") != atom_type:
            continue
        if tag and tag.lower() not in [t.lower() for t in atom.get("tags", [])]:
            continue
        if status and atom.get("status") != status:
            continue
        layer = temporal_layer(atom.get("updated", ""))
        if hot_only and layer != "hot":
            continue
        results.append({**atom, "_layer": layer})
    return results


def generate_boot_summary(manifest: list[dict]) -> str:
    """Generate a compact boot summary."""
    from collections import Counter

    active = [a for a in manifest if a.get("status") == "active"]
    hot = [a for a in active if temporal_layer(a.get("updated", "")) == "hot"]
    warm = [a for a in active if temporal_layer(a.get("updated", "")) == "warm"]

    proj_counts = Counter(a["project"] for a in active)
    type_counts = Counter(a["type"] for a in active)

    lines = [
        f"Cortex: {len(active)} active atoms ({len(hot)} hot, {len(warm)} warm)",
        f"Projects: {', '.join(f'{p}({c})' for p, c in proj_counts.most_common(6))}",
        f"Types: {', '.join(f'{t}({c})' for t, c in type_counts.most_common(5))}",
        "",
        "Hot atoms (last 48h):",
    ]
    for a in sorted(hot, key=lambda x: x.get("updated", ""), reverse=True)[:15]:
        lines.append(f"  [{a['project']}] {a['name']}")

    return "\n".join(lines)


def read_atom_content(path: str) -> str:
    """Read full content of an atom."""
    fpath = VAULT / path
    if not fpath.exists():
        return f"[NOT FOUND: {path}]"
    return fpath.read_text(encoding="utf-8", errors="replace")


def format_brief(atom: dict) -> str:
    """
    Format atom as brief: name, path, and one line of context.

    When the hit came from the note's body, that line is the matching passage
    rather than the note's opening -- it is the whole reason to surface a note
    whose frontmatter says nothing about the query.
    """
    layer = atom.get("_layer", "?")
    score = atom.get("_score", "")
    score_str = f" (score: {score:.1f})" if score else ""
    header = f"[{layer}] {atom['name']}{score_str}\n  {atom['path']}"

    body_hits = atom.get("_body_hits") or []
    snippet = (atom.get("_snippet") or "").replace("\n", " ").strip()
    if atom.get("_body_only") and snippet:
        marker = "+".join(body_hits)
        return f"{header}\n  body:{marker} ...{snippet[:200]}..."

    if atom.get('description'):
        return f"{header}\n  {atom['description'][:200]}"

    content = read_atom_content(atom["path"])
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()
    body_lines = [l for l in content.split("\n") if l.strip() and not l.startswith("#")][:3]
    body_preview = " | ".join(body_lines)[:200]
    return f"{header}\n  {body_preview}"


def main():
    # Body snippets carry the vault's real text, which is bilingual. The Windows
    # console defaults to cp1252 and would raise on the first accented word.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Cortex Smart Loader")
    parser.add_argument("query", nargs="*", help="Keyword search terms")
    parser.add_argument("--project", "-p", help="Filter by project")
    parser.add_argument("--type", "-t", help="Filter by type")
    parser.add_argument("--tag", help="Filter by tag")
    parser.add_argument("--hot", action="store_true", help="Hot layer only")
    parser.add_argument("--top", type=int, default=10, help="Max results")
    parser.add_argument("--paths", action="store_true", help="Output paths only")
    parser.add_argument("--content", action="store_true", help="Output full content")
    parser.add_argument("--brief", action="store_true", help="Output brief summaries")
    parser.add_argument("--summary", action="store_true", help="Boot summary")
    parser.add_argument("--all-status", action="store_true", help="Include archived/superseded")
    parser.add_argument("--no-fts", action="store_true",
                        help="Metadata only; skip the full-text body index")
    parser.add_argument('--automatic', action='store_true', help='Clean conversational prompt; reject weak matches')
    parser.add_argument('--no-log', action='store_true', help='Read-only diagnostic query')
    parser.add_argument('--current', action='store_true', help='Prefer canonical current-state documents')

    args = parser.parse_args()
    manifest = load_manifest()

    if not manifest:
        print("No manifest. Run: python -m cortex.index_builder", file=sys.stderr)
        sys.exit(1)

    if args.summary:
        print(generate_boot_summary(manifest))
        return

    import time
    t0 = time.monotonic()
    if args.automatic:
        args.current = args.current or bool(re.search(r'\b(current|authoritative|actual|vigente|memory system|memory setup)\b', ' '.join(args.query), re.I))
        query, inferred = automatic_query(' '.join(args.query))
        args.query = [query]
        args.project = args.project or inferred
    filters = dict(project=args.project, atom_type=args.type, tag=args.tag,
                   hot_only=args.hot, status=None if args.all_status else 'active',
                   min_match=2 if args.automatic else 1,
                   current_state=True if args.current else None)
    if args.query:
        results = search_keywords(manifest, args.query, args.top,
                                  use_fts=not args.no_fts, **filters)
    else:
        status = None if args.all_status else "active"
        results = filter_atoms(manifest, args.project, args.type, args.tag,
                               args.hot, status)[:args.top]
    duration_ms = (time.monotonic() - t0) * 1000
    query_str = " ".join(args.query) if args.query else f"filter:{args.project or ''}/{args.type or ''}/{args.tag or ''}"
    diagnostics = None
    if args.query:
        diagnostics = retrieval_diagnostics(manifest, args.query, results,
                                            args.top, not args.no_fts, **filters)
    if not args.no_log:
        log_retrieval(query_str, results, duration_ms, diagnostics)

    if not results:
        print("No results.", file=sys.stderr)
        return

    if args.content:
        for r in results:
            print(f"\n{'='*60}")
            print(f"# {r['name']} [{r.get('_layer', '?')}]")
            print(f"# {r['path']}")
            print(f"{'='*60}")
            print(read_atom_content(r["path"]))
    elif args.paths:
        for r in results:
            print(r["path"])
    elif args.brief or args.query:
        for r in results:
            print(format_brief(r))
            print()
    else:
        for r in results:
            print(f"  {r['path']}")


if __name__ == "__main__":
    main()
