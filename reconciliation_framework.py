"""
reconciliation_framework.py
============================
Reusable SQL-based data reconciliation framework for DB2 batch tables.
Automates row-count checks, null validation, and referential integrity checks.
Replaces ad-hoc querying with a consistent, auditable process.

Usage:
    python reconciliation_framework.py --config config.json --output reports/

Supported databases via connection string:
    DB2:        ibm_db_sa://user:pass@host:port/dbname
    PostgreSQL: postgresql://user:pass@host:port/dbname   (for local testing)
    SQLite:     sqlite:///path/to/file.db                 (for demo/testing)
"""

import json
import csv
import argparse
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import sqlalchemy as sa
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt= "%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    check_type:   str
    table:        str
    column:       Optional[str]
    status:       str          # PASS | FAIL | WARN | ERROR
    detail:       str
    expected:     Optional[str] = None
    actual:       Optional[str] = None
    run_at:       str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def passed(self)  -> bool: return self.status == "PASS"
    def failed(self)  -> bool: return self.status == "FAIL"


@dataclass
class ReconciliationReport:
    run_id:   str
    run_at:   str
    source:   str
    results:  list[CheckResult] = field(default_factory=list)

    @property
    def total(self)   -> int: return len(self.results)
    @property
    def passed(self)  -> int: return sum(1 for r in self.results if r.passed())
    @property
    def failed(self)  -> int: return sum(1 for r in self.results if r.failed())
    @property
    def success_rate(self) -> float:
        return round(self.passed / self.total * 100, 1) if self.total else 0.0


# ── Database connection ────────────────────────────────────────────────────────

def get_engine(connection_string: str):
    if not HAS_SQLALCHEMY:
        raise RuntimeError("sqlalchemy is not installed. Run: pip install sqlalchemy")
    return sa.create_engine(connection_string)


def execute_scalar(engine, sql: str, params: dict = None):
    """Execute a query and return the single scalar value."""
    with engine.connect() as conn:
        result = conn.execute(sa.text(sql), params or {})
        row = result.fetchone()
        return row[0] if row else None


def execute_rows(engine, sql: str, params: dict = None) -> list[dict]:
    """Execute a query and return all rows as list of dicts."""
    with engine.connect() as conn:
        result = conn.execute(sa.text(sql), params or {})
        keys = result.keys()
        return [dict(zip(keys, row)) for row in result.fetchall()]


# ── Check implementations ─────────────────────────────────────────────────────

class ReconciliationFramework:
    """
    Core framework for running SQL-based data quality checks.
    Each check method returns a CheckResult.
    """

    def __init__(self, engine):
        self.engine = engine

    # 1. Row-count check -------------------------------------------------------

    def row_count_check(
        self,
        table: str,
        expected_min: int,
        expected_max: Optional[int] = None,
        where: str = "1=1",
    ) -> CheckResult:
        """Assert that a table contains a row count within expected bounds."""
        sql   = f"SELECT COUNT(*) FROM {table} WHERE {where}"  # noqa: S608
        try:
            actual = int(execute_scalar(self.engine, sql))
        except Exception as exc:
            return CheckResult("row_count", table, None, "ERROR", str(exc))

        if expected_max is None:
            ok = actual >= expected_min
            detail = f"Row count {actual} {'≥' if ok else '<'} min {expected_min}"
        else:
            ok = expected_min <= actual <= expected_max
            detail = f"Row count {actual} {'within' if ok else 'outside'} [{expected_min}, {expected_max}]"

        return CheckResult(
            check_type="row_count",
            table=table,
            column=None,
            status="PASS" if ok else "FAIL",
            detail=detail,
            expected=f"[{expected_min}, {expected_max or '∞'}]",
            actual=str(actual),
        )

    # 2. Null check ------------------------------------------------------------

    def null_check(
        self,
        table: str,
        column: str,
        max_null_pct: float = 0.0,
        where: str = "1=1",
    ) -> CheckResult:
        """Assert that null percentage in a column is within tolerance."""
        sql = f"""
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS null_count
            FROM {table}
            WHERE {where}
        """  # noqa: S608
        try:
            rows = execute_rows(self.engine, sql)
            total = int(rows[0]["total_rows"])
            nulls = int(rows[0]["null_count"])
        except Exception as exc:
            return CheckResult("null_check", table, column, "ERROR", str(exc))

        null_pct = round(nulls / total * 100, 2) if total > 0 else 0.0
        ok = null_pct <= max_null_pct

        return CheckResult(
            check_type="null_check",
            table=table,
            column=column,
            status="PASS" if ok else "FAIL",
            detail=f"Null rate {null_pct}% ({'≤' if ok else '>'} allowed {max_null_pct}%) – {nulls}/{total} rows",
            expected=f"≤ {max_null_pct}%",
            actual=f"{null_pct}%",
        )

    # 3. Referential integrity check -------------------------------------------

    def referential_integrity_check(
        self,
        child_table: str,
        child_col: str,
        parent_table: str,
        parent_col: str,
        where: str = "1=1",
    ) -> CheckResult:
        """Assert that all FK values in child table exist in parent table."""
        sql = f"""
            SELECT COUNT(*) AS orphan_count
            FROM {child_table} c
            WHERE c.{child_col} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {parent_table} p
                  WHERE p.{parent_col} = c.{child_col}
              )
              AND ({where})
        """  # noqa: S608
        try:
            orphans = int(execute_scalar(self.engine, sql))
        except Exception as exc:
            return CheckResult("referential_integrity", child_table, child_col, "ERROR", str(exc))

        ok = orphans == 0
        return CheckResult(
            check_type="referential_integrity",
            table=f"{child_table}.{child_col} → {parent_table}.{parent_col}",
            column=child_col,
            status="PASS" if ok else "FAIL",
            detail=f"{orphans} orphan records found" if not ok else "All FK values match parent",
            expected="0 orphans",
            actual=str(orphans),
        )

    # 4. Duplicate check -------------------------------------------------------

    def duplicate_check(
        self,
        table: str,
        key_columns: list[str],
        max_dup_pct: float = 0.0,
    ) -> CheckResult:
        """Assert that composite key has no (or acceptable) duplicates."""
        key_expr = ", ".join(key_columns)
        sql = f"""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(*) - COUNT(DISTINCT CONCAT({', '.join([f"CAST({c} AS VARCHAR(255))" for c in key_columns])})) AS dup_count
            FROM {table}
        """  # noqa: S608
        try:
            rows     = execute_rows(self.engine, sql)
            total    = int(rows[0]["total_rows"])
            dup_count = int(rows[0]["dup_count"])
        except Exception as exc:
            return CheckResult("duplicate_check", table, key_expr, "ERROR", str(exc))

        dup_pct = round(dup_count / total * 100, 2) if total > 0 else 0.0
        ok = dup_pct <= max_dup_pct
        return CheckResult(
            check_type="duplicate_check",
            table=table,
            column=key_expr,
            status="PASS" if ok else "FAIL",
            detail=f"{dup_count} duplicates ({dup_pct}%) on [{key_expr}]",
            expected=f"≤ {max_dup_pct}%",
            actual=f"{dup_pct}%",
        )

    # 5. Value range check -----------------------------------------------------

    def value_range_check(
        self,
        table: str,
        column: str,
        min_val=None,
        max_val=None,
    ) -> CheckResult:
        """Assert that a numeric column stays within expected bounds."""
        sql = f"SELECT MIN({column}), MAX({column}) FROM {table}"  # noqa: S608
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sa.text(sql)).fetchone()
                actual_min, actual_max = row[0], row[1]
        except Exception as exc:
            return CheckResult("value_range", table, column, "ERROR", str(exc))

        violations = []
        if min_val is not None and actual_min is not None and actual_min < min_val:
            violations.append(f"min {actual_min} < allowed {min_val}")
        if max_val is not None and actual_max is not None and actual_max > max_val:
            violations.append(f"max {actual_max} > allowed {max_val}")

        ok = len(violations) == 0
        return CheckResult(
            check_type="value_range",
            table=table,
            column=column,
            status="PASS" if ok else "FAIL",
            detail="; ".join(violations) if violations else f"Values in [{actual_min}, {actual_max}] within bounds",
            expected=f"[{min_val}, {max_val}]",
            actual=f"[{actual_min}, {actual_max}]",
        )

    # 6. Cross-table count reconciliation -------------------------------------

    def cross_table_count_check(
        self,
        source_table: str,
        target_table: str,
        source_where: str = "1=1",
        target_where: str = "1=1",
        tolerance_pct: float = 0.0,
    ) -> CheckResult:
        """Assert that source and target tables have matching row counts (within tolerance)."""
        try:
            src = int(execute_scalar(self.engine, f"SELECT COUNT(*) FROM {source_table} WHERE {source_where}"))
            tgt = int(execute_scalar(self.engine, f"SELECT COUNT(*) FROM {target_table} WHERE {target_where}"))
        except Exception as exc:
            return CheckResult("cross_table_count", f"{source_table}↔{target_table}", None, "ERROR", str(exc))

        diff_pct = abs(src - tgt) / max(src, tgt) * 100 if max(src, tgt) > 0 else 0.0
        ok = diff_pct <= tolerance_pct
        return CheckResult(
            check_type="cross_table_count",
            table=f"{source_table} ↔ {target_table}",
            column=None,
            status="PASS" if ok else "FAIL",
            detail=f"{source_table}={src} rows, {target_table}={tgt} rows, diff={diff_pct:.1f}%",
            expected=f"diff ≤ {tolerance_pct}%",
            actual=f"{diff_pct:.1f}%",
        )

    # ── Run all checks from config ─────────────────────────────────────────────

    def run_from_config(self, config: dict) -> ReconciliationReport:
        report = ReconciliationReport(
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S"),
            run_at = datetime.now().isoformat(timespec="seconds"),
            source = config.get("source", "unknown"),
        )
        checks = config.get("checks", [])
        log.info("Running %d checks from config…", len(checks))

        for chk in checks:
            ctype = chk.get("type")
            log.info("  %-30s  %s", ctype, chk.get("table", ""))
            try:
                if ctype == "row_count":
                    result = self.row_count_check(
                        table        = chk["table"],
                        expected_min = chk.get("expected_min", 1),
                        expected_max = chk.get("expected_max"),
                        where        = chk.get("where", "1=1"),
                    )
                elif ctype == "null_check":
                    result = self.null_check(
                        table        = chk["table"],
                        column       = chk["column"],
                        max_null_pct = chk.get("max_null_pct", 0.0),
                        where        = chk.get("where", "1=1"),
                    )
                elif ctype == "referential_integrity":
                    result = self.referential_integrity_check(
                        child_table  = chk["child_table"],
                        child_col    = chk["child_col"],
                        parent_table = chk["parent_table"],
                        parent_col   = chk["parent_col"],
                    )
                elif ctype == "duplicate_check":
                    result = self.duplicate_check(
                        table       = chk["table"],
                        key_columns = chk["key_columns"],
                        max_dup_pct = chk.get("max_dup_pct", 0.0),
                    )
                elif ctype == "value_range":
                    result = self.value_range_check(
                        table   = chk["table"],
                        column  = chk["column"],
                        min_val = chk.get("min_val"),
                        max_val = chk.get("max_val"),
                    )
                elif ctype == "cross_table_count":
                    result = self.cross_table_count_check(
                        source_table  = chk["source_table"],
                        target_table  = chk["target_table"],
                        source_where  = chk.get("source_where", "1=1"),
                        target_where  = chk.get("target_where", "1=1"),
                        tolerance_pct = chk.get("tolerance_pct", 0.0),
                    )
                else:
                    result = CheckResult(ctype or "unknown", chk.get("table", "?"), None, "ERROR", f"Unknown check type: {ctype}")
            except Exception as exc:
                result = CheckResult(ctype or "unknown", chk.get("table", "?"), None, "ERROR", str(exc))

            log.info("    → %s  %s", result.status, result.detail)
            report.results.append(result)

        return report


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(report: ReconciliationReport):
    print("\n" + "=" * 70)
    print(f"  RECONCILIATION REPORT  |  run_id={report.run_id}")
    print(f"  Source: {report.source}  |  Run at: {report.run_at}")
    print("=" * 70)
    for r in report.results:
        icon = "✅" if r.passed() else ("❌" if r.failed() else "⚠️")
        col  = f".{r.column}" if r.column else ""
        print(f"  {icon}  [{r.check_type}]  {r.table}{col}")
        print(f"       {r.detail}")
    print("-" * 70)
    print(f"  Total: {report.total}   Passed: {report.passed}   Failed: {report.failed}   "
          f"Success Rate: {report.success_rate}%")
    print("=" * 70 + "\n")


def save_report_csv(report: ReconciliationReport, output_dir: str):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"recon_report_{report.run_id}.csv"
    fields = ["run_id", "run_at", "check_type", "table", "column", "status", "detail", "expected", "actual"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in report.results:
            row = asdict(r)
            row["run_id"] = report.run_id
            row["run_at"] = report.run_at
            w.writerow({k: row.get(k, "") for k in fields})
    log.info("CSV report saved → %s", path)
    return str(path)


def save_report_json(report: ReconciliationReport, output_dir: str):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"recon_report_{report.run_id}.json"
    data = {
        "run_id": report.run_id,
        "run_at": report.run_at,
        "source": report.source,
        "summary": {
            "total":        report.total,
            "passed":       report.passed,
            "failed":       report.failed,
            "success_rate": report.success_rate,
        },
        "results": [asdict(r) for r in report.results],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    log.info("JSON report saved → %s", path)
    return str(path)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SQL Reconciliation Framework")
    parser.add_argument("--config",     required=True, help="Path to JSON config file")
    parser.add_argument("--output",     default="reports", help="Output directory for reports")
    parser.add_argument("--connection", default=None,  help="Override DB connection string")
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    conn_str = args.connection or config.get("connection_string")
    if not conn_str:
        log.error("connection_string not provided in config or CLI args.")
        sys.exit(1)

    engine  = get_engine(conn_str)
    fw      = ReconciliationFramework(engine)
    report  = fw.run_from_config(config)

    print_report(report)
    save_report_csv(report, args.output)
    save_report_json(report, args.output)

    sys.exit(0 if report.failed == 0 else 1)


if __name__ == "__main__":
    main()
