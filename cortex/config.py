"""
Cortex Configuration -- Loads settings from cortex.toml or environment.

Resolution order:
  1. CORTEX_VAULT environment variable (for vault path and vault config)
  2. cortex.toml in current directory
  3. cortex.toml beside the cortex package
  4. Built-in defaults

All domain-specific data (project names, directory mappings, tag keywords)
lives in cortex.toml, not in source code.
"""
import os
from pathlib import Path

_config_cache = None


def _parse_toml(path: Path) -> dict:
    """Parse a TOML file. Uses tomllib (3.11+) or a minimal fallback."""
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    # Minimal fallback for Python 3.10 and below.
    # Handles flat keys and simple arrays. Not a full TOML parser.
    config = {}
    current_section = None
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            config.setdefault(current_section, {})
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip inline comments (not inside quotes)
        if not val.startswith('"') and "#" in val:
            val = val[:val.index("#")].strip()
        # Parse value
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        elif val.startswith("[") and val.endswith("]"):
            items = val[1:-1].split(",")
            val = [v.strip().strip('"').strip("'") for v in items if v.strip()]
        elif val.lstrip("-").isdigit():
            val = int(val)
        elif val.lstrip("-").replace(".", "", 1).isdigit():
            val = float(val)
        if current_section:
            config[current_section][key] = val
        else:
            config[key] = val
    return config


def _find_config() -> dict:
    """Locate and parse cortex.toml."""
    # A selected vault owns its project taxonomy. Prefer its config so callers
    # launched from the Cortex source tree do not accidentally inherit the
    # repository's generic example project list.
    env_vault = os.environ.get("CORTEX_VAULT")
    if env_vault:
        vault_config = Path(env_vault).resolve() / "cortex.toml"
        if vault_config.exists():
            return _parse_toml(vault_config)
    # Check current directory
    cwd_config = Path.cwd() / "cortex.toml"
    if cwd_config.exists():
        return _selected_config(cwd_config)
    # Check vault root (parent of cortex/ package)
    vault_config = Path(__file__).resolve().parent.parent / "cortex.toml"
    if vault_config.exists():
        return _selected_config(vault_config)
    return {}


def _selected_config(path):
    cfg = _parse_toml(path)
    selected = cfg.get('vault', {}).get('path')
    if selected:
        root = (path.parent / selected).resolve()
        own = root / 'cortex.toml'
        if own.exists() and own != path:
            cfg = _parse_toml(own)
        cfg.setdefault('vault', {})['path'] = str(root)
    return cfg


def _load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        _config_cache = _find_config()
    return _config_cache


def get_vault_path() -> Path:
    """Return the vault root directory."""
    env = os.environ.get("CORTEX_VAULT")
    if env:
        return Path(env).resolve()
    cfg = _load_config()
    vault_section = cfg.get("vault", {})
    if isinstance(vault_section, dict) and "path" in vault_section:
        return Path(vault_section["path"]).resolve()
    # Default: parent of the cortex/ package directory
    return Path(__file__).resolve().parent.parent


def get_index_dir() -> Path:
    return get_vault_path() / "index"


def get_layers_dir() -> Path:
    return get_vault_path() / "layers"


def get_valid_projects() -> list:
    """Return list of valid project names for atom validation."""
    cfg = _load_config()
    projects = cfg.get("projects", {})
    if isinstance(projects, dict):
        return projects.get("names", ["default"])
    return ["default"]


def get_valid_types() -> list:
    """Atom types are part of the schema, not user config."""
    return [
        "rule", "insight", "incident", "project", "person", "reference",
        "decision", "lesson", "event", "loss", "win", "concept", "feedback", "user",
    ]


def get_valid_statuses() -> list:
    return ["active", "review", "archived", "superseded"]


def get_dir_to_project() -> dict:
    """Return directory-name to project-name mapping."""
    cfg = _load_config()
    dirs = cfg.get("directories", {})
    if isinstance(dirs, dict) and dirs:
        return dirs
    # Sensible defaults for common directory names
    return {
        "feedback": "meta",
        "rules": "meta",
        "reference": "meta",
        "_moc": "meta",
    }


def get_keyword_tags() -> dict:
    """Return keyword-to-tag mapping for auto-tagging."""
    cfg = _load_config()
    tags = cfg.get("tags", {})
    if isinstance(tags, dict) and tags:
        return tags
    return {
        "deploy": ["deploy", "commit", "push", "restart"],
        "risk": ["risk", "loss", "drawdown", "halt"],
        "bug": ["bug", "fix", "error", "broken"],
    }


def get_layer_thresholds() -> dict:
    """Return temporal layer thresholds in days."""
    cfg = _load_config()
    layers = cfg.get("layers", {})
    return {
        "hot": int(layers.get("hot", 2)),
        "warm": int(layers.get("warm", 7)),
    }


def get_skip_dirs() -> set:
    return {".obsidian", "engine", "cortex", "index", "layers",
            "__pycache__", ".git", "node_modules", "session-archive", "backups"}


def get_skip_files() -> set:
    return {"MEMORY.md"}
