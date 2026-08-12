from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserCenterAccess(Base):
    """Maps staff users to one or more branches (centers) within their organization."""

    __tablename__ = "user_center_access"
    __table_args__ = (UniqueConstraint("user_id", "center_id", name="uq_user_center_access"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    center_id: Mapped[str] = mapped_column(String(32), ForeignKey("centers.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(32), default="")
    created_by: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id], back_populates="center_access")
    center = relationship("Center")
