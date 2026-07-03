# Cohort DSL Reference

## Overview

The Cohort DSL (Domain-Specific Language) is a declarative language for defining retrospective study cohorts. It compiles to SQL for data extraction.

## Syntax

Each line is a clause with the format:
```
CLAUSE_TYPE: expression
```

### Clause Types

| Clause | Required | Multiplicity | Description |
|--------|----------|-------------|-------------|
| `INCLUDE` | Optional | Multiple | Inclusion criteria (AND logic between multiple INCLUDE clauses) |
| `EXCLUDE` | Optional | Multiple | Exclusion criteria |
| `INDEX` | **Required** | Once | Index date definition |
| `FOLLOWUP` | **Required** | Once | Follow-up end definition |

## Examples

### Basic Cohort
```
INCLUDE: age >= 18
INCLUDE: patients.diagnosis_date IS NOT NULL
EXCLUDE: prior outcome within 6 months
INDEX: first diagnosis date
FOLLOWUP: outcome or censoring at last visit
```

### Detailed Cohort with Table References
```
INCLUDE: patients.age >= 18
INCLUDE: patients.enrollment_date >= '2015-01-01'
EXCLUDE: patients.prior_cancer = 1
EXCLUDE: prescriptions.contraindicated = 1
INDEX: prescriptions.first_prescription_date
FOLLOWUP: outcomes.event_date OR patients.last_visit_date
```

## Column References

Use `table_name.column_name` to reference specific database columns:
```
INCLUDE: patients.age >= 65
INCLUDE: labs.hba1c > 7.0
```

When a dataset schema is provided, column references are validated against known tables and columns.

## Validation Rules

### Automatic Checks
1. **INDEX required**: Every cohort must define an index date
2. **FOLLOWUP required**: Every cohort must define follow-up end
3. **Temporal order**: Outcome cannot be defined before index date
4. **Immortal time bias**: Detects survival/event-free conditions in INCLUDE with exposure-based INDEX

### Immortal Time Bias Detection
The compiler flags potential immortal time bias when:
- INCLUDE clauses contain survival/event-free language AND
- INDEX clause uses exposure-based timing (first prescription, treatment start, etc.)

Example that triggers a warning:
```
INCLUDE: survived at least 30 days     ← survival condition
INDEX: first prescription date           ← exposure-based index
FOLLOWUP: death or last visit
```

This is flagged because requiring survival to a point after exposure creates immortal time.

## SQL Compilation

### Without Schema
Generates SQL using `patients` as default base table:
```sql
WITH base_cohort AS (
  SELECT p.*
  FROM patients p
  WHERE (age >= 18)
    AND NOT (prior outcome within 6 months)
)
SELECT * FROM base_cohort;
```

### With Schema
When a dataset schema JSON is provided, the compiler:
1. Resolves table references from DSL clauses
2. Determines the base table (defaults to `patients`)
3. Builds JOIN plan from schema-defined relationships
4. Applies table aliases to predicates
5. Infers joins via `patient_id` if no explicit joins defined

## Dataset Schema Format

```json
{
  "tables": ["patients", "prescriptions", "labs", "outcomes"],
  "columns": [
    {"table": "patients", "name": "patient_id"},
    {"table": "patients", "name": "age"},
    {"table": "prescriptions", "name": "patient_id"},
    {"table": "prescriptions", "name": "drug_code"}
  ],
  "joins": [
    {
      "left_table": "patients",
      "left_key": "patient_id",
      "right_table": "prescriptions",
      "right_key": "patient_id",
      "type": "LEFT"
    }
  ]
}
```

## CLI Usage

```bash
# From file
python3 dsl_compiler.py --dsl cohort.dsl --output extraction.sql

# With schema validation
python3 dsl_compiler.py --dsl cohort.dsl --schema schema.json --output extraction.sql

# From stdin
echo "INCLUDE: age >= 18
INDEX: cohort_start
FOLLOWUP: outcome_or_censor" | python3 dsl_compiler.py --stdin --output extraction.sql
```
