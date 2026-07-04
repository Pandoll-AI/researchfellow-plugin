# State Machine Rules

## Project States

```
IDLE → RUNNING → COMPLETED
  ↓       ↓ ↑
  ↓    PAUSED
  ↓       ↓
  └→ BLOCKED → RUNNING
         ↓
       FAILED
```

| State | Description |
|-------|-------------|
| `idle` | No active work, initial state |
| `running` | Actively working on a step |
| `paused` | User paused progress |
| `blocked` | Waiting for gate approval or blocker resolution |
| `failed` | Unrecoverable error |
| `completed` | All 12 steps done |

## Step States

| State | Description |
|-------|-------------|
| `pending` | Not yet started |
| `in_progress` | Currently being worked on |
| `completed` | Step finished successfully |
| `skipped` | Intentionally skipped (with reason) |
| `blocked` | Waiting for gate/dependency |

## Transition Rules

### Step Advancement
1. A step can only start if `current_step` matches and all blocking gates for that step are approved
2. Completing step N sets `current_step = N + 1`
3. Steps cannot be skipped without explicit user approval and recorded reason

### Gate Dependencies

| Gate | Blocks Step | Required For |
|------|------------|--------------|
| Gate#1 | Step 2 | Topic Go/No-Go |
| Gate#2 | Step 4 | Novelty confirmation |
| Gate#3 | Step 5 | Endpoint confirmation |
| Gate#4 | Step 9 | Feasibility (real-data gate) |
| Gate#5 | Step 9 | Protocol approval (real-data gate) |
| Gate#9 | Step 10 | Data QC (real-data gate) |
| Gate#10 | Step 11 | Results interpretation |
| Gate#11 | Step 12 | Manuscript approval |

### Real-Data Mode Transition
- Steps 1-8: Planning Mode (default)
- Steps 9-12: Require `execution_mode = "real_data"`
- Mode transition requires all three real-data gates: Gate#4, Gate#5, Gate#9

### Backward Navigation
- User may revisit completed steps for review (read-only)
- Re-executing a completed step creates a new version (audit logged)
- Re-executing steps 1-6 may invalidate downstream artifacts (steps 7-12)

## Gate States

| State | Description |
|-------|-------------|
| `pending` | Not yet reviewed |
| `approved` | User approved |
| `rejected` | User rejected (blocks progress) |
| `changes_requested` | Needs revision before re-review |

### Gate Reversal
- If a previously approved gate is reversed (e.g., Gate#5 changed to rejected):
  - All downstream artifacts are invalidated
  - Project state → `blocked`
  - Affected steps must be re-executed

## Audit Events

Every state transition appends to `audit.jsonl`:
```json
{
  "timestamp": "2026-02-18T10:00:00Z",
  "event": "STEP_COMPLETED",
  "step": 1,
  "from_state": "in_progress",
  "to_state": "completed",
  "details": { "artifacts": ["idea.json"] }
}
```

## Invariants

1. At most one step `in_progress` at a time
2. Gate approval is immutable once recorded (new review creates new entry)
3. Real-data mode requires all three real-data gates approved
4. Synthetic results never appear in manuscript results/conclusions
5. Every artifact has a source step and version
6. Audit log is append-only, never modified
