---
id: vault_protocol
name: Vault recall and write protocol
type: rule
project: meta
status: active
created: 2026-11-06
updated: 2026-11-06
tags: [protocol, routing]
links: []
description: "How an AI agent recalls from and writes to this vault. Read this before touching any note."
---

# Vault protocol — read this first

This folder is the agent's long-term memory. **Never bulk-read `atoms/`.** Load context
cheapest first and stop as soon as you have what you need.

## Recall (before answering)

1. **Route:** read `CONTEXT.md` to find the right project.
2. **Current state:** open the project's entry in `state/INDEX.md`. That file is what is
   true *now*. It wins over any older note.
3. **Search** instead of opening files (run from the Cortex repo folder):
   ```
   CORTEX_VAULT=<path>/my-vault python -m cortex.smart_loader "words you remember" --project <name> --brief --top 5
   ```
   Search covers note bodies, so query the detail you remember (a customer name, an amount,
   an order number). Only `active` notes come back by default; `--all-status` includes history.
4. Open only the top 1–3 notes you actually need.

## Write (after work that changed something)

- **One fact, one place.** When something is resolved, decided or changed, **replace the old
  claim** in the project's `state/projects/<name>.md`. Do not just append a newer line under
  the old one: an agent reading tomorrow must not find "still open" anywhere active.
- **Supersede, don't accumulate.** When a note is replaced, set the old one's
  `status: superseded` and add `superseded_by: <path of the new note>`.
- **Keep state files short: 3,000 words at most.** A state file says what is true now.
  Dated history, build logs and long procedures go in a `reference/` note, listed at the
  top of the state file under a "Detail documents" heading with one line saying when to
  open it. When a save would push a state file over the limit, move the detail out
  word for word instead of stacking another dated section. `cortex.health` names any
  state file over the limit.
- **Resolved stays resolved.** A closed issue gets a dated line in the state file
  ("Resolved 2026-11-06: ..."). Never raise it again unless new evidence appears; if it does,
  say what is new.
- New event notes go in `atoms/` with `python -m cortex.atom_writer --name ... --type ... --project ... --description "..." --body "..."`.
  Put the conclusion in the first sentence. Keep it under 200 words. The description is the
  one line search shows: 160 characters at most (a longer one is rejected).
- Link every new note from its project's map in `_moc/`.
- Always rebuild after writing: `python -m cortex.index_builder`. A note is not findable until then.
  Then `python -m cortex.health` should report 0 errors.
- If the Stop hook gives you a checkpoint token, finish with the exact
  `python -m cortex.checkpoint acknowledge --token ... --files ...` command it shows
  (or `--no-change "reason"` if nothing durable happened). Never write filler notes.
- **History is in git, if the vault is a git repository.** Each acknowledged turn is then
  committed and the receipt prints the commit. To see or recover an earlier version of a
  note: `git -C <path>/my-vault log -p -- <note path>`. Never reset or rewrite that history.
  `git history NOT updated` in a receipt means the save worked and the snapshot did not; say so.
- If the user asked for a review only / no changes, write nothing.

## Folder map

| Folder | Holds | Load when |
|---|---|---|
| `CONTEXT.md`, `state/INDEX.md` | routing and current state | every task |
| `state/projects/` | one current-state file per project | working on that project |
| `_moc/` | one map per project linking all its notes | finding a project's notes |
| `atoms/` | short dated events and decisions | only top search matches |
| `incidents/` | what broke and what fixed it | only top search matches |
| `reference/` | long specs, procedures | only when the detail is needed |
