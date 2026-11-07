#!/usr/bin/env python3
"""
Cortex Auto-Linker -- Discovers cross-references between atoms and injects wikilinks.

Scans all atoms for:
  1. Backtick path references (`feedback/foo.md`) and converts to [[feedback/foo]]
  2. Atom name mentions (case-insensitive) and suggests wikilinks
  3. Graph analysis: identifies orphans (no links) and hubs (most connections)

Usage:
    python -m cortex.auto_linker --dry-run     # preview changes
    python -m cortex.auto_linker               # apply wikilink injection
    python -m cortex.auto_linker --report      # cross-reference report
"""
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from cortex.config import get_vault_path, get_index_dir, get_skip_dirs


VAULT = get_vault_path()
INDEX_DIR = get_index_dir()
SKIP_DIRS = get_skip_dirs()


def load_manifest() -> list[dict]:
    path = INDEX_DIR / "manifest.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def build_name_map(manifest: list[dict]) -> dict:
    """Map atom names (lowercased) to their paths."""
    name_map = {}
    for a in manifest:
        name = a.get("name", "")
        if len(name) > 5:
            name_map[name.lower()] = a["path"]
    return name_map


def convert_backtick_refs(text: str) -> tuple[str, int]:
    """Convert `path/file.md` references to [[path/file]] wikilinks."""
    count = 0

    def replace_ref(m):
        nonlocal count
        ref = m.group(1)
        if "/" in ref and not ref.startswith("http"):
            wiki = ref.replace(".md", "")
            count += 1
            return f"[[{wiki}]]"
        return m.group(0)

    new_text = re.sub(r'`([a-zA-Z0-9_/.-]+\.md)`', replace_ref, text)
    return new_text, count


def find_name_mentions(text: str, name_map: dict, own_path: str) -> list[str]:
    """Find mentions of other atom names in text."""
    found = []
    text_lower = text.lower()
    for name, path in name_map.items():
        if path == own_path:
            continue
        if name in text_lower and f"[[{path.replace('.md', '')}]]" not in text:
            found.append(path)
    return found[:5]


def build_cross_reference_report(manifest: list[dict]) -> str:
    """Generate a report of cross-references in the vault."""
    graph = {}
    graph_path = INDEX_DIR / "graph.json"
    if graph_path.exists():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))

    # Normalize paths: strip .md extension for consistent comparison
    def norm(p):
        return p.removesuffix(".md") if p.endswith(".md") else p

    all_paths = {norm(a["path"]) for a in manifest}
    linked_to = set()
    for targets in graph.values():
        linked_to.update(norm(t) for t in targets)

    has_outgoing = {norm(k) for k in graph.keys()}
    orphans = all_paths - linked_to - has_outgoing

    connection_count = defaultdict(int)
    for source, targets in graph.items():
        connection_count[norm(source)] += len(targets)
        for t in targets:
            connection_count[norm(t)] += 1

    hubs = sorted(connection_count.items(), key=lambda x: x[1], reverse=True)[:10]

    lines = [
        "Cross-Reference Report",
        "=" * 50,
        f"Total atoms: {len(all_paths)}",
        f"Atoms with links: {len(has_outgoing)}",
        f"Orphans (no links in/out): {len(orphans)}",
        "",
        "Top 10 Hubs:",
    ]
    for path, count in hubs:
        lines.append(f"  {count:3d} links: {path}")

    lines.append(f"\nOrphans ({len(orphans)}):")
    for p in sorted(orphans)[:20]:
        lines.append(f"  {p}")
    if len(orphans) > 20:
        lines.append(f"  ... and {len(orphans) - 20} more")

    return "\n".join(lines)


def process_vault(dry_run: bool = True) -> dict:
    """Scan vault and inject/suggest wikilinks."""
    manifest = load_manifest()
    if not manifest:
        print("No manifest. Run index_builder first.", file=sys.stderr)
        return {}

    name_map = build_name_map(manifest)
    stats = {"files_changed": 0, "refs_converted": 0}

    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            fpath = Path(root) / fname
            rel_path = str(fpath.relative_to(VAULT))

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            new_text, ref_count = convert_backtick_refs(text)

            if ref_count > 0:
                stats["refs_converted"] += ref_count
                stats["files_changed"] += 1
                if dry_run:
                    print(f"  Would convert {ref_count} refs in {rel_path}")
                else:
                    fpath.write_text(new_text, encoding="utf-8")
                    print(f"  Converted {ref_count} refs in {rel_path}")

    return stats


def main():
    dry_run = "--dry-run" in sys.argv
    report = "--report" in sys.argv

    if report:
        manifest = load_manifest()
        print(build_cross_reference_report(manifest))
        return

    print(f"Auto-Linker ({'DRY RUN' if dry_run else 'LIVE'})")
    print("=" * 50)
    stats = process_vault(dry_run)
    print(f"\nFiles changed: {stats.get('files_changed', 0)}")
    print(f"Refs converted: {stats.get('refs_converted', 0)}")

    if dry_run and stats.get("refs_converted", 0) > 0:
        print("\nRun without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
