#!/usr/bin/env python3
"""
Cortex FTS Effectiveness Report -- is the body index earning its place?

Every search since 2026-09-08 records a counterfactual: what smart_loader would
have returned with the body index switched off. This reads those records back
and answers the only question that matters -- how often did searching note
bodies change the answer, and on which projects.

Three outcomes are counted, in increasing order of value:

    top changed    the body index reordered the winner
    top body-only  the winning note matched ONLY in its body
    rescued        the query returned nothing at all before

Changes in ranking do not establish correctness. Body-only matches are expected
for details too specific to fit a concise description. Validate quality separately.

Usage:
    python -m cortex.fts_report                # all instrumented queries
    python -m cortex.fts_report --days 7       # last week
    python -m cortex.fts_report --days 30      # last month
    python -m cortex.fts_report --queries      # list the queries FTS rescued
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cortex.config import get_index_dir

# Below this many instrumented queries, every percentage here is noise.
MIN_SAMPLE = 30


def _load_log() -> list[dict]:
    path = get_index_dir() / "access_log.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _load_projects() -> dict:
    """Map note path -> project, for attributing body-only hits."""
    path = get_index_dir() / "manifest.json"
    if not path.exists():
        return {}
    try:
        return {a["path"]: a.get("project", "?")
                for a in json.loads(path.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _parse_ts(entry: dict):
    try:
        return datetime.fromisoformat(entry["ts"])
    except (KeyError, ValueError):
        return None


def report(days: int = None, show_queries: bool = False) -> str:
    entries = _load_log()
    if not entries:
        return "No retrieval log yet. Nothing has searched the vault."

    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        kept = []
        for e in entries:
            ts = _parse_ts(e)
            if ts and ts >= cutoff:
                kept.append(e)
        entries = kept

    # Only keyword searches carry the counterfactual; filter runs never do.
    instrumented = [e for e in entries if isinstance(e.get("fts_effect"), dict)
                    and e['fts_effect'].get('version') == 2
                    and e['fts_effect'].get('fts') is True]
    legacy = len(entries) - len(instrumented)

    window = f"last {days} days" if days else "all time"
    out = [f"FTS effectiveness -- {window}",
           f"  {len(entries)} logged retrievals, {len(instrumented)} instrumented"]
    if legacy:
        out.append(f"  {legacy} lack corrected v2 FTS-enabled diagnostics and are "
                   f"excluded, not counted as no-effect")
    if not instrumented:
        out.append("")
        out.append("  Nothing to measure yet. Run some real searches:")
        out.append('    python -m cortex.smart_loader "keywords" --brief --top 5')
        return "\n".join(out)

    n = len(instrumented)
    fx = [e["fts_effect"] for e in instrumented]
    rescued = [e for e in instrumented if e["fts_effect"].get("rescued")]
    top_body = [e for e in instrumented if e["fts_effect"].get("top_body_only")]
    changed = [e for e in instrumented if e["fts_effect"].get("top_changed")]
    any_effect = [e for e in instrumented
                  if e["fts_effect"].get("rescued")
                  or e["fts_effect"].get("top_changed")
                  or e["fts_effect"].get("n_body_only")]
    zero_now = [e for e in instrumented if e.get("n_results", 0) == 0]

    def pct(k):
        return f"{k / n * 100:5.1f}%"

    out += ["",
            f"  any effect on results   {len(any_effect):4d}  {pct(len(any_effect))}",
            f"  top result changed      {len(changed):4d}  {pct(len(changed))}",
            f"  top matched body only   {len(top_body):4d}  {pct(len(top_body))}",
            f"  rescued (0 -> some)     {len(rescued):4d}  {pct(len(rescued))}",
            f"  still zero results      {len(zero_now):4d}  {pct(len(zero_now))}"]

    avg_body = sum(f.get("n_body_only", 0) for f in fx) / n
    lat = [e.get("duration_ms", 0) for e in instrumented if e.get("duration_ms")]
    out.append(f"  body-only hits/query    {avg_body:5.2f}")
    if lat:
        out.append(f"  median latency          {sorted(lat)[len(lat) // 2]:5.1f} ms")

    # Where the rescues land: those projects have descriptions that do not
    # describe. This is the actionable half of the report.
    projects = _load_projects()
    hit_projects = Counter()
    for f in fx:
        for p in f.get("body_only_paths", []):
            hit_projects[projects.get(p, "?")] += 1
    if hit_projects:
        out += ["", "  Body-only hits by project (details found beyond descriptions):"]
        for proj, cnt in hit_projects.most_common(8):
            out.append(f"    {proj:24s} {cnt:4d}")

    if show_queries:
        interesting = [(e["query"], e["fts_effect"]) for e in any_effect]
        if interesting:
            out += ["", "  Queries the body index changed:"]
            for q, f in interesting[:40]:
                why = ("rescued" if f.get("rescued") else
                       "body won" if f.get("top_body_only") else
                       "reordered" if f.get("top_changed") else "extra hits")
                out.append(f"    [{why:9s}] {q}")

    # Verdict
    out += ["", "  Verdict:"]
    if n < MIN_SAMPLE:
        out.append(f"    Too early. {n} instrumented queries; wait for {MIN_SAMPLE}+ "
                   f"before reading the percentages above.")
    else:
        out.append("    This measures changed retrieval, not answer correctness.")
        out.append("    Use the fixed recall evaluation and reviewed real queries to judge quality.")
        out.append("    Body-only matches can be useful detail; they do not imply poor descriptions.")
    return "\n".join(out)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Cortex FTS effectiveness report")
    ap.add_argument("--days", type=int, help="Only look at the last N days")
    ap.add_argument("--queries", action="store_true",
                    help="List the queries the body index changed")
    args = ap.parse_args()
    print(report(args.days, args.queries))


if __name__ == "__main__":
    main()
