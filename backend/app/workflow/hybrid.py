"""Helpers for the SQL-to-document minimum cross-source flow."""


def extract_primary_entity(sql_result: dict) -> str | None:
    rows = sql_result.get("rows") or []
    if not rows:
        return None
    first = rows[0]
    return first.get("BillingCountry") or first.get("Country")
