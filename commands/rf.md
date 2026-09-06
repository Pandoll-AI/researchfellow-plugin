---
description: Start or resume a ResearchFellow study
argument-hint: "<research idea | status | next | step N | doctor>"
---

Invoke the researchfellow skill with the user's arguments: $ARGUMENTS. If no arguments, follow the skill's initialization routing.

## doctor

`/rf doctor` — run `scripts/env_check.py` (add `--project-dir research` when a project exists) and print the JSON.

프로젝트 상태를 읽거나 만들지 않는다.

- `modes.synthetic` / `modes.real` say which analysis modes this machine can run.
- `next_action` is the one-line recommendation (synthetic demo, or `pip install -r requirements.txt` before real data).
- `mcp.reachable` is whether the remote MCP server answered; unreachable does not block local work.
