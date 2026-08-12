from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.registry import institution_fk_target


class MarksEntry(Base):
    """Offline / manual marks recorded by tutors (spreadsheet or CSV upload)."""

    __tablename__ = "marks_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("student_profiles.id"))
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    batch_name: Mapped[str] = mapped_column(String(128))
    assessment_title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str] = mapped_column(String(128))
    max_marks: Mapped[int] = mapped_column(Integer)
    scored_marks: Mapped[float] = mapped_column(Float)
    percentage: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))  # manual | upload
    conducted_on: Mapped[str] = mapped_column(String(16))
    saved_at: Mapped[str] = mapped_column(String(32))
    created_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
