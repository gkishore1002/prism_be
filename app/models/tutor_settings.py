from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TutorDashboardSetting(Base):
    __tablename__ = "tutor_dashboard_settings"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    institution_id: Mapped[str] = mapped_column(String, ForeignKey("institutions.id"))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[str] = mapped_column(String, default="")
