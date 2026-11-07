"""Entry point for python3 -m cortex."""
print("""Cortex v0.2.0 -- Hierarchical-temporal retrieval for AI agent memory

Commands:
  python3 -m cortex.index_builder          Build JSON indexes from vault
  python3 -m cortex.smart_loader "query"   Search atoms by keywords
  python3 -m cortex.smart_loader --summary Vault boot summary
  python3 -m cortex.atom_writer --help     Create a new atom
  python3 -m cortex.layer_compressor       Build temporal layers
  python3 -m cortex.auto_linker --report   Cross-reference analysis
  python3 -m cortex.moc_refresher          Refresh MOC files
  python3 -m cortex.migrate_existing       Standardize frontmatter

Set vault path:
  export CORTEX_VAULT=/path/to/vault
  Or place cortex.toml in your vault root.

Docs: https://github.com/vocatus23/Cortex""")
