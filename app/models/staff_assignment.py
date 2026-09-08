"""Year-scoped staff academic placements (one assignment per staff per academic year)."""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.registry import institution_fk_target


class StaffAssignment(Base):
    """Academic placement for a staff user in one academic year.

    Portal/branch visibility remains on UserCenterAccess — this row is academic context only.
    """

    __tablename__ = "staff_assignments"
    __table_args__ = (
        UniqueConstraint("staff_id", "academic_year_id", name="uq_staff_assignment_staff_year"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    staff_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()), index=True)
    academic_year_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("academic_years.id"), index=True
    )
    center_id: Mapped[str] = mapped_column(String(32), ForeignKey("centers.id"), index=True)
    # active | completed | transferred | inactive
    status: Mapped[str] = mapped_column(String(16), default="active")
    start_date: Mapped[str] = mapped_column(String(32), default="")
    end_date: Mapped[str | None] = mapped_column(String(32), nullable=True)

    staff = relationship("User", foreign_keys=[staff_id])
    academic_year = relationship("AcademicYear", foreign_keys=[academic_year_id])
    center = relationship("Center", foreign_keys=[center_id])
