"""Daily aggregated LLM token usage per institution / service / model."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.registry import institution_fk_target, registry_schema

_registry = registry_schema()
_kw = {"schema": _registry} if _registry else {}


class LlmUsageDaily(Base):
    __tablename__ = "llm_usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "usage_date",
            "service",
            "model",
            name="uq_llm_usage_daily_inst_date_svc_model",
        ),
        _kw,
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(
        String(32), ForeignKey(institution_fk_target()), index=True
    )
    usage_date: Mapped[str] = mapped_column(String(16), index=True)  # YYYY-MM-DD
    service: Mapped[str] = mapped_column(String(64), default="other")
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String(32), default="")
