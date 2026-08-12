from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.registry import institution_fk_target, registry_schema

_registry = registry_schema()
_institution_table_args = {"schema": _registry} if _registry else {}


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = _institution_table_args

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    schema_name: Mapped[str] = mapped_column(String(128), default="public")
    type: Mapped[str] = mapped_column(String(32), default="coaching")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    board_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    policies_json: Mapped[str] = mapped_column(Text, default="{}")


class Center(Base):
    __tablename__ = "centers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    active: Mapped[bool] = mapped_column(default=True)
    student_count: Mapped[int] = mapped_column(default=0)
    batch_count: Mapped[int] = mapped_column(default=0)

    institution: Mapped["Institution"] = relationship("Institution", foreign_keys=[institution_id])
