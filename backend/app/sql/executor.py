"""Execute validated SQL through a read-only database connection."""

import sqlite3
import time
from typing import Any

from app.config import get_settings


from app.workflow.progress import observed

@observed('只读数据库查询')
def execute_sql(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.database_path.exists():
        raise FileNotFoundError("数据库不存在，请先运行 python scripts/init_database.py")
    started = time.perf_counter()
    connection = sqlite3.connect(f"file:{settings.database_path.as_posix()}?mode=ro", uri=True)
    deadline = started + settings.sql_timeout_seconds
    connection.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 1000)
    try:
        cursor = connection.execute(sql, params)
        columns = [item[0] for item in cursor.description or []]
        rows = [dict(zip(columns, row)) for row in cursor.fetchmany(201)]
        return {
            "columns": columns,
            "rows": rows[:200],
            "truncated": len(rows) > 200,
            "row_count": min(len(rows), 200),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    finally:
        connection.close()
