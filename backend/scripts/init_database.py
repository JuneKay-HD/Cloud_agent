"""Create a deterministic SQLite dataset for the minimum demo."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings

CUSTOMERS = [
    (1, "Alice", "Brown", "USA"),
    (2, "Bob", "Miller", "USA"),
    (3, "Claire", "Martin", "France"),
    (4, "Louis", "Bernard", "France"),
    (5, "Anna", "Schmidt", "Germany"),
    (6, "Paul", "Fischer", "Germany"),
    (7, "Emma", "Wilson", "Canada"),
    (8, "Noah", "Taylor", "Canada"),
    (9, "Li", "Ming", "China"),
    (10, "Wang", "Fang", "China"),
]

INVOICES = [
    (1, 1, "2011-02-10", "USA", 40.0), (2, 2, "2011-08-18", "USA", 60.0),
    (3, 3, "2011-03-12", "France", 30.0), (4, 4, "2011-10-22", "France", 50.0),
    (5, 5, "2011-04-08", "Germany", 45.0), (6, 6, "2011-11-14", "Germany", 45.0),
    (7, 7, "2011-05-16", "Canada", 30.0), (8, 8, "2011-12-06", "Canada", 40.0),
    (9, 1, "2012-01-15", "USA", 70.0), (10, 2, "2012-09-20", "USA", 90.0),
    (11, 3, "2012-02-17", "France", 50.0), (12, 4, "2012-10-11", "France", 70.0),
    (13, 5, "2012-03-21", "Germany", 49.0), (14, 6, "2012-11-09", "Germany", 50.0),
    (15, 7, "2012-04-25", "Canada", 45.0), (16, 8, "2012-12-12", "Canada", 60.0),
    (17, 9, "2011-06-18", "China", 55.0), (18, 10, "2011-12-20", "China", 45.0),
    (19, 9, "2012-06-16", "China", 90.0), (20, 10, "2012-12-21", "China", 60.0),
]


def main() -> None:
    path = get_settings().database_path
    if path.exists():
        raise FileExistsError(f"拒绝覆盖已有数据库: {path}；请使用新的 DATABASE_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            DROP TABLE IF EXISTS Invoice;
            DROP TABLE IF EXISTS Customer;
            CREATE TABLE Customer (
                CustomerId INTEGER PRIMARY KEY,
                FirstName TEXT NOT NULL,
                LastName TEXT NOT NULL,
                Country TEXT NOT NULL
            );
            CREATE TABLE Invoice (
                InvoiceId INTEGER PRIMARY KEY,
                CustomerId INTEGER NOT NULL REFERENCES Customer(CustomerId),
                InvoiceDate TEXT NOT NULL,
                BillingCountry TEXT NOT NULL,
                Total REAL NOT NULL CHECK(Total >= 0)
            );
            CREATE INDEX idx_invoice_date ON Invoice(InvoiceDate);
            CREATE INDEX idx_invoice_country ON Invoice(BillingCountry);
            """
        )
        connection.executemany("INSERT INTO Customer VALUES (?, ?, ?, ?)", CUSTOMERS)
        connection.executemany("INSERT INTO Invoice VALUES (?, ?, ?, ?, ?)", INVOICES)
        connection.commit()
    finally:
        connection.close()
    print(f"数据库已创建: {path}，客户 {len(CUSTOMERS)} 条，发票 {len(INVOICES)} 条")


if __name__ == "__main__":
    main()
