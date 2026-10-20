"""Load the small approved schema supplied to the SQL generator."""

import json

import yaml

from app.config import get_settings


def load_sql_context() -> dict:
    settings = get_settings()
    schema = json.loads((settings.config_path / "schema_catalog.json").read_text(encoding="utf-8"))
    metrics = yaml.safe_load((settings.config_path / "metrics.yaml").read_text(encoding="utf-8"))
    entities = yaml.safe_load((settings.config_path / "entities.yaml").read_text(encoding="utf-8"))
    return {"schema": schema, "metrics": metrics, "entities": entities}


def normalize_sql_params(params: dict) -> dict:
    """Map user-facing entity aliases to values stored in the database."""
    settings = get_settings()
    payload = yaml.safe_load((settings.config_path / "entities.yaml").read_text(encoding="utf-8")) or {}
    normalized_values: dict[str, str] = {}
    for entity in (payload.get("entities") or {}).values():
        for canonical, aliases in (entity.get("value_aliases") or {}).items():
            normalized_values[str(canonical).casefold()] = str(canonical)
            for alias in aliases or []:
                normalized_values[str(alias).casefold()] = str(canonical)
    return {
        key: normalized_values.get(value.strip().casefold(), value) if isinstance(value, str) else value
        for key, value in params.items()
    }
