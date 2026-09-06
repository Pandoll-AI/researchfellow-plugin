---
description: Start or resume a ResearchFellow study
argument-hint: "<research idea | status | next | step N | doctor>"
---

Invoke the researchfellow skill with the user's arguments: $ARGUMENTS. If no arguments, follow the skill's initialization routing.

## doctor

`/researchfellow doctor` — run `scripts/env_check.py` (add `--project-dir research` when a project exists) and print the JSON.

- `modes.synthetic` / `modes.real` say which analysis modes this machine can run.
- `next_action` is the one-line recommendation (synthetic demo, or `pip install -r requirements.txt` before real data).
- `mcp.reachable` is whether the remote MCP server answered; unreachable does not block local work.
