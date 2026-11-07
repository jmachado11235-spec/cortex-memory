#!/usr/bin/env python3
"""
Cortex Index Builder -- Scans vault, parses frontmatter, builds JSON indexes.

Walks every .md file in the vault, extracts YAML frontmatter metadata, and
produces five JSON indexes for fast retrieval:

    manifest.json     -- flat list of all atom metadata
    by_project.json   -- atoms grouped by project
    by_type.json      -- atoms grouped by type
    by_tag.json       -- atoms grouped by tag
    graph.json        -- directed link graph from wikilinks and backtick refs

Usage:
    python -m cortex.index_builder              # build all indexes
    python -m cortex.index_builder --stats      # print stats only
"""
import json
import os
import re
import sys
import hashlib
import uuid
from cortex.storage import atomic_json, index_lock
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from cortex.config import (get_vault_path, get_index_dir, get_dir_to_project,
                           get_skip_dirs, get_skip_files)


VAULT = get_vault_path()
INDEX_DIR = get_index_dir()
SKIP_DIRS = get_skip_dirs()
SKIP_FILES = get_skip_files()


def parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from markdown. Returns dict or empty."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm_block = text[3:end].strip()
    result = {}
    for line in fm_block.split("\n"):
        if line[:1].isspace():
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
            result[key] = items
        else:
            result[key] = val.strip('"').strip("'")
    return result


def infer_project(filepath: Path) -> str:
    """Infer project from directory path using config mapping."""
    dir_map = get_dir_to_project()
    rel = filepath.relative_to(VAULT)
    for part in rel.parts:
        if part in dir_map:
            return dir_map[part]
    return "unknown"


def extract_links(text: str, current_path: Path) -> list[str]:
    """Extract wikilinks [[target]] and backtick references `path/file.md`."""
    links = []
    for m in re.finditer(r'\[\[([^\]]+)\]\]', text):
        target = m.group(1).split("|")[0].strip()
        links.append(target)
    for m in re.finditer(r'`([a-zA-Z0-9_/]+\.md)`', text):
        links.append(m.group(1))
    return links


def extract_tags_from_content(text: str) -> list[str]:
    """Extract #hashtags from content."""
    return [m.group(1) for m in re.finditer(r'(?:^|\s)#([a-zA-Z][a-zA-Z0-9_-]+)', text)]


def scan_vault() -> list[dict]:
    """Scan all .md files and return atom metadata."""
    atoms = []
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".md") or fname in SKIP_FILES:
                continue
            fpath = Path(root) / fname
            if not fpath.exists() or fpath.is_symlink():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            fm = parse_frontmatter(text)
            rel_path = str(fpath.relative_to(VAULT))
            lines = text.count("\n") + 1
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime).strftime("%Y-%m-%d")

            fm_project = fm.get("project", "")
            project = fm_project if fm_project and fm_project != "unknown" else infer_project(fpath)

            atom = {
                "sha256": hashlib.sha256(fpath.read_bytes()).hexdigest(),
                "path": rel_path,
                "name": fm.get("name", fname.replace(".md", "").replace("_", " ").title()),
                "type": fm.get("type", "unknown"),
                "project": project,
                "status": fm.get("status", "active"),
                "tags": fm.get("tags", []) + extract_tags_from_content(text),
                "updated": fm.get("updated", mtime),
                "created": fm.get("created", mtime),
                "lines": lines,
                "size_bytes": len(text.encode("utf-8")),
                "links_out": extract_links(text, fpath),
                "description": fm.get("description", ""),
            }
            atom["tags"] = list(dict.fromkeys(t.lower() for t in atom["tags"] if t))
            atoms.append(atom)
    return atoms


def build_indexes(atoms: list[dict]) -> dict:
    """Build all indexes from atom list."""
    by_project = defaultdict(list)
    by_type = defaultdict(list)
    by_tag = defaultdict(list)
    graph = defaultdict(list)

    for a in atoms:
        entry = {"path": a["path"], "name": a["name"], "type": a["type"],
                 "tags": a["tags"], "updated": a["updated"], "status": a["status"]}

        by_project[a["project"]].append({**entry, "project": a["project"]})
        by_type[a["type"]].append({**entry, "project": a["project"]})

        for tag in a["tags"]:
            by_tag[tag].append({"path": a["path"], "name": a["name"],
                                "project": a["project"], "type": a["type"]})

        if a["links_out"]:
            graph[a["path"]] = a["links_out"]

    return {
        "by_project": dict(by_project),
        "by_type": dict(by_type),
        "by_tag": dict(by_tag),
        "graph": dict(graph),
    }


def write_indexes(indexes: dict, manifest: list[dict]):
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in indexes.items():
        path = INDEX_DIR / f"{name}.json"
        atomic_json(path, data)

    manifest_path = INDEX_DIR / "manifest.json"
    clean = [{k: v for k, v in a.items() if k != "links_out"} for a in manifest]
    atomic_json(manifest_path, clean)


def print_stats(atoms: list[dict]):
    print(f"\n{'='*60}")
    print("CORTEX VAULT STATS")
    print(f"{'='*60}")
    print(f"Total atoms: {len(atoms)}")
    print(f"Total size: {sum(a['size_bytes'] for a in atoms) / 1024:.0f} KB")
    print(f"Total lines: {sum(a['lines'] for a in atoms)}")
    print()

    for label, key in [("By Project", "project"), ("By Type", "type"), ("By Status", "status")]:
        counts = Counter(a[key] for a in atoms)
        print(f"{label}:")
        for val, cnt in counts.most_common():
            print(f"  {val:20s} {cnt:4d}")
        print()

    no_type = [a for a in atoms if a["type"] == "unknown"]
    print(f"Missing type: {len(no_type)}")
    if no_type:
        for a in no_type[:10]:
            print(f"  {a['path']}")
        if len(no_type) > 10:
            print(f"  ... and {len(no_type) - 10} more")


def main():
    with index_lock(INDEX_DIR):
        return build_main()


def build_main():
    stats_only = "--stats" in sys.argv
    atoms = scan_vault()
    indexes = build_indexes(atoms)

    if not stats_only:
        write_indexes(indexes, atoms)
        print(f"Indexed {len(atoms)} atoms -> {INDEX_DIR}/")
        # Full-text shard over note bodies. Imported here, not at module level,
        # because fts_index reuses this module's parsers. A failure here must
        # never cost us the JSON indexes that were already written.
        if "--no-fts" not in sys.argv:
            try:
                from cortex import fts_index
                fts_index.build(verbose=True)
            except Exception as exc:
                print(f"Full-text index failed; checkpoint is NOT verified: {exc}", file=sys.stderr)
                raise
        atomic_json(INDEX_DIR/'generation.json', {
            'id': uuid.uuid4().hex, 'built_at': datetime.now().isoformat(),
            'manifest_sha256': hashlib.sha256((INDEX_DIR/'manifest.json').read_bytes()).hexdigest(),
            'fts': '--no-fts' not in sys.argv,
        })

    print_stats(atoms)


if __name__ == "__main__":
    main()
