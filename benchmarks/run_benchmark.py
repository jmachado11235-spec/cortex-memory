#!/usr/bin/env python3
"""
Cortex Benchmark -- Generates a synthetic vault and measures retrieval performance.

Creates 500 atoms across 5 projects with realistic metadata, runs 100 queries,
and reports latency, precision, and temporal layer distribution.

Usage:
    python3 benchmarks/run_benchmark.py
"""
import json
import os
import random
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BENCH_VAULT = Path(__file__).resolve().parent / "_bench_vault"
PROJECTS = ["backend", "frontend", "infra", "research", "ops"]
TYPES = ["rule", "insight", "incident", "decision", "feedback", "reference", "project"]
TAG_POOL = ["deploy", "risk", "bug", "api", "auth", "database", "cache", "test",
            "migration", "performance", "security", "logging", "config", "ci"]

# 100 queries with expected top-result keywords (for precision measurement)
QUERIES = [
    ("deploy risk", ["deploy", "risk"]),
    ("database migration", ["database", "migration"]),
    ("api auth", ["api", "auth"]),
    ("cache performance", ["cache", "performance"]),
    ("security bug", ["security", "bug"]),
    ("logging config", ["logging", "config"]),
    ("test ci", ["test", "ci"]),
    ("deploy database", ["deploy", "database"]),
    ("auth security", ["auth", "security"]),
    ("performance cache", ["performance", "cache"]),
]


def generate_vault():
    """Generate 500 synthetic atoms with realistic temporal distribution."""
    if BENCH_VAULT.exists():
        shutil.rmtree(BENCH_VAULT)

    for d in ["atoms", "rules", "feedback", "insights", "index", "layers"]:
        (BENCH_VAULT / d).mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    atoms = []
    for i in range(500):
        project = random.choice(PROJECTS)
        atom_type = random.choice(TYPES)
        tags = random.sample(TAG_POOL, k=random.randint(1, 4))
        # Temporal distribution: 10% hot, 20% warm, 70% cold
        r = random.random()
        if r < 0.10:
            days_ago = random.randint(0, 2)
        elif r < 0.30:
            days_ago = random.randint(3, 7)
        else:
            days_ago = random.randint(8, 180)
        updated = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        created = (now - timedelta(days=days_ago + random.randint(0, 30))).strftime("%Y-%m-%d")

        name = f"{random.choice(tags).title()} {random.choice(['fix', 'finding', 'decision', 'rule', 'update', 'analysis'])} {i}"
        slug = name.lower().replace(" ", "_")

        dir_map = {"rule": "rules", "feedback": "feedback", "insight": "insights"}
        target_dir = dir_map.get(atom_type, "atoms")

        content = f"""---
id: {created.replace('-', '')}_{slug[:40]}
name: {name}
type: {atom_type}
project: {project}
status: active
created: {created}
updated: {updated}
tags: [{', '.join(tags)}]
links: []
description: Synthetic atom for benchmarking
---

# {name}

This is a synthetic atom for benchmarking Cortex retrieval.
Project: {project}. Tags: {', '.join(tags)}.
"""
        filepath = BENCH_VAULT / target_dir / f"{slug[:50]}.md"
        filepath.write_text(content, encoding="utf-8")
        atoms.append({"name": name, "tags": tags, "project": project, "updated": updated})

    return atoms


def build_indexes():
    os.environ["CORTEX_VAULT"] = str(BENCH_VAULT)
    # Force config reload
    import cortex.config as cfg
    cfg._config_cache = None

    from cortex.index_builder import scan_vault, build_indexes, write_indexes
    atoms = scan_vault()
    indexes = build_indexes(atoms)
    write_indexes(indexes, atoms)
    return len(atoms)


def run_queries():
    os.environ["CORTEX_VAULT"] = str(BENCH_VAULT)
    import cortex.config as cfg
    cfg._config_cache = None

    from cortex.smart_loader import load_manifest, search_keywords, temporal_layer

    manifest = load_manifest()
    results_data = []

    # Expand to 100 queries by combining tags and projects
    all_queries = []
    for base_q, expected in QUERIES:
        all_queries.append((base_q, expected))
    for tag in TAG_POOL:
        all_queries.append((tag, [tag]))
    for t1 in TAG_POOL:
        for t2 in TAG_POOL:
            if t1 != t2:
                all_queries.append((f"{t1} {t2}", [t1, t2]))
            if len(all_queries) >= 100:
                break
        if len(all_queries) >= 100:
            break
    all_queries = all_queries[:100]

    latencies = []
    layer_counts = {"hot": 0, "warm": 0, "cold": 0}
    precision_hits = 0
    total_scored = 0

    for query_str, expected_tags in all_queries:
        t0 = time.monotonic()
        results = search_keywords(manifest, [query_str], top_n=5)
        dt = (time.monotonic() - t0) * 1000
        latencies.append(dt)

        if results:
            top = results[0]
            layer = top.get("_layer", "cold")
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

            # Precision: does top result contain at least one expected tag?
            top_tags = [t.lower() for t in top.get("tags", [])]
            top_name = top.get("name", "").lower()
            top_project = top.get("project", "").lower()
            hit = any(t in top_tags or t in top_name or t == top_project for t in expected_tags)
            if hit:
                precision_hits += 1
            total_scored += 1

    return {
        "n_queries": len(all_queries),
        "n_atoms": len(manifest),
        "latencies": latencies,
        "layer_counts": layer_counts,
        "precision_hits": precision_hits,
        "total_scored": total_scored,
    }


def print_results(data):
    lats = data["latencies"]
    lats_sorted = sorted(lats)
    n = len(lats)

    print("=" * 60)
    print("CORTEX BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Vault size:    {data['n_atoms']} atoms")
    print(f"Queries run:   {data['n_queries']}")
    print()
    print("LATENCY (keyword search only, excludes manifest load)")
    print(f"  Mean:    {sum(lats)/n:.2f}ms")
    print(f"  Median:  {lats_sorted[n//2]:.2f}ms")
    print(f"  P95:     {lats_sorted[int(n*0.95)]:.2f}ms")
    print(f"  P99:     {lats_sorted[int(n*0.99)]:.2f}ms")
    print(f"  Min:     {min(lats):.2f}ms")
    print(f"  Max:     {max(lats):.2f}ms")
    print()
    total_layer = sum(data["layer_counts"].values())
    if total_layer > 0:
        print("TOP RESULT TEMPORAL LAYER")
        for layer in ["hot", "warm", "cold"]:
            cnt = data["layer_counts"].get(layer, 0)
            pct = cnt / total_layer * 100
            print(f"  {layer:5s}  {cnt:3d} ({pct:.0f}%)")
    print()
    if data["total_scored"] > 0:
        prec = data["precision_hits"] / data["total_scored"] * 100
        print(f"PRECISION (top-1 contains expected tag)")
        print(f"  {data['precision_hits']}/{data['total_scored']} = {prec:.1f}%")
    print()
    print("=" * 60)


def main():
    print("Generating 500-atom synthetic vault...")
    generate_vault()

    print("Building indexes...")
    n = build_indexes()
    print(f"Indexed {n} atoms.")

    print("Running 100 queries...\n")
    data = run_queries()
    print_results(data)

    # Cleanup
    shutil.rmtree(BENCH_VAULT)
    print("Benchmark vault cleaned up.")


if __name__ == "__main__":
    random.seed(42)
    main()
