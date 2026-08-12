"""Public registry schema helpers (Postgres multi-tenant mode)."""

from __future__ import annotations

from app.core.config import settings


def registry_schema() -> str | None:
    """Postgres: public registry. SQLite dev: single schema (None)."""
    return "public" if settings.database_url.startswith("postgresql") else None


def institution_fk_target() -> str:
    schema = registry_schema()
    if schema:
        return f"{schema}.institutions.id"
    return "institutions.id"
