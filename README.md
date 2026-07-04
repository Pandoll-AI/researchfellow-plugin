# ResearchFellow (plugin)

An AI co-researcher for **retrospective clinical research** — a fellow, not a wizard. It
joins your study at *any* stage and carries it to a submission-ready manuscript, keeping
the whole trail auditable. Free tier; the entire 13-step workflow completes offline.

## Install (local marketplace)

Add this repo as a local plugin marketplace, then install the plugin:

```
/plugin marketplace add /path/to/researchfellow-plugin
/plugin install researchfellow
```

(Or point Claude Code at the directory containing `.claude-plugin/plugin.json`.) Scripts
resolve via `${CLAUDE_PLUGIN_ROOT}`; Python 3 (stdlib only) is the only runtime dependency.

> **Trigger conflict — disable the old `research-assistant` skill.** This plugin supersedes
> the legacy `research-assistant` skill. Running both makes `/research` and the research
> keywords ambiguous. **Disable or remove `research-assistant`** before using ResearchFellow.

## Quickstart

```
/research
```

If `/research` is not recognized (another skill or plugin claims the short name — e.g.
the legacy `research-assistant` skill), use the namespaced form:

```
/researchfellow:research
```

- No project yet → pick a starting point (idea / dataset / new manuscript / revise /
  reviewer response), or just type your idea as the argument.
- Existing project → resume from the saved point.

`/research status` shows the dashboard; `/research next` advances; `/research step N`
targets a step. (Same arguments work with the namespaced form.)

## The 13 steps

PICO → literature → evidence table → variables → protocol → SAP → table/figure shells →
synthetic dry-run → data QC → real analysis → manuscript → submission package → revision
loop. Progress is judged by an **artifact DAG** (not a linear cursor), so you can enter at
any stage — a half-written draft, a bare dataset, or reviewer comments. Three **hard gates**
(feasibility, protocol, QC) deterministically block real-data analysis until approved; five
soft gates are handled in conversation. All state lives in `.research/` (`state.json`,
`materials.json`, append-only `audit.jsonl`).

## PHI stays local

All patient-level data and PHI screening happen on your machine and never leave it.
`phi_screener.py` flags Korean identifiers (RRN, phone, name, email, birthdate) locally
and reports only column names and row numbers — never the matched values.

## Optional remote MCP (not required)

A paid remote `researchfellow` MCP server can *deepen* a few steps (novelty check,
methodology advice, checklist mapping, integrity report, reviewer playbook). **It is
optional — the free workflow completes fully without it.** To connect, copy
`.mcp.json.example` to `.mcp.json` and set your server URL. When the server is absent, the
skill simply skips remote enrichment; only de-identified derivatives are ever sent, after a
local PHI screen.
