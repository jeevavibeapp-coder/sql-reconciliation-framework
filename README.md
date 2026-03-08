# SQL-Based Data Reconciliation Framework

A reusable Python framework that automates SQL data quality checks across batch pipeline tables — replacing ad-hoc querying with a **consistent, auditable, config-driven process**. Supports DB2, PostgreSQL, and SQLite.

---

## Features

| Check Type | Description |
|------------|-------------|
| **Row Count** | Assert table has rows within expected bounds |
| **Null Check** | Validate null percentage per column with tolerance threshold |
| **Referential Integrity** | Detect orphan FK records across tables |
| **Duplicate Check** | Find duplicate composite keys |
| **Value Range** | Assert numeric columns stay within min/max bounds |
| **Cross-Table Count** | Reconcile row counts between source and target tables |

All checks produce structured results (PASS / FAIL / WARN / ERROR) with CSV + JSON audit reports per run.

---

## Project Structure

```
sql-reconciliation-framework/
├── reconciliation_framework.py  # Core engine – all checks + reporting
├── config.json                  # Sample check config (edit for your tables)
├── demo_run.py                  # End-to-end demo on SQLite (no DB2 needed)
├── requirements.txt
└── tests/
    └── test_reconciliation.py   # 14 unit tests
```

---

## Quickstart

### 1. Install & run demo
```bash
git clone https://github.com/jeeva-s0604/sql-reconciliation-framework.git
cd sql-reconciliation-framework
pip install -r requirements.txt
python demo_run.py
```

### 2. Run your own checks
```bash
# Edit config.json with your tables and checks, then:
python reconciliation_framework.py \
  --config   config.json \
  --output   reports/ \
  --connection "ibm_db_sa://user:pass@host:50000/MYDB"    # DB2
  # or: "postgresql://user:pass@host:5432/mydb"
```

### 3. Run tests
```bash
python -m pytest tests/ -v
```

---

## Config Format

```json
{
  "source": "daily_batch",
  "connection_string": "sqlite:///demo.db",
  "checks": [
    { "type": "row_count",           "table": "ORDERS",        "expected_min": 100 },
    { "type": "null_check",          "table": "ORDERS",        "column": "ORDER_ID",   "max_null_pct": 0.0 },
    { "type": "referential_integrity","child_table": "ITEMS",  "child_col": "ORDER_ID",
                                      "parent_table": "ORDERS","parent_col": "ORDER_ID" },
    { "type": "duplicate_check",     "table": "ORDERS",        "key_columns": ["ORDER_ID"] },
    { "type": "value_range",         "table": "ITEMS",         "column": "PRICE", "min_val": 0 },
    { "type": "cross_table_count",   "source_table": "ORDERS", "target_table": "ORDER_AUDIT", "tolerance_pct": 1.0 }
  ]
}
```

---

## Sample Output

```
======================================================================
  RECONCILIATION REPORT  |  run_id=20250115_063000
  Source: batch_pipeline_daily  |  Run at: 2025-01-15T06:30:00
======================================================================
  ✅  [row_count]  transactions
       Row count 289 within [100, 10000]
  ✅  [null_check]  transactions.transaction_id
       Null rate 0.0% (≤ allowed 0.0%) – 0/500 rows
  ❌  [null_check]  customers.email
       Null rate 10.0% (> allowed 2.0%) – 20/200 rows
  ✅  [referential_integrity]  transactions.customer_id → customers.id
       All FK values match parent
----------------------------------------------------------------------
  Total: 9   Passed: 8   Failed: 1   Success Rate: 88.9%
======================================================================
```

Reports saved as CSV + JSON to `reports/` for audit trail.

---

## DB2 Connection

```bash
pip install sqlalchemy ibm_db_sa ibm_db
python reconciliation_framework.py \
  --config config.json \
  --connection "ibm_db_sa://DB2USER:password@hostname:50000/DATABASE"
```

---

## Tech Stack

`Python 3.12` · `SQLAlchemy` · `DB2` · `PostgreSQL` · `SQLite` · `pytest`

---

*Built by Jeeva S — [linkedin.com/in/jeeva-s0604](https://linkedin.com/in/jeeva-s0604)*
