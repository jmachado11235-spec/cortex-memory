#!/usr/bin/env python3
"""
Cortex MOC Auto-Refresher -- Keeps Map-of-Content files current with live data.

This module provides the generic marker-based update engine. It replaces
content between HTML marker pairs in any markdown file:

    <!-- AUTO:section_name -->
    ...content replaced on each run...
    <!-- /AUTO:section_name -->

The engine is designed to be extended with custom data collectors. Override
refresh_all() or call update_file() directly with your own rendered content.

Built-in capabilities:
    - Marker-based file updates (atomic write via temp file + rename)
    - Frontmatter date auto-update
    - Incident age annotation (e.g., "(Apr 8 EOD)" gets "*(5d ago)*")
    - Vault atom counts from manifest.json

Usage:
    python -m cortex.moc_refresher              # refresh with built-in collectors
    python -m cortex.moc_refresher --dry-run    # preview without writing
    python -m cortex.moc_refresher --verbose    # debug logging
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from cortex.config import get_vault_path, get_index_dir


VAULT = get_vault_path()
MEMORY_MD = VAULT / "MEMORY.md"
MOC_DIR = VAULT / "_moc"
INDEX_DIR = get_index_dir()

log = logging.getLogger("cortex.moc_refresher")


@dataclass
class VaultCounts:
    total_atoms: int
    active_atoms: int


@dataclass
class UpdateResult:
    path: str
    markers_found: list
    markers_missing: list
    changed: bool


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_vault_counts() -> Optional[VaultCounts]:
    """Count atoms from manifest.json."""
    manifest_path = INDEX_DIR / "manifest.json"
    if not manifest_path.exists():
        log.warning("manifest.json not found")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        total = len(manifest)
        active = sum(1 for a in manifest if a.get("status") == "active")
        return VaultCounts(total_atoms=total, active_atoms=active)
    except Exception as e:
        log.error("Manifest parse failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Computers (pure, no I/O)
# ---------------------------------------------------------------------------

def compute_countdown(target_date: date, today: Optional[date] = None) -> str:
    """Compute a human-readable countdown to a target date."""
    today = today or date.today()
    delta = (target_date - today).days
    if delta < 0:
        return f"OVERDUE by {abs(delta)}d"
    elif delta == 0:
        return "TODAY"
    return f"~{delta}d"


def compute_incident_ages(text: str, today: Optional[date] = None) -> str:
    """Annotate incident lines with age. E.g., '(Apr 8 EOD)' adds '*(5d ago)*'."""
    today = today or date.today()
    age_pat = re.compile(r'\s*\*\(\d+d ago\)\*\s*$')
    month_map = {m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
    lines = text.split("\n")
    result = []
    for line in lines:
        line = age_pat.sub('', line)
        m = re.search(r'\((\w{3})\s+(\d{1,2})', line)
        if m and m.group(1) in month_map:
            try:
                month = month_map[m.group(1)]
                day = int(m.group(2))
                incident_date = date(today.year, month, day)
                age_days = (today - incident_date).days
                if age_days >= 0:
                    line = f"{line} *({age_days}d ago)*"
            except ValueError:
                pass
        result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_vault_line(counts: VaultCounts) -> str:
    return (f"> **Vault:** {counts.total_atoms} atoms, {counts.active_atoms} active. "
            f"Obsidian-compatible with [[wikilinks]].")


# ---------------------------------------------------------------------------
# File updater (generic marker-based replacement)
# ---------------------------------------------------------------------------

def update_file(filepath: Path, sections: dict, dry_run: bool = False) -> UpdateResult:
    """Replace content between <!-- AUTO:name --> markers. Atomic write."""
    if not filepath.exists():
        return UpdateResult(str(filepath), [], list(sections.keys()), False)

    text = filepath.read_text(encoding="utf-8")
    original = text
    found = []
    missing = []

    for marker_name, new_content in sections.items():
        pattern = re.compile(
            rf'(<!-- AUTO:{re.escape(marker_name)} -->)\n.*?\n(<!-- /AUTO:{re.escape(marker_name)} -->)',
            re.DOTALL
        )
        if pattern.search(text):
            text = pattern.sub(rf'\1\n{new_content}\n\2', text)
            found.append(marker_name)
        else:
            missing.append(marker_name)
            log.warning("Marker AUTO:%s not found in %s", marker_name, filepath.name)

    changed = text != original
    if changed and not dry_run:
        tmp = filepath.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        # Path.rename cannot replace an existing destination on Windows.
        # Path.replace provides the intended atomic overwrite semantics.
        tmp.replace(filepath)

    return UpdateResult(str(filepath), found, missing, changed)


def update_frontmatter_date(filepath: Path, dry_run: bool = False):
    """Update the 'updated' field in YAML frontmatter to today."""
    if not filepath.exists():
        return
    text = filepath.read_text(encoding="utf-8")
    today_str = date.today().isoformat()
    new_text = re.sub(r'(updated:\s*)\S+', rf'\g<1>{today_str}', text, count=1)
    if new_text != text and not dry_run:
        tmp = filepath.with_suffix(".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(filepath)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def refresh_all(dry_run: bool = False) -> dict:
    """Collect -> render -> update. Extend this for your domain."""
    report = {"files": [], "errors": []}

    vault = collect_vault_counts()
    if vault is None:
        report["errors"].append("manifest.json: not found or parse error")

    # Update MEMORY.md vault counts
    if vault and MEMORY_MD.exists():
        result = update_file(MEMORY_MD,
                             {"vault_count": render_vault_line(vault)}, dry_run)
        report["files"].append(result)

    # Update MOC files: add your own sections here.
    # Example:
    #   deadline = compute_countdown(date(2026, 5, 6))
    #   update_file(MOC_DIR / "project.md",
    #               {"deadline": f"## Deadline ({deadline})"}, dry_run)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cortex MOC Auto-Refresher")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show changes without writing")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    report = refresh_all(dry_run=args.dry_run)

    updated = [r for r in report["files"] if r.changed]
    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"MOC Refresh ({mode}): {len(updated)}/{len(report['files'])} files updated")
    for r in report["files"]:
        status = "UPDATED" if r.changed else "unchanged"
        found = ", ".join(r.markers_found) if r.markers_found else "none"
        miss = ", ".join(r.markers_missing) if r.markers_missing else "none"
        print(f"  {Path(r.path).name}: {status} (found: {found}; missing: {miss})")
    if report["errors"]:
        print(f"Errors: {'; '.join(report['errors'])}")


if __name__ == "__main__":
    main()
