#!/usr/bin/env python3
"""
Cortex Atom Writer -- Creates new memory atoms with standardized frontmatter.

Each atom is a markdown file with YAML frontmatter containing structured
metadata (id, name, type, project, status, dates, tags, links).

Usage:
    python -m cortex.atom_writer --name "Test before deploy" --type rule \
        --project ops --tags "deploy,risk" --body "Always run tests first."
"""
import argparse
import re
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

from cortex.config import get_vault_path, get_valid_projects, get_valid_types, get_valid_statuses


VAULT = get_vault_path()
VALID_TYPES = get_valid_types()
VALID_STATUSES = get_valid_statuses()


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '_', text)
    return text[:60]


def derive_description(body: str, max_len: int = 160) -> str:
    """Derive a one-line description from the first sentence of the body."""
    text = " ".join(body.split())
    if not text:
        return ""
    m = re.match(r'(.{20,}?[.!?])(?:\s|$)', text)
    desc = m.group(1) if m else text
    if len(desc) > max_len:
        desc = desc[:max_len - 1].rstrip() + "…"
    return desc.replace('"', "'")


def validate_project(project: str, allow_personal: bool = False) -> None:
    """Reject unknown projects and make the personal fallback deliberate."""
    valid_projects = get_valid_projects()
    if project not in valid_projects:
        raise ValueError(
            f"Unknown project {project!r}; choose one of: {', '.join(valid_projects)}"
        )
    if project == "personal" and not allow_personal:
        raise ValueError(
            "--project personal is a last resort. Choose the owning project, "
            "or add --allow-personal to confirm this is genuinely unowned."
        )


def create_atom(name: str, atom_type: str, project: str, tags: list[str],
                body: str, directory: str = None, status: str = "active",
                pnl_impact: float = None, confidence: float = None,
                description: str = None, allow_personal: bool = False) -> Path:
    validate_project(project, allow_personal)
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y%m%d")
    slug = slugify(name)
    atom_id = f"{date_prefix}_{slug}"

    if directory:
        target_dir = (VAULT / directory).resolve()
        if not target_dir.is_relative_to(VAULT.resolve()):
            raise ValueError(f"Directory must be within vault: {directory}")
    else:
        dir_map = {
            "rule": "rules", "feedback": "feedback", "insight": "insights",
            "incident": "incidents", "person": "people", "reference": "reference",
            "user": "user",
        }
        target_dir = VAULT / dir_map.get(atom_type, "atoms")

    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}.md"
    filepath = target_dir / filename

    if filepath.exists():
        counter = 2
        while filepath.exists():
            filepath = target_dir / f"{slug}_{counter}.md"
            counter += 1

    fm_lines = [
        "---",
        f"id: {atom_id}",
        f"name: {name}",
        f"type: {atom_type}",
        f"project: {project}",
        f"status: {status}",
        f"created: {now.strftime('%Y-%m-%d')}",
        f"updated: {now.strftime('%Y-%m-%d')}",
        f"tags: [{', '.join(tags)}]",
        "links: []",
    ]
    desc = (description or "").strip() or derive_description(body)
    if desc:
        fm_lines.append(f'description: "{desc}"')
    if pnl_impact is not None:
        fm_lines.append(f"pnl_impact: {pnl_impact}")
    if confidence is not None:
        fm_lines.append(f"confidence: {confidence}")
    fm_lines.append("---")

    content = "\n".join(fm_lines) + f"\n\n# {name}\n\n{body}\n"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def main():
    valid_projects = get_valid_projects()

    parser = argparse.ArgumentParser(description="Create a new Cortex atom")
    parser.add_argument("--name", required=True, help="Atom title")
    parser.add_argument("--type", required=True, choices=VALID_TYPES, help="Atom type")
    parser.add_argument("--project", required=True, choices=valid_projects,
                        help="Owning project slug (validated against vault cortex.toml)")
    parser.add_argument(
        "--allow-personal",
        action="store_true",
        help="Confirm that this genuinely fits no named project; required with --project personal",
    )
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--body", default="", help="Body text (or - for stdin)")
    parser.add_argument("--dir", default=None, help="Override target directory")
    parser.add_argument("--status", default="active", choices=VALID_STATUSES)
    parser.add_argument("--pnl", type=float, default=None, help="PnL impact")
    parser.add_argument("--confidence", type=float, default=None, help="Confidence 0-1")
    parser.add_argument("--description", default=None,
                        help="One-line summary for recall scoring (auto-derived from body if omitted)")

    args = parser.parse_args()
    try:
        validate_project(args.project, args.allow_personal)
    except ValueError as exc:
        parser.error(str(exc))
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    # 🔴 stdin se lee como UTF-8 a la fuerza. Sin esto, en Windows Python lo decodifica con
    # cp1252 y un cuerpo con acentos se guarda como mojibake («nÃºmero»): la nota queda
    # escrita, indexada y con hash correcto, y aun así ilegible. Pasó el 21/09/2026 en tres
    # atomos seguidos y no lo detectó nada, porque el frontmatter viene por argv y sale bien.
    if args.body == "-":
        body = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace").read()
    else:
        body = args.body

    path = create_atom(args.name, args.type, args.project, tags, body,
                       args.dir, args.status, args.pnl, args.confidence,
                       args.description, args.allow_personal)
    print(f"Created: {path.relative_to(VAULT)}")


if __name__ == "__main__":
    main()
