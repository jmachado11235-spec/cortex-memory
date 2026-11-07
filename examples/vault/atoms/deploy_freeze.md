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
If within the freeze window, queue it for after the release.

Related: [[rules/test_before_deploy]]
