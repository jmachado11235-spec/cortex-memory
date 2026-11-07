# Cortex — memory for AI agents, with verified saving

File-based long-term memory for an AI agent: plain markdown notes, keyword + full-text search,
and a save step that is checked, not assumed. Python standard library only.

This is a working version of [Cortex by Andres Hartmann](https://github.com/vocatus23/Cortex)
(MIT). The search engine is his. This version adds the parts that stop an agent from
forgetting what it already settled — the "we fixed that yesterday, why are you asking again?" problem.

## What this version adds

| Addition | What it does |
|---|---|
| Current-state files | One `state/projects/<name>.md` per project holds what is true *now*. It outranks older notes. Resolved issues move to a dated "Resolved" list instead of piling up beside the open ones. |
| Status filtering | Only `active` notes come back from search by default. Replaced notes are marked `superseded` and stop showing up. |
| Full-text body search | SQLite FTS5 over note bodies, stemmed and accent-folded (English and Spanish), so you can search for the detail you remember. |
| Verified per-turn saving | With Claude Code, the Stop hook asks the agent to save at the end of each turn. The save only counts when the saved files' SHA-256 hashes match both indexes (`cortex/checkpoint.py`). |
| Review-only detection | "Look but don't change anything" requests write nothing to memory. |
| Raw transcript archive | One recovery copy of each session transcript in `my-vault/session-archive/`. |
| Health check | `python -m cortex.health` finds broken links, notes missing from the index, and unmapped notes. |

## Set up with Claude Code (about 10 minutes)

Needs Python 3.10+ and Claude Code. Commands run from the repo folder.

```bash
git clone https://github.com/jmachado11235-spec/cortex-memory.git
cd cortex-memory
cp -r templates/vault my-vault        # your private memory; git ignores it
python -m cortex.index_builder        # builds the indexes
python hooks/install_claude_hooks.py  # adds the hooks to ~/.claude/settings.json
```

Restart Claude Code and approve the new hooks in `/hooks`. Then edit `my-vault/cortex.toml`
(your project names), `my-vault/CONTEXT.md` and `my-vault/state/` for your own projects.
`my-vault/CLAUDE.md` is the protocol the agent follows; every new session is told to read it.

What happens after that:

- **Session start:** the agent is pointed at the protocol and the current-state index.
- **Each turn:** it searches memory for what it needs instead of guessing.
- **End of each turn:** the Stop hook hands it a one-time token. The agent updates the state
  file (or writes a short note), rebuilds the index, and runs
  `python -m cortex.checkpoint acknowledge --token ... --files ...`. If nothing worth keeping
  happened it says so with `--no-change "reason"`. Unverified saves stay listed in
  `python -m cortex.checkpoint pending` until fixed.

## Using it with another bot (Grok, ChatGPT, a custom bot)

Cortex is files plus three commands, so any bot that can run a command or call a function
can use it. The hooks only automate it for Claude Code. For another bot, wire in:

1. **Before each reply:** `python -m cortex.smart_loader "<the user's message>" --automatic --brief --top 3`
   and the project's `state/projects/<name>.md`, both added to the bot's context.
2. **After something is resolved or decided:** edit the state file (move the item to
   "Resolved" with the date), then `python -m cortex.index_builder`.
3. **System prompt:** the rules in `templates/vault/CLAUDE.md`.

A chat-only bot with no tool access cannot do this by itself; it needs a small wrapper
script that runs the commands around each message.

## The Problem

LLM agents need persistent memory, but current approaches have friction:

- **Full context loading** wastes tokens on irrelevant history. A vault of 500 atoms doesn't fit in a context window, and most of it isn't relevant to the current query.
- **Vector/embedding search** requires API calls, a vector database, and still returns results by semantic similarity rather than operational relevance. An atom from 30 days ago can outrank one from 2 hours ago.
- **Recency matters.** In fast-paced domains (operations, trading, engineering), 80% of queries are about the last 48 hours. Memory systems should reflect this.

Cortex solves these with three ideas: **atoms** (structured markdown), **multi-dimensional indexes** (JSON, not vectors), and **temporal stratification** (hot/warm/cold layers).

## How It Works

```
                    Your Vault
                    (markdown files with YAML frontmatter)
                         |
                         v
              +-----------------------+
              |    Index Builder      |   Scans vault, parses frontmatter,
              |                       |   builds 5 JSON indexes + 1 FTS shard
              +-----------------------+
                         |
          +--------------+--------------+--------------+
          |              |              |              |
          v              v              v              v
    manifest.json   by_project.json  graph.json     fts.db
    by_type.json    by_tag.json                  (SQLite FTS5:
          |              |              |         full note bodies)
          +--------------+--------------+--------------+
                         |
                         v
              +-----------------------+
              |     Smart Loader      |   Keyword scoring + temporal bonus
              |                       |   + full-text body matching
              |                       |   Returns top-N ranked atoms
              +-----------------------+
                         |
                         v
                  Relevant context
                  (loaded into LLM)
```

## Scoring Algorithm

When you search for keywords, each atom is scored:

| Match Type | Points | Example |
|------------|--------|---------|
| Name match | +10 per keyword | "deploy" in atom name "Deploy freeze" |
| Tag match | +8 per keyword | "risk" in tags ["deploy", "risk"] |
| Project match | +5 per keyword | "ops" matches project field |
| Description match | +4 per keyword | keyword found in description |
| Body match | +3 per keyword | keyword found in the note's text (FTS5) |
| Path match | +3 per keyword | keyword found in file path |
| Body relevance | +0 to +1.5 | bm25, ranks body hits against each other |

Body matching is stemmed and accent-folded, so `payments` finds "payment" and
`amortizacion` finds "amortizacion". Curated fields still lead: an average body
mention scores below a hand-written description. Pass `--no-fts` for the
metadata-only behaviour, and note that search degrades to exactly that if
`fts.db` is missing.

**Bonuses and penalties:**

| Modifier | Effect | Rationale |
|----------|--------|-----------|
| All keywords match | x1.5 | Reward precision over recall |
| Hot atom (<=2 days) | +2.0 | Recent context wins ties |
| Warm atom (2-7 days) | +1.0 | Settled decisions still relevant |
| Cold atom (>7 days) | +0.0 | No bonus, but still searchable |
| Archived | x0.3 | De-prioritize, don't hide |
| Superseded | x0.5 | Partially relevant |

**Example:** Searching for "deploy risk"

```
Atom A: name="Deploy freeze during release windows", tags=[deploy, risk]
  deploy: +10 (name) +8 (tag) = 18
  risk:   +8 (tag) = 8
  Both keywords match: (18 + 8) * 1.5 = 39.0
  Updated 1 day ago (hot): +2.0
  Final score: 41.0

Atom B: name="API rate limits", tags=[api, performance]
  deploy: no match
  risk:   no match
  Final score: 0.0 (not returned)
```

## Try the search on the example vault

```bash
CORTEX_VAULT=examples/vault python -m cortex.index_builder
CORTEX_VAULT=examples/vault python -m cortex.smart_loader "deploy risk" --brief
```

## Data Model

Every atom is a markdown file with YAML frontmatter:

```yaml
---
id: 20260412_deploy_freeze_during_release
name: Deploy freeze during release windows
type: decision
project: ops
status: active
created: 2026-04-12
updated: 2026-04-12
tags: [deploy, risk, decision]
links: []
---

# Deploy freeze during release windows

No deploys allowed 24 hours before and after a release cut.

**Why:** Concurrent deploys during the v2.3 release caused a merge conflict
that broke the staging environment for 6 hours.

**How to apply:** Check the release calendar before scheduling any deploy.
```

### Frontmatter Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | yes | `YYYYMMDD_slugified_name` (unique) |
| name | string | yes | Human-readable title |
| type | enum | yes | rule, insight, incident, project, person, reference, decision, lesson, event, loss, win, concept, feedback, user |
| project | string | yes | Project name (configured in cortex.toml) |
| status | enum | yes | active, review, archived, superseded |
| created | date | yes | YYYY-MM-DD |
| updated | date | yes | YYYY-MM-DD |
| tags | list | yes | Lowercase keywords |
| links | list | yes | Reserved for wikilink targets |
| description | string | no | First line of body, max 150 chars |
| pnl_impact | float | no | Domain-specific numeric impact |
| confidence | float | no | 0-1 confidence level |

## Temporal Layers

Cortex classifies every atom into three temporal layers based on `updated` date:

```
  Hot   (<=2 days)    29 atoms    Full detail, high priority
  -------- threshold --------
  Warm  (2-7 days)    69 atoms    Medium priority
  -------- threshold --------
  Cold  (>7 days)    372 atoms    Low priority, background

  Total: 470 atoms
  Hot + Warm = 21% of vault, covers the majority of routine queries
```

The thresholds are configurable in `cortex.toml`:

```toml
[layers]
hot = 2    # days
warm = 7   # days
```

The `layer_compressor` module builds `layers/hot.json`, `layers/warm.json`, and `layers/cold.json` for fast layer-specific retrieval.

## MOC Auto-Refresh

Map-of-Content (MOC) files serve as entry points to each project domain. The `moc_refresher` module keeps them current by replacing content between marker pairs:

```markdown
<!-- AUTO:section_name -->
...this content is replaced on each run...
<!-- /AUTO:section_name -->
```

This is a non-destructive update: only content between markers is touched. Manual edits outside markers are preserved. Writes are atomic (temp file + rename).

Built-in collectors update vault atom counts. The module is designed to be extended with custom collectors for your domain (database queries, API calls, computed metrics).

## Modules

| Module | Lines | Purpose |
|--------|-------|---------|
| `smart_loader.py` | 343 | Query engine: keyword scoring, temporal layers, body search |
| `migrate_existing.py` | 277 | Standardize frontmatter on existing files |
| `moc_refresher.py` | 236 | Marker-based MOC auto-update engine |
| `index_builder.py` | 215 | Vault scanner, frontmatter parser, 5 JSON indexes |
| `fts_index.py` | 272 | SQLite FTS5 full-text index over note bodies |
| `auto_linker.py` | 181 | Wikilink discovery, orphan/hub analysis |
| `config.py` | 174 | Configuration loader (cortex.toml) |
| `atom_writer.py` | 110 | Create atoms with standardized frontmatter |
| `layer_compressor.py` | 102 | Temporal layer classification (hot/warm/cold) |

Total: ~1,900 lines of Python. No external dependencies.

## Indexes

The index builder produces five JSON files and one SQLite database:

| Index | Keys | Purpose |
|-------|------|---------|
| `manifest.json` | flat array | All atom metadata (the master list) |
| `by_project.json` | project names | Atoms grouped by project |
| `by_type.json` | type names | Atoms grouped by type |
| `by_tag.json` | tag names | Atoms grouped by tag |
| `graph.json` | source paths | Directed link graph from wikilinks |
| `fts.db` | full-text | Every note's body, searchable (SQLite FTS5) |

The JSON indexes are human-readable and carry frontmatter only. `fts.db` is the
one binary: a local SQLite file holding the note bodies so a fact written inside
a note is findable, not just one described in its frontmatter. It uses Python's
bundled `sqlite3` -- still no external dependencies, and still no server. It is
derived data: delete it and the next `index_builder` run rebuilds it.

## Configuration

Copy `cortex.toml` to your vault root and customize:

```toml
[vault]
path = "/path/to/your/vault"    # or set CORTEX_VAULT env var

[projects]
names = ["ops", "research", "personal"]

[directories]
# Map directory names to project names for auto-inference
feedback = "meta"
rules = "meta"
research = "research"

[tags]
# Keyword-to-tag mapping for auto-tagging
deploy = ["deploy", "commit", "push"]
risk = ["risk", "loss", "halt"]

[layers]
hot = 2     # days
warm = 7    # days
```

Resolution order: `CORTEX_VAULT` env var > `cortex.toml` in cwd > `cortex.toml` in vault root > defaults.

## Cross-Reference Analysis

The auto-linker discovers connections between atoms:

```bash
CORTEX_VAULT=my-vault python3 -m cortex.auto_linker --report
```

Outputs orphans (atoms with no links in or out) and hubs (atoms with the most connections). Useful for identifying knowledge silos and potential cross-references.

## Design Principles

1. **Zero external dependencies.** Stdlib Python only. Runs anywhere Python runs.
2. **Deterministic scoring.** No embeddings, no API calls, no randomness. Same query returns same results.
3. **File-based atoms.** Plain markdown with YAML frontmatter. Works with Obsidian, any text editor, and git.
4. **Temporal relevance over semantic similarity.** Recent atoms win ties because in operational contexts, freshness correlates with relevance.
5. **Human-readable everything.** Indexes are JSON. Atoms are markdown. Config is TOML. No binary formats.

## Requirements

- Python 3.10+ (3.11+ for native TOML parsing; 3.10 uses built-in fallback)
- No pip install required

## License

MIT. Original engine copyright (c) 2026 Andres Hartmann; additions in this version copyright (c) 2026 Juvenal J. Machado. See `LICENSE`.
