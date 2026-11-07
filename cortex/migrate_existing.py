#!/usr/bin/env python3
"""
Cortex Migration -- Standardizes frontmatter on existing markdown files.

For each .md file in the vault:
  1. If has frontmatter: ADD missing fields (project, status, created, updated, tags, id)
  2. If no frontmatter: GENERATE from filename, directory, and content
  3. Does NOT move or rename files
  4. Does NOT delete content

Usage:
    python -m cortex.migrate_existing --dry-run    # preview changes
    python -m cortex.migrate_existing              # apply migration
    python -m cortex.migrate_existing --stats      # show current state
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from cortex.config import (get_vault_path, get_dir_to_project, get_keyword_tags,
                           get_skip_dirs, get_skip_files)


VAULT = get_vault_path()
SKIP_DIRS = get_skip_dirs()
SKIP_FILES = get_skip_files()


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse existing frontmatter. Returns (fm_dict, body_after_frontmatter)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 3:].lstrip("\n")

    fm = {}
    for line in fm_block.split("\n"):
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
            fm[key] = items
        else:
            fm[key] = val.strip('"').strip("'")
    return fm, body


def infer_project(filepath: Path) -> str:
    dir_map = get_dir_to_project()
    rel = filepath.relative_to(VAULT)
    for part in rel.parts:
        if part in dir_map:
            return dir_map[part]
    return "unknown"


def infer_type(fm: dict, filepath: Path, body: str) -> str:
    """Infer atom type from existing frontmatter, path, or content."""
    if fm.get("type") and fm["type"] != "unknown":
        return fm["type"]

    path_str = str(filepath).lower()
    if "feedback" in path_str or "rule" in path_str:
        return "feedback"
    if "incident" in path_str:
        return "incident"
    if "index" in filepath.name.lower():
        return "project"
    if "changelog" in filepath.name.lower():
        return "project"
    if any(kw in path_str for kw in ["insight", "thesis", "finding", "backtest"]):
        return "insight"
    if any(kw in path_str for kw in ["bio", "profile", "contact"]):
        return "person"
    if "archive" in path_str:
        return "reference"

    body_lower = body[:500].lower()
    if "**why:**" in body_lower and "**how to apply:**" in body_lower:
        return "feedback"
    if "lesson" in body_lower:
        return "lesson"

    return "reference"


def infer_status(filepath: Path, fm: dict) -> str:
    if "archive" in str(filepath).lower():
        return "archived"
    return fm.get("status", "active")


def extract_tags_simple(body: str, filepath: Path) -> list[str]:
    """Extract basic tags from content keywords."""
    tags = set()
    path_parts = filepath.relative_to(VAULT).parts
    for part in path_parts[:-1]:
        if part not in SKIP_DIRS and part not in {"archive", "atoms"}:
            tags.add(part)

    body_lower = body[:2000].lower()
    keyword_tags = get_keyword_tags()
    for tag, keywords in keyword_tags.items():
        if any(kw in body_lower for kw in keywords):
            tags.add(tag)

    return sorted(tags)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '_', text)
    return text[:60]


def build_frontmatter(fm: dict) -> str:
    """Serialize frontmatter dict to YAML string."""
    lines = ["---"]
    field_order = ["id", "name", "description", "type", "project", "status",
                   "created", "updated", "tags", "links"]
    for key in field_order:
        if key in fm:
            val = fm[key]
            if isinstance(val, list):
                lines.append(f"{key}: [{', '.join(str(v) for v in val)}]")
            else:
                lines.append(f"{key}: {val}")
    for key, val in fm.items():
        if key not in field_order:
            if isinstance(val, list):
                lines.append(f"{key}: [{', '.join(str(v) for v in val)}]")
            else:
                lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def migrate_file(filepath: Path, dry_run: bool) -> dict:
    """Migrate a single file. Returns change summary."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"status": "error", "error": str(e)}

    fm, body = parse_frontmatter(text)
    had_frontmatter = bool(fm)
    changes = []

    stat = filepath.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
    try:
        ctime = datetime.fromtimestamp(stat.st_birthtime).strftime("%Y-%m-%d")
    except AttributeError:
        ctime = mtime

    name_from_file = filepath.stem.replace("_", " ").replace("-", " ").title()

    if "id" not in fm:
        date_prefix = ctime.replace("-", "")
        fm["id"] = f"{date_prefix}_{slugify(fm.get('name', name_from_file))}"
        changes.append("added id")

    if "name" not in fm:
        fm["name"] = name_from_file
        changes.append("added name")

    if "type" not in fm or fm["type"] == "unknown":
        fm["type"] = infer_type(fm, filepath, body)
        changes.append(f"set type={fm['type']}")

    if "project" not in fm:
        fm["project"] = infer_project(filepath)
        changes.append(f"set project={fm['project']}")

    if "status" not in fm:
        fm["status"] = infer_status(filepath, fm)
        changes.append(f"set status={fm['status']}")

    if "created" not in fm:
        fm["created"] = ctime
        changes.append("added created")

    if "updated" not in fm:
        fm["updated"] = mtime
        changes.append("added updated")

    if "tags" not in fm:
        fm["tags"] = extract_tags_simple(body, filepath)
        changes.append(f"added {len(fm['tags'])} tags")

    if "links" not in fm:
        fm["links"] = []
        changes.append("added links")

    if "description" not in fm:
        desc_lines = [l.strip() for l in body.split("\n")
                      if l.strip() and not l.startswith("#") and not l.startswith("---")]
        if desc_lines:
            fm["description"] = desc_lines[0][:150].replace('"', "'")
        else:
            fm["description"] = f"{fm['type']} atom for {fm['project']}"
        changes.append("added description")

    if not changes:
        return {"status": "unchanged"}

    new_fm = build_frontmatter(fm)
    new_text = new_fm + "\n\n" + body

    if not dry_run:
        filepath.write_text(new_text, encoding="utf-8")

    return {"status": "migrated" if not had_frontmatter else "updated",
            "changes": changes, "had_fm": had_frontmatter}


def main():
    dry_run = "--dry-run" in sys.argv
    stats_only = "--stats" in sys.argv

    files = []
    for root, dirs, fnames in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(fnames):
            if fname.endswith(".md") and fname not in SKIP_FILES:
                fpath = Path(root) / fname
                if fpath.exists() and not fpath.is_symlink():
                    files.append(fpath)

    if stats_only:
        has_fm = 0
        no_fm = 0
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            if text.startswith("---") and text.find("---", 3) != -1:
                has_fm += 1
            else:
                no_fm += 1
        print(f"Total .md files: {len(files)}")
        print(f"With frontmatter: {has_fm}")
        print(f"Without frontmatter: {no_fm}")
        return

    print(f"Cortex Migration ({'DRY RUN' if dry_run else 'LIVE'})")
    print("=" * 60)
    print(f"Scanning {len(files)} files...\n")

    counts = {"migrated": 0, "updated": 0, "unchanged": 0, "error": 0}
    for f in files:
        rel = f.relative_to(VAULT)
        result = migrate_file(f, dry_run)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
        if status in ("migrated", "updated"):
            changes_str = ", ".join(result.get("changes", []))
            prefix = "NEW FM" if status == "migrated" else "UPDATE"
            print(f"  [{prefix}] {rel}: {changes_str}")

    print(f"\n{'='*60}")
    print(f"Results: {counts['migrated']} new frontmatter, {counts['updated']} updated, "
          f"{counts['unchanged']} unchanged, {counts['error']} errors")

    if dry_run and (counts["migrated"] + counts["updated"] > 0):
        print("\nRun without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
