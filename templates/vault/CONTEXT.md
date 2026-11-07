---
name: Vault router
type: rule
project: meta
status: active
created: 2026-11-06
updated: 2026-11-06
tags: [routing]
description: "Which project a question belongs to and where its current state lives."
---

# Router

| If the question is about | Project | Current state |
|---|---|---|
| (describe the topic) | `example` | [state/projects/example.md](state/projects/example.md) |

Add one row per project. Keep the project names identical to `[projects].names` in `cortex.toml`.
