"""
demo_run.py
-----------
Creates a local SQLite demo database and runs the full reconciliation
framework against it — no DB2 / AWS needed.

    python demo_run.py
"""

import sqlite3
import os
import sys
from pathlib import Path

DB_PATH = "sample_data/demo.db"


def create_demo_db():
    Path("sample_data").mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    cur.executescript("""
        DROP TABLE IF EXISTS transaction_audit;
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            id       INTEGER PRIMARY KEY,
            name     TEXT NOT NULL,
            email    TEXT
        );

        CREATE TABLE products (
            id    INTEGER PRIMARY KEY,
            name  TEXT NOT NULL,
            price REAL NOT NULL
        );

        CREATE TABLE transactions (
            transaction_id  TEXT PRIMARY KEY,
            customer_id     INTEGER NOT NULL,
            amount          REAL NOT NULL,
            status          TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE order_items (
            id             INTEGER PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            product_id     INTEGER NOT NULL,
            quantity       INTEGER NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        -- Mirror table for cross-table count check
        CREATE TABLE transaction_audit (
            transaction_id TEXT,
            audited_at     TEXT
        );
    """)

    # Customers
    customers = [(i, f"Customer {i}", f"c{i}@example.com" if i % 10 != 0 else None)
                 for i in range(1, 201)]
    cur.executemany("INSERT INTO customers VALUES (?,?,?)", customers)

    # Products
    products = [(i, f"Product {i}", round(10 + i * 2.5, 2)) for i in range(1, 51)]
    cur.executemany("INSERT INTO products VALUES (?,?,?)", products)

    # Transactions (500 rows, all with valid customer_id)
    import random, datetime
    random.seed(42)
    txns = []
    for i in range(1, 501):
        txns.append((
            f"TXN{i:05d}",
            random.randint(1, 200),
            round(random.uniform(10, 9999), 2),
            random.choice(["COMPLETED", "COMPLETED", "COMPLETED", "PENDING", "FAILED"]),
            datetime.date(2025, random.randint(1, 12), random.randint(1, 28)).isoformat(),
        ))
    cur.executemany("INSERT INTO transactions VALUES (?,?,?,?,?)", txns)

    # Order items (reference valid products)
    items = [(i, f"TXN{random.randint(1,500):05d}", random.randint(1, 50), random.randint(1, 5))
             for i in range(1, 201)]
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)

    # Audit mirror (same 500 rows → cross-table check should PASS)
    audit = [(f"TXN{i:05d}", "2025-01-01") for i in range(1, 501)]
    cur.executemany("INSERT INTO transaction_audit VALUES (?,?)", audit)

    conn.commit()
    conn.close()
    print(f"✅  Demo database created: {DB_PATH}")


def run_framework():
    """Run the reconciliation framework against the demo DB."""
    from reconciliation_framework import get_engine, ReconciliationFramework, print_report, save_report_csv, save_report_json
    import json

    with open("config.json") as f:
        config = json.load(f)

    engine = get_engine(f"sqlite:///{DB_PATH}")
    fw     = ReconciliationFramework(engine)
    report = fw.run_from_config(config)

    print_report(report)
    save_report_csv(report, "reports")
    save_report_json(report, "reports")

    return report.failed == 0


if __name__ == "__main__":
    create_demo_db()
    success = run_framework()
    sys.exit(0 if success else 1)
