---
name: ledger
description: Show where Claude Code tokens are going - per week/day/session totals, context size per turn, turns per prompt, biggest tool outputs, and what the tokenwise handlers did. Use when the user asks about token usage, session limits, "why am I hitting the limit", or wants the weekly report.
---
```
{{TOKENWISE}}/tokenwise/ledger.py report --week          # default; also --today, --days N, --session <id prefix>
{{TOKENWISE}}/tokenwise/ledger.py actions --days 7       # every handler action with its estimate
```
- The report ingests new transcript lines first (fast, incremental). Cost is a LIST-PRICE PROXY; the weekly-limit % is only in `/usage`.
- Read it as: cache_read total = (context per turn) × (turns). Large-context turns (>400K) carry most of it. Fewer turns per prompt and smaller context per turn are the two levers.
- Summarize for the user in ≤6 lines: totals, avg context/turn, the >400K share, turns/prompt, top tool, handler counts. Do not paste the whole table unless asked.
