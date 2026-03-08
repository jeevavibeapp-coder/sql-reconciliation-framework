"""
tests/test_reconciliation.py
Run with: python -m pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import pytest
import sqlalchemy as sa
from reconciliation_framework import ReconciliationFramework, get_engine


@pytest.fixture(scope="module")
def engine():
    """In-memory SQLite DB with known data for deterministic tests."""
    eng = sa.create_engine("sqlite:///:memory:")
    with eng.connect() as conn:
        conn.execute(sa.text("""
            CREATE TABLE departments (
                dept_id   INTEGER PRIMARY KEY,
                dept_name TEXT NOT NULL
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE employees (
                emp_id    INTEGER PRIMARY KEY,
                dept_id   INTEGER,
                name      TEXT NOT NULL,
                salary    REAL,
                email     TEXT
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE emp_audit (
                emp_id INTEGER
            )
        """))
        conn.execute(sa.text("INSERT INTO departments VALUES (1,'Engineering'),(2,'Sales'),(3,'HR')"))
        conn.execute(sa.text("""
            INSERT INTO employees VALUES
            (1, 1, 'Alice',  95000, 'alice@x.com'),
            (2, 1, 'Bob',    88000, NULL),
            (3, 2, 'Carol',  72000, 'carol@x.com'),
            (4, 2, 'Dave',   68000, 'dave@x.com'),
            (5, 9, 'Eve',    55000, NULL)
        """))
        conn.execute(sa.text("INSERT INTO emp_audit VALUES (1),(2),(3),(4),(5)"))
        conn.commit()
    return eng


@pytest.fixture(scope="module")
def fw(engine):
    return ReconciliationFramework(engine)


class TestRowCount:
    def test_pass_within_bounds(self, fw):
        r = fw.row_count_check("employees", expected_min=1, expected_max=10)
        assert r.status == "PASS"
        assert r.actual == "5"

    def test_fail_below_min(self, fw):
        r = fw.row_count_check("employees", expected_min=100)
        assert r.status == "FAIL"

    def test_fail_above_max(self, fw):
        r = fw.row_count_check("employees", expected_min=1, expected_max=2)
        assert r.status == "FAIL"

    def test_with_where_clause(self, fw):
        r = fw.row_count_check("employees", expected_min=2, expected_max=3, where="dept_id = 1")
        assert r.status == "PASS"


class TestNullCheck:
    def test_no_nulls_pass(self, fw):
        r = fw.null_check("employees", "name", max_null_pct=0.0)
        assert r.status == "PASS"

    def test_nulls_detected(self, fw):
        # email has 2 nulls out of 5 rows = 40%
        r = fw.null_check("employees", "email", max_null_pct=0.0)
        assert r.status == "FAIL"
        assert "40.0%" in r.actual

    def test_nulls_within_tolerance(self, fw):
        r = fw.null_check("employees", "email", max_null_pct=50.0)
        assert r.status == "PASS"


class TestReferentialIntegrity:
    def test_orphan_detected(self, fw):
        # Eve has dept_id=9 which doesn't exist in departments
        r = fw.referential_integrity_check(
            child_table="employees", child_col="dept_id",
            parent_table="departments", parent_col="dept_id",
        )
        assert r.status == "FAIL"
        assert "1" in r.actual  # 1 orphan

    def test_no_orphans_pass(self, fw):
        r = fw.referential_integrity_check(
            child_table="emp_audit", child_col="emp_id",
            parent_table="employees", parent_col="emp_id",
        )
        assert r.status == "PASS"


class TestDuplicateCheck:
    def test_no_duplicates(self, fw):
        r = fw.duplicate_check("employees", key_columns=["emp_id"])
        assert r.status == "PASS"


class TestValueRange:
    def test_salary_within_range(self, fw):
        r = fw.value_range_check("employees", "salary", min_val=0, max_val=200000)
        assert r.status == "PASS"

    def test_salary_exceeds_max(self, fw):
        r = fw.value_range_check("employees", "salary", min_val=0, max_val=90000)
        assert r.status == "FAIL"


class TestCrossTableCount:
    def test_equal_counts_pass(self, fw):
        r = fw.cross_table_count_check("employees", "emp_audit")
        assert r.status == "PASS"

    def test_unequal_counts_fail(self, fw):
        r = fw.cross_table_count_check("employees", "departments", tolerance_pct=0.0)
        assert r.status == "FAIL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
