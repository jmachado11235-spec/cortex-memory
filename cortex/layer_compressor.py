#!/usr/bin/env python3
"""
Cortex Layer Compressor -- Classifies atoms into hot/warm/cold temporal layers.

Temporal stratification is the core insight of Cortex: most agent queries are
about recent work. By classifying atoms into three layers based on their
last-updated date, retrieval can prioritize fresh context without scanning
the entire vault.

    Hot  (default: <=2 days):  Full detail, high priority
    Warm (default: 2-7 days):  Medium priority
    Cold (default: >7 days):   Low priority, background context

Thresholds are configurable in cortex.toml under [layers].

Usage:
    python -m cortex.layer_compressor           # build layers
    python -m cortex.layer_compressor --stats   # print layer stats
"""
import json
import sys
from datetime import datetime

from cortex.config import get_index_dir, get_layers_dir, get_layer_thresholds


INDEX_DIR = get_index_dir()
LAYERS_DIR = get_layers_dir()


def classify_layer(updated_str: str) -> str:
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


def build_layers():
    manifest_path = INDEX_DIR / "manifest.json"
    if not manifest_path.exists():
        print("No manifest. Run index_builder first.", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = {"hot": [], "warm": [], "cold": []}

    for atom in manifest:
        if atom.get("status") == "archived":
            continue
        layer = classify_layer(atom.get("updated", ""))
        entry = {
            "path": atom["path"],
            "name": atom["name"],
            "type": atom["type"],
            "project": atom["project"],
            "tags": atom.get("tags", []),
            "updated": atom.get("updated", ""),
            "lines": atom.get("lines", 0),
        }
        layers[layer].append(entry)

    for layer in layers.values():
        layer.sort(key=lambda x: x.get("updated", ""), reverse=True)

    LAYERS_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in layers.items():
        path = LAYERS_DIR / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return layers


def print_stats(layers: dict):
    print(f"\n{'='*50}")
    print("CORTEX TEMPORAL LAYERS")
    print(f"{'='*50}")
    for name in ["hot", "warm", "cold"]:
        data = layers[name]
        total_lines = sum(a.get("lines", 0) for a in data)
        print(f"\n{name.upper()} ({len(data)} atoms, {total_lines} lines):")
        for a in data[:10]:
            print(f"  [{a['project']:10s}] {a['name'][:50]}")
        if len(data) > 10:
            print(f"  ... and {len(data) - 10} more")


def main():
    layers = build_layers()
    print(f"Layers built: hot={len(layers['hot'])}, warm={len(layers['warm'])}, cold={len(layers['cold'])}")
    if "--stats" in sys.argv:
        print_stats(layers)


if __name__ == "__main__":
    main()
