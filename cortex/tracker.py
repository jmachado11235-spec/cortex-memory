"""
Cortex Retrieval Tracker -- Logs every search and tracks atom access patterns.

Writes to two files in the vault:
  index/access_log.jsonl   -- append-only log of every retrieval
  index/access_stats.json  -- per-atom access count and last_accessed

This data enables:
  - Precision measurement (review logs, mark relevance)
  - Dead atom detection (never accessed)
  - Usage-weighted scoring (future)
  - Paper-quality evaluation metrics

Usage:
    from cortex.tracker import log_retrieval, get_stats, print_report
    log_retrieval("deploy risk", results, duration_ms)
    print_report()  # show access stats
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from cortex.config import get_index_dir


def _log_path() -> Path:
    return get_index_dir() / "access_log.jsonl"


def _stats_path() -> Path:
    return get_index_dir() / "access_stats.json"


def _load_stats() -> dict:
    p = _stats_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save_stats(stats: dict):
    p = _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")


def log_retrieval(query: str, results: list[dict], duration_ms: float = 0,
                  diagnostics: dict = None):
    """
    Log a retrieval event and update per-atom access stats.

    diagnostics, when given, carries the full-text counterfactual from
    smart_loader.retrieval_diagnostics(): what this query would have returned
    without the body index. Entries written before 2026-09-08 do not have it,
    so any analysis must treat its absence as "unknown", not as "no effect".
    """
    now = datetime.now(timezone.utc).isoformat()
    paths = [r.get("path", "") for r in results]
    scores = [r.get("_score", 0) for r in results]
    layers = [r.get("_layer", "?") for r in results]

    # Append to access log
    entry = {
        "ts": now,
        "query": query,
        "n_results": len(results),
        "top_score": round(scores[0], 1) if scores else 0,
        "top_layer": layers[0] if layers else "?",
        "results": paths[:10],
        "duration_ms": round(duration_ms, 1),
    }
    if diagnostics:
        entry["fts_effect"] = diagnostics
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Update per-atom access stats
    stats = _load_stats()
    for p in paths:
        if p not in stats:
            stats[p] = {"count": 0, "first_accessed": now, "last_accessed": now}
        stats[p]["count"] += 1
        stats[p]["last_accessed"] = now
    _save_stats(stats)


def get_stats() -> dict:
    """Return per-atom access stats."""
    return _load_stats()


def get_log(last_n: int = 50) -> list[dict]:
    """Return last N retrieval log entries."""
    log = _log_path()
    if not log.exists():
        return []
    lines = log.read_text(encoding="utf-8").strip().split("\n")
    entries = [json.loads(l) for l in lines[-last_n:]]
    return entries


def print_report():
    """Print access stats summary."""
    stats = _load_stats()
    log = get_log(1000)

    if not stats and not log:
        print("No retrieval data yet.")
        return

    # Query stats
    print(f"Total retrievals: {len(log)}")
    if log:
        layers = [e["top_layer"] for e in log]
        hot_pct = layers.count("hot") / len(layers) * 100 if layers else 0
        warm_pct = layers.count("warm") / len(layers) * 100 if layers else 0
        cold_pct = layers.count("cold") / len(layers) * 100 if layers else 0
        avg_ms = sum(e.get("duration_ms", 0) for e in log) / len(log)
        print(f"Avg latency: {avg_ms:.1f}ms")
        print(f"Top result layer: hot={hot_pct:.0f}% warm={warm_pct:.0f}% cold={cold_pct:.0f}%")

    # Atom stats
    if stats:
        by_count = sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True)
        never = sum(1 for _, v in by_count if v["count"] == 0)
        print(f"\nAtoms accessed: {len(stats)}")
        print(f"Top 10 most accessed:")
        for path, s in by_count[:10]:
            print(f"  {s['count']:4d}x  {path}")
