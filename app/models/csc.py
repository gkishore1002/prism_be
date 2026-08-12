from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssessmentAccessRequest(Base):
    __tablename__ = "assessment_access_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("student_profiles.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    requested_at: Mapped[str] = mapped_column(String(32))
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_granted_until: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ReportCollectionLog(Base):
    __tablename__ = "report_collection_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("student_profiles.id"))
    report_kind: Mapped[str] = mapped_column(String(16))  # assessment | overall | monthly
    report_ref: Mapped[str] = mapped_column(String(64), default="")
    collected_at: Mapped[str] = mapped_column(String(32))
    collected_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    guardian_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
