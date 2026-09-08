"""Academic years and student enrollments (one enrollment per student per year)."""

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.registry import institution_fk_target


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (UniqueConstraint("institution_id", "name", name="uq_academic_year_inst_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()), index=True)
    name: Mapped[str] = mapped_column(String(16))  # e.g. 2025-26
    start_date: Mapped[str] = mapped_column(String(32), default="")
    end_date: Mapped[str] = mapped_column(String(32), default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    institution = relationship("Institution", foreign_keys=[institution_id])


class StudentEnrollment(Base):
    __tablename__ = "student_enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "academic_year_id", name="uq_enrollment_student_year"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id"), index=True
    )
    academic_year_id: Mapped[str] = mapped_column(
        ForeignKey("academic_years.id"), index=True
    )
    board: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("batches.id"), nullable=True, index=True
    )
    center_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("centers.id"), nullable=True, index=True
    )
    # active | completed | detained | transferred | dropped | graduated | inactive
    status: Mapped[str] = mapped_column(String(16), default="active")
    enrolled_at: Mapped[str] = mapped_column(String(32), default="")
    completed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    student = relationship("StudentProfile", foreign_keys=[student_id])
    academic_year = relationship("AcademicYear", foreign_keys=[academic_year_id])
    batch = relationship("Batch", foreign_keys=[batch_id])
    center = relationship("Center", foreign_keys=[center_id])
