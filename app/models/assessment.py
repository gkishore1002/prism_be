from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.registry import institution_fk_target


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    title: Mapped[str] = mapped_column(String(255))
    board: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(16), default="topic")
    mode: Mapped[str] = mapped_column(String(16), default="assessment")
    batch_name: Mapped[str] = mapped_column(String(128))
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    scheduled_at: Mapped[str] = mapped_column(String(32))
    available_until: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    class_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    center_ids: Mapped[str] = mapped_column(Text, default="[]")
    selected_question_ids: Mapped[str] = mapped_column(Text, default="[]")
    assigned_student_ids: Mapped[str] = mapped_column(Text, default="[]")
    created_by_tutor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chapter: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(128), nullable=True)
    question_paper_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    paper_coverage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_topics: Mapped[str | None] = mapped_column(Text, nullable=True)
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, default=False)


class AssessmentSubmission(Base):
    __tablename__ = "assessment_submissions"
    __table_args__ = (UniqueConstraint("assessment_id", "student_id", name="uq_assessment_student"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("student_profiles.id"))
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    time_spent_min: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="attended")
    answers: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    remaining_seconds: Mapped[int] = mapped_column(Integer, default=0)
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    flagged_ids: Mapped[str] = mapped_column(Text, default="[]")


class AssessmentStudentReport(Base):
    """Persisted per-assessment report for a student (summary stored once)."""

    __tablename__ = "assessment_student_reports"
    __table_args__ = (UniqueConstraint("assessment_id", "student_id", name="uq_assessment_student_report"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("student_profiles.id"))
    submission_id: Mapped[str | None] = mapped_column(
        ForeignKey("assessment_submissions.id"), nullable=True
    )
    assessment_title: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(128))
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_pct: Mapped[int] = mapped_column(Integer, default=0)
    class_avg_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_in_class: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_in_class: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_spent_min: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[str] = mapped_column(String(32))
    subject_scores: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    strong_topics: Mapped[str] = mapped_column(Text, default="[]")
    weak_topics: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_ta: Mapped[str] = mapped_column(Text, default="")
    student_message_en: Mapped[str] = mapped_column(Text, default="")
    student_message_ta: Mapped[str] = mapped_column(Text, default="")
    summary_source: Mapped[str] = mapped_column(String(16), default="rule-based")
    computed_at: Mapped[str] = mapped_column(String(32))
