# Desk Interface — local interactive HTML contract

The Desk is a one-shot local web page (S1 PICO interview / 13-step dashboard)
served by `scripts/desk_server.py` on 127.0.0.1. It is an **enhancement, not a
dependency**: every Desk view has an unchanged chat fallback in
`entry-points.md`, and the flow below defines exactly when to fall back.
The server renders and collects only — `state.json` and `audit.jsonl` writes
always remain the host LLM's job.

The Korean verb-form step labels are single-sourced in `state_tool.py` `STEP_LABELS_KO`; because `desk_server.py` only renders, the host assembling the dashboard payload must take `next_action.label` from that constant.

## Launch flow (host LLM)

1. **Probe first** — run once per session before ever starting a server:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/desk_server.py --probe-headless
   ```
   `{"likely_headless": true}` → skip the Desk entirely this session and use the
   chat procedure. Signals: any `SSH_CONNECTION|SSH_TTY|SSH_CLIENT` env; on
   Linux only, missing `DISPLAY`/`WAYLAND_DISPLAY` (macOS/Windows never set it).
2. Write the payload to `research/.system/desk/payload-<session>.json` (schema below).
   Pick `<session>`: short id you choose (e.g. `s1-01`) — all file names derive
   from it.
3. Launch **in the background** (the script blocks until submit/timeout — the
   harness notifies you when it exits; do NOT poll):
   ```
   python3 .../desk_server.py --view {s1_interview|dashboard} \
       --payload research/.system/desk/payload-<session>.json \
       --project-dir research --session-id <session>
   ```
   The server opens the browser itself and prints `{url, port, session}`.
4. On process exit, read `research/.system/desk/session-<session>.json`:

   | exit / status | meaning | you do |
   |---|---|---|
   | 0 / `submitted` | answers written | read `answers-<session>.json`, continue |
   | 3 / `timeout` | user never submitted (default 8 min) | chat fallback, mention nothing technical |
   | 4 / `fallback_requested` | user clicked "그냥 채팅으로 할게요" | chat fallback immediately |
   | 2 / `no_port` · 1 / input error | environment problem | chat fallback, silently |

5. `answers-<session>.json` shape: `{view, submitted_at, answers, _phi}`.
   `answers` free-text fields are ALREADY masked (`[MASKED:rule]`) — if
   `_phi.finding_count > 0`, append a `PHI_DETECTED` audit event (no values).

## Payload schema

Common: `free_text_keys: string[]` — answer keys the server must mask through
phi_detect before writing to disk (fail-closed: engine unavailable → withheld).
`view`/`session` are set by the server from CLI args; don't include them.

### view = `s1_interview`

```json
{
  "free_text_seed": "<유저가 말한 원문 — textarea 시드>",
  "pico_prefill": {
    "population":  {"value": "…", "confidence": "stated|low"},
    "exposure":    {"value": "…"}, "comparator": {"value": "…"},
    "outcome":     {"value": "…"}, "timeframe": {"value": "…"},
    "data_source": {"value": "…"}
  },
  "candidates": [ {"label": "방향 제안 한 줄", "pico": {"population": "…", "exposure": "…", "outcome": "…"}} ],
  "free_text_keys": ["idea_text"]
}
```

- Fill `pico_prefill` only with what YOU already extracted (clear/rough) — empty
  fields render as highlighted blanks. **Never expose clarity labels.**
- `candidates` (0–3) only for the vague path — chips that fill the card client-side.
- Answers: `{idea_text, pico: {<field>: {value, confidence}}, selected_candidate}`.
  `confidence: "low"` = the "잘 모르겠어요" toggle → keep those fields marked
  uncertain in idea.json, exactly like the chat path. Skip the chat restate-and-
  confirm step (the form WAS the confirmation) — go straight to the Step 1 summary.

### view = `dashboard`

```json
{
  "project_name": "…", "research_card": null, "entry_label": "…", "completed_count": 4,
  "steps": [ {"n": 1, "name": "PICO Structuring", "status": "completed|in_progress|pending|imported|invalidated|skipped|blocked", "blocked_reason": "…"} ],
  "next_action": {"label": "…", "step": 6},
  "blockers": ["…"],
  "gates": [ {"id": "gate.qc", "type": "hard|soft", "status": "pending|approved|rejected"} ],
  "compliance": {"items": [ {"id": "irb_approval", "label": "…", "checked": false} ]},
  "rehearsal": {"active": false},
  "recap": "지난 세션 이후 …",           // optional — participation copy
  "micro_task": "오늘은 …만 15분에",      // optional
  "milestone": "Planning Mode 완주!",    // optional
  "free_text_keys": []
}
```

- `research_card`가 있으면 헤더 개막 문장으로 렌더하고, 없거나 `null`이면 생략합니다.
- Build `steps`/`blockers` from a FRESH `state_tool.py can-enter`/`validate`
  run, not a stale snapshot.
- Answers: `{action: "continue|start_rehearsal|new_entry|new_project", compliance: {id: bool}}`.
  Persist compliance checks into `research/.system/compliance-checklist.json` (set
  `checked_at`), then route by `action` exactly like the S0 3-choice chat menu.
  Gate strip is READ-ONLY — gate approvals stay conversational.

## Security invariants

- Bind 127.0.0.1 only; one-shot session token in `?t=` (403 otherwise; a stray
  POST never kills the session); token masked in access logs.
- Free-text answers pass phi_detect.redact_text before disk; structured PICO
  fields are short clinical vocabulary and are not masked.
- The Desk emits NO telemetry itself — step/gate events fire from the normal
  SKILL.md emission points after you process the answers.
