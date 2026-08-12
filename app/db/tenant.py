"""Provision and migrate per-tenant Postgres schemas."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateSchema

from app.db.base import Base
from app.services.tenant_context import is_multi_schema_enabled

# Registry tables live in public only.
PUBLIC_TABLE_NAMES = frozenset({"institutions", "system_initialization", "super_admins"})


def tenant_tables():
    return [t for t in Base.metadata.sorted_tables if t.name not in PUBLIC_TABLE_NAMES]


def ensure_institution_schema_name(engine: Engine) -> None:
    if not inspect(engine).has_table("institutions"):
        return
    cols = {c["name"] for c in inspect(engine).get_columns("institutions")}
    if "schema_name" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE institutions ADD COLUMN schema_name VARCHAR(128) NOT NULL DEFAULT 'public'"))
            conn.execute(text("UPDATE institutions SET schema_name = 'public' WHERE schema_name = '' OR schema_name IS NULL"))


def create_tenant_schema(engine: Engine, schema_name: str) -> None:
    """Create schema and clone tenant DDL (Postgres multi-schema mode only)."""
    if not is_multi_schema_enabled():
        return
    if schema_name == "public":
        return

    with engine.begin() as conn:
        conn.execute(CreateSchema(schema_name, if_not_exists=True))

    tables = tenant_tables()
    if not tables:
        return

    with engine.begin() as conn:
        conn = conn.execution_options(schema_translate_map={None: schema_name})
        Base.metadata.create_all(bind=conn, tables=tables, checkfirst=True)


def patch_all_tenant_schemas(engine: Engine, patch_fn) -> int:
    """Run patch_fn(engine, schema_name) for every registered tenant schema."""
    if not is_multi_schema_enabled():
        patch_fn(engine, "public")
        return 1
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT schema_name FROM public.institutions WHERE schema_name != 'public'")).fetchall()
    count = 0
    for (schema_name,) in rows:
        patch_fn(engine, schema_name)
        count += 1
    return count
