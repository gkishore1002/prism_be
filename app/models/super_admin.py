"""Platform super user — lives in public schema only (Swotify-style)."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.registry import registry_schema

_registry = registry_schema()
_table_args = {"schema": _registry} if _registry else {}


class SuperAdmin(Base):
    __tablename__ = "super_admins"
    __table_args__ = _table_args

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="Super Admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
