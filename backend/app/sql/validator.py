"""Validate SQL syntax and access scope before execution."""

import re

from sqlglot import exp, parse

ALLOWED_TABLES = {"invoice", "customer"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace)\b",
    re.IGNORECASE,
)


def validate_sql(sql: str, *, allowed_tables=None) -> None:
    if FORBIDDEN.search(sql):
        raise ValueError("SQL 包含禁止操作")
    statements = parse(sql, read="sqlite")
    if len(statements) != 1:
        raise ValueError("只允许执行一条 SQL")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise ValueError("只允许 SELECT 查询")
    # sqlglot represents a CTE reference such as `FROM yearly_sales` as a
    # Table node as well. Treat CTE aliases declared by this same statement as
    # local query results, while keeping the physical-table allowlist strict.
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = {table.name.lower() for table in tree.find_all(exp.Table)}
    unknown = tables - (ALLOWED_TABLES if allowed_tables is None else allowed_tables) - cte_names
    if unknown:
        raise ValueError(f"SQL 访问了未授权表: {sorted(unknown)}")


def validate_query_contract(sql: str, params: dict, task) -> None:
    """Accept only the AST and bound values of the supported query contract.

    This intentionally rejects alternative query shapes; it is not a general
    proof of equivalence for arbitrary model-generated SQL.
    """
    from app.sql.generator import compile_query
    if task.query_scope=='schema':
        from app.sql.schema_query import catalog
        validate_sql(sql,allowed_tables={t.lower() for t in catalog()['tables']})
    else:
        validate_sql(sql)
    expected = compile_query(task)
    actual_tree = parse(sql, read="sqlite")[0]
    expected_tree = parse(expected.sql, read="sqlite")[0]
    if actual_tree != expected_tree or params != expected.params:
        raise ValueError("SQL 与查询条件不一致：指标、过滤、时间、分组或比较条件发生变化，已阻止执行")
