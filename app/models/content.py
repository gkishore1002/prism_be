from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.registry import institution_fk_target


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    name: Mapped[str] = mapped_column(String(128))
    board: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schedule_timing: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avg_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BatchStudent(Base):
    __tablename__ = "batch_students"

    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("student_profiles.id"), primary_key=True)


class QuestionPaper(Base):
    __tablename__ = "question_papers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    name: Mapped[str] = mapped_column(String(255))
    board: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(128))
    question_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    topics: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    total_marks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="upload")
    parent_paper_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
