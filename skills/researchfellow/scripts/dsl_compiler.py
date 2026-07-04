#!/usr/bin/env python3
"""Cohort DSL to SQL compiler for the Research Assistant skill.

Parses a cohort definition language (INCLUDE/EXCLUDE/INDEX/FOLLOWUP)
and compiles it to SQL. Validates for common retrospective study pitfalls
like immortal time bias and temporal violations.

Usage:
    python3 dsl_compiler.py --dsl cohort.dsl --output extraction.sql
    python3 dsl_compiler.py --dsl cohort.dsl --schema schema.json --output extraction.sql
    echo "INCLUDE: age >= 18\nINDEX: cohort_start\nFOLLOWUP: outcome_or_censor" | python3 dsl_compiler.py --stdin --output extraction.sql
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


class DSLValidationError(ValueError):
    pass


@dataclass
class CohortSpec:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    index: str = ""
    followup: str = ""


CLAUSE_PATTERN = re.compile(r"^(INCLUDE|EXCLUDE|INDEX|FOLLOWUP)\s*:\s*(.+)$", re.IGNORECASE)
IMMORTAL_TIME_PATTERN = re.compile(r"\b(surviv(?:e|ed|al)|event[-\s]?free)\b", re.IGNORECASE)
INDEX_EXPOSURE_PATTERN = re.compile(r"\b(first\s+(prescription|exposure)|treatment\s+start|drug\s+start)\b", re.IGNORECASE)
TEMPORAL_VIOLATION_PATTERN = re.compile(r"\boutcome\s+before\s+index\b", re.IGNORECASE)
COLUMN_REF_PATTERN = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b")


def parse_cohort_dsl(cohort_dsl: str) -> CohortSpec:
    spec = CohortSpec()
    for raw in cohort_dsl.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = CLAUSE_PATTERN.match(line)
        if not match:
            raise DSLValidationError(f"Invalid clause format: '{line}'")
        clause, content = match.group(1).upper(), match.group(2).strip()

        if clause == "INCLUDE":
            spec.include.append(content)
        elif clause == "EXCLUDE":
            spec.exclude.append(content)
        elif clause == "INDEX":
            if spec.index:
                raise DSLValidationError("INDEX clause must appear only once")
            spec.index = content
        elif clause == "FOLLOWUP":
            if spec.followup:
                raise DSLValidationError("FOLLOWUP clause must appear only once")
            spec.followup = content

    if not spec.index:
        raise DSLValidationError("INDEX clause is required")
    if not spec.followup:
        raise DSLValidationError("FOLLOWUP clause is required")

    return spec


def validate_spec(spec: CohortSpec) -> List[str]:
    """Validate spec and return list of warnings."""
    warnings = []

    for clause in [spec.index, spec.followup, *spec.include, *spec.exclude]:
        if TEMPORAL_VIOLATION_PATTERN.search(clause):
            raise DSLValidationError("Detected invalid temporal order pattern")

    if IMMORTAL_TIME_PATTERN.search(" ".join(spec.include)) and INDEX_EXPOSURE_PATTERN.search(spec.index):
        raise DSLValidationError(
            "Potential immortal time bias: survival/event-free condition in INCLUDE with exposure-based index"
        )

    # Additional warnings (non-fatal)
    if not spec.include:
        warnings.append("No INCLUDE clauses defined — entire population will be included")

    return warnings


def _to_sql_predicate(expr: str) -> str:
    normalized = expr.strip()
    normalized = re.sub(r"\bage\s*>=\s*(\d+)\b", r"age >= \1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bage\s*>\s*(\d+)\b", r"age > \1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bage\s*<=\s*(\d+)\b", r"age <= \1", normalized, flags=re.IGNORECASE)

    if "prior outcome" in normalized.lower() and "within" in normalized.lower():
        return (
            "NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.patient_id = p.patient_id "
            "AND o.date BETWEEN p.index_date - INTERVAL '6 months' AND p.index_date)"
        )

    return normalized


def _build_schema_index(dataset_schema: Dict[str, Any]) -> Tuple[Set[str], Dict[str, Set[str]], List[Dict[str, str]]]:
    tables: Set[str] = set()
    table_columns: Dict[str, Set[str]] = {}

    for t in dataset_schema.get("tables", []) or []:
        table_name = t if isinstance(t, str) else str(t.get("name", "")) if isinstance(t, dict) else ""
        if table_name:
            tables.add(table_name)
            table_columns.setdefault(table_name, set())

    for c in dataset_schema.get("columns", []) or []:
        if not isinstance(c, dict):
            continue
        table_name = str(c.get("table", ""))
        column_name = str(c.get("name", ""))
        if table_name and column_name:
            tables.add(table_name)
            table_columns.setdefault(table_name, set()).add(column_name)

    joins: List[Dict[str, str]] = []
    for j in dataset_schema.get("joins", []) or []:
        if not isinstance(j, dict):
            continue
        lt = str(j.get("left_table", ""))
        lk = str(j.get("left_key", ""))
        rt = str(j.get("right_table", ""))
        rk = str(j.get("right_key", ""))
        jt = str(j.get("type", "LEFT")).upper()
        if lt and lk and rt and rk:
            joins.append({"left_table": lt, "left_key": lk, "right_table": rt, "right_key": rk, "type": jt})

    if not joins:
        if "patients" in tables and "patient_id" in table_columns.get("patients", set()):
            for tn in sorted(tables):
                if tn != "patients" and "patient_id" in table_columns.get(tn, set()):
                    joins.append({"left_table": "patients", "left_key": "patient_id", "right_table": tn, "right_key": "patient_id", "type": "LEFT"})

    return tables, table_columns, joins


def _extract_column_refs(spec: CohortSpec) -> Set[Tuple[str, str]]:
    refs: Set[Tuple[str, str]] = set()
    for clause in [spec.index, spec.followup, *spec.include, *spec.exclude]:
        for table_name, column_name in COLUMN_REF_PATTERN.findall(clause):
            refs.add((table_name, column_name))
    return refs


def validate_against_schema(spec: CohortSpec, dataset_schema: Dict[str, Any]) -> None:
    tables, table_columns, _ = _build_schema_index(dataset_schema)
    if not tables:
        raise DSLValidationError("Dataset schema has no tables")

    refs = _extract_column_refs(spec)
    for table_name, column_name in refs:
        if table_name not in tables:
            raise DSLValidationError(f"Unknown table: {table_name}")
        known = table_columns.get(table_name)
        if known and column_name not in known:
            raise DSLValidationError(f"Unknown column: {table_name}.{column_name}")


def compile_to_sql(spec: CohortSpec, dataset_schema: Optional[Dict[str, Any]] = None) -> str:
    include_predicates = [_to_sql_predicate(item) for item in spec.include] or ["TRUE"]
    exclude_predicates = [_to_sql_predicate(item) for item in spec.exclude]

    where_parts = [f"({pred})" for pred in include_predicates]
    for pred in exclude_predicates:
        where_parts.append(f"NOT ({pred})")

    where_sql = "\n    AND ".join(where_parts)

    if dataset_schema is None:
        return (
            f"-- Generated SQL from cohort DSL\n"
            f"-- INDEX: {spec.index}\n"
            f"-- FOLLOWUP: {spec.followup}\n"
            f"WITH base_cohort AS (\n"
            f"  SELECT p.*\n"
            f"  FROM patients p\n"
            f"  WHERE {where_sql}\n"
            f")\n"
            f"SELECT * FROM base_cohort;"
        )

    # Schema-aware compilation with joins
    schema_tables, _, joins = _build_schema_index(dataset_schema)
    refs = _extract_column_refs(spec)
    required_tables = {t for t, _ in refs}

    base = "patients" if "patients" in (required_tables | schema_tables) else sorted(required_tables | schema_tables)[0]
    aliases = {base: "p"}
    idx = 1
    for tn in sorted(schema_tables):
        if tn != base:
            aliases[tn] = f"t{idx}"
            idx += 1

    # Build join plan
    joined = {base}
    pending = set(required_tables) - {base}
    join_lines = []
    while pending:
        progressed = False
        for edge in joins:
            lt, rt = edge["left_table"], edge["right_table"]
            if lt in joined and rt in pending:
                join_lines.append(f"  {edge['type']} JOIN {rt} {aliases.get(rt, rt)} ON {aliases.get(lt, lt)}.{edge['left_key']} = {aliases.get(rt, rt)}.{edge['right_key']}")
                joined.add(rt)
                pending.remove(rt)
                progressed = True
                break
            if rt in joined and lt in pending:
                join_lines.append(f"  {edge['type']} JOIN {lt} {aliases.get(lt, lt)} ON {aliases.get(rt, rt)}.{edge['right_key']} = {aliases.get(lt, lt)}.{edge['left_key']}")
                joined.add(lt)
                pending.remove(lt)
                progressed = True
                break
        if not progressed:
            raise DSLValidationError(f"Cannot build join path for: {', '.join(sorted(pending))}")

    join_sql = "\n".join(join_lines)
    if join_sql:
        join_sql = "\n" + join_sql

    # Apply aliases to predicates
    def apply_aliases(expr: str) -> str:
        def repl(m):
            t, c = m.group(1), m.group(2)
            return f"{aliases.get(t, t)}.{c}"
        return COLUMN_REF_PATTERN.sub(repl, expr)

    aliased_includes = [apply_aliases(p) for p in include_predicates]
    aliased_excludes = [apply_aliases(p) for p in exclude_predicates]
    where_parts = [f"({p})" for p in aliased_includes]
    for p in aliased_excludes:
        where_parts.append(f"NOT ({p})")
    where_sql = "\n    AND ".join(where_parts)

    return (
        f"-- Generated SQL from cohort DSL\n"
        f"-- INDEX: {spec.index}\n"
        f"-- FOLLOWUP: {spec.followup}\n"
        f"-- BASE_TABLE: {base} AS {aliases[base]}\n"
        f"WITH base_cohort AS (\n"
        f"  SELECT {aliases[base]}.*\n"
        f"  FROM {base} {aliases[base]}{join_sql}\n"
        f"  WHERE {where_sql}\n"
        f")\n"
        f"SELECT * FROM base_cohort;"
    )


def compile_dsl(cohort_dsl: str, dataset_schema: Optional[Dict[str, Any]] = None) -> Tuple[str, str, List[str]]:
    spec = parse_cohort_dsl(cohort_dsl)
    warnings = validate_spec(spec)
    if dataset_schema is not None:
        validate_against_schema(spec, dataset_schema)
    sql = compile_to_sql(spec, dataset_schema=dataset_schema)
    digest = hashlib.sha256(cohort_dsl.encode("utf-8")).hexdigest()
    return sql, digest, warnings


def main():
    parser = argparse.ArgumentParser(description="Compile Cohort DSL to SQL")
    parser.add_argument("--dsl", help="Path to DSL file")
    parser.add_argument("--stdin", action="store_true", help="Read DSL from stdin")
    parser.add_argument("--schema", help="Path to dataset schema JSON (optional)")
    parser.add_argument("--output", required=True, help="Output SQL file path")
    args = parser.parse_args()

    if args.stdin:
        dsl_text = sys.stdin.read()
    elif args.dsl:
        with open(args.dsl) as f:
            dsl_text = f.read()
    else:
        print("ERROR: Provide --dsl or --stdin", file=sys.stderr)
        sys.exit(1)

    schema = None
    if args.schema:
        with open(args.schema) as f:
            schema = json.load(f)

    try:
        sql, digest, warnings = compile_dsl(dsl_text, schema)
    except DSLValidationError as exc:
        print(f"VALIDATION ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        f.write(sql)

    for w in warnings:
        print(f"  WARNING: {w}", file=sys.stderr)

    print(f"SQL written to {args.output}")
    print(f"DSL hash: {digest[:16]}...")


if __name__ == "__main__":
    main()
