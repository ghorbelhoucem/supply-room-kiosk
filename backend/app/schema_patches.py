"""
Tiny, idempotent, ADDITIVE schema patches, applied at every start-up.

SQLAlchemy's create_all only creates tables that do not exist yet; it never adds a
column to a table that already does. Rather than a manual one-off migration script
(which, if forgotten, breaks every room at once), new nullable columns are listed here
and added automatically when the API starts. Running it again changes nothing.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

# (table, column, SQL type) — additive; a NOT NULL column must carry a DEFAULT so existing rows get a value
PATCHES = [
    ("inventory_items", "shelf_location", "VARCHAR(8)"),
    ("inventory_items", "is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
]


def apply_schema_patches(engine) -> list[str]:
    applied: list[str] = []
    for table, column, ddl in PATCHES:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            continue  # a brand-new database: create_all builds the table with the column
        if column in {c["name"] for c in insp.get_columns(table)}:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied.append(f"{table}.{column}")
        except Exception:  # noqa: BLE001 — e.g. another process added it a moment ago
            if column not in {c["name"] for c in inspect(engine).get_columns(table)}:
                raise
    return applied
