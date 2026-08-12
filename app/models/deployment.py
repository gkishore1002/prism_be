"""Deployment initialization state — one row per customer deployment."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.registry import institution_fk_target, registry_schema

_registry = registry_schema()
_init_table_args = {"schema": _registry} if _registry else {}

SINGLETON_ID = "default"


class SystemInitialization(Base):
    """Marks that first-run setup completed for this deployment."""

    __tablename__ = "system_initialization"
    __table_args__ = _init_table_args

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    initialized_at: Mapped[str] = mapped_column(String(32))
    initialized_by_user_id: Mapped[str] = mapped_column(String(32))
