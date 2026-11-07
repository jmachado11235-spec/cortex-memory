---
id: 20260401_test_before_deploy
name: Test before deploy
type: rule
project: ops
status: active
created: 2026-04-01
updated: 2026-04-10
tags: [deploy, risk, ops]
links: []
---

# Test before deploy

Every deployment must pass the full test suite before going live.

**Why:** A deploy without tests in March caused 4 hours of downtime.

**How to apply:** Run `make test` before every `make deploy`. CI enforces
this, but local deploys must follow the same rule.

See also: [[atoms/deploy_freeze]]
