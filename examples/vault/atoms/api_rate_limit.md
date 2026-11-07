---
id: 20260405_api_rate_limit_finding
name: API rate limits affect batch operations
type: insight
project: research
status: active
created: 2026-04-05
updated: 2026-04-05
tags: [api, performance, research]
links: []
---

# API rate limits affect batch operations

When processing more than 100 items per minute, the external API returns
429 Too Many Requests. Current batch size of 50 with 2s delay handles this,
but any increase in throughput will require implementing exponential backoff.

Measured ceiling: 120 requests/minute before consistent 429s.
Safe operating range: 80 requests/minute (67% of ceiling).
