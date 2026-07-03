# Research Assistant — Claude Code Skill

A 12-step retrospective medical research workflow assistant that runs inside Claude Code. No web server, database, or external LLM needed.

## Quick Start

```
/research                        # Start new project or resume existing
/research status                 # Show progress dashboard
/research next                   # Continue to next step
/research step 5                 # Jump to step 5
```

## How It Works

The skill manages research projects through a `.research/` directory in your project folder, storing all state, artifacts, and audit trails as local files.

**12 Steps:** Idea → Literature → Evidence → Variables → Protocol → SAP → Shells → Synthetic → Data Prep → Analysis → Manuscript → Package

**Key Features:**
- PICO/PECO extraction from free-text research ideas
- PubMed search with structured evidence table generation
- Protocol and SAP generation with templates
- Cohort DSL → SQL compilation with bias detection
- Statistical analysis (GLM binomial, Cox PH, effect measures)
- STROBE/RECORD/TRIPOD checklist mapping
- HITL gates at every critical decision point
- Full audit trail (append-only JSONL)

## Architecture

```
~/.claude/skills/research-assistant/
├── SKILL.md              # Skill entry point
├── references/           # Workflow rules, state machine, guardrails
├── templates/            # JSON/Markdown templates for each step
├── scripts/              # Python tools (PubMed, analysis, DSL, QC)
└── examples/             # Sample workflow walkthrough
```

**Project data** lives in `.research/` within your working directory:
```
.research/
├── state.json            # Current progress
├── idea.json             # PICO structure
├── literature/           # Search results
├── evidence-table.json   # Structured evidence
├── variables.json        # Variable definitions
├── protocol.md           # Study protocol
├── sap.md                # Statistical analysis plan
├── shells/               # Table/figure templates
├── analysis/             # Synthetic and real results
├── manuscript.md         # IMRD draft
├── checklist.json        # Reporting checklist
├── gates.json            # Gate approval history
└── audit.jsonl           # Full audit trail
```

## Scripts

All scripts are standalone Python (stdlib + optional pandas/statsmodels/lifelines):

| Script | Purpose | Dependencies |
|--------|---------|-------------|
| `pubmed_search.py` | PubMed E-utilities search | stdlib only |
| `analysis_runner.py` | Effect measures, GLM, Cox PH | pandas, statsmodels, lifelines (optional) |
| `dsl_compiler.py` | Cohort DSL → SQL | stdlib only |
| `qc_checker.py` | Data quality validation | pandas (for CSV) |

## Safety Guardrails

- Planning Mode (steps 1-8) vs Real-Data Mode (steps 9-12) separation
- Required gate approvals before real-data analysis
- Synthetic results watermarked, never in manuscript results
- Immortal time bias detection in cohort DSL
- All state changes audit-logged
