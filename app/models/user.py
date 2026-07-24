from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # student | tutor | admin
    roles: Mapped[str] = mapped_column(String(255), default="")  # comma-separated for multi-role
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grade_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    board_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    institution = relationship("Institution", back_populates="users")
    student_profile: Mapped["StudentProfile | None"] = relationship(
        back_populates="user", uselist=False
    )


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    board: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(64))
    batch: Mapped[str] = mapped_column(String(128), default="")
    center_id: Mapped[str] = mapped_column(String(32), default="")
    academic_year: Mapped[str] = mapped_column(String(16), default="2025-26")
    school_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    health: Mapped[int] = mapped_column(default=70)
    health_status: Mapped[str] = mapped_column(String(16), default="good")
    readiness: Mapped[int] = mapped_column(default=70)
    last_assessment: Mapped[str] = mapped_column(String(32), default="")
    critical_gaps: Mapped[int] = mapped_column(default=0)
    improving: Mapped[bool] = mapped_column(default=True)

    user: Mapped["User"] = relationship(back_populates="student_profile")
