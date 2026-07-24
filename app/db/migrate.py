"""Lightweight SQLite schema patches for dev databases (create_all does not alter tables)."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_batch_schedule_timing(engine: Engine) -> None:
    if not inspect(engine).has_table("batches"):
        return
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(batches)"))}
        if "schedule_timing" not in columns:
            conn.execute(text("ALTER TABLE batches ADD COLUMN schedule_timing VARCHAR(128)"))


def ensure_assessment_student_reports(engine: Engine) -> None:
    if inspect(engine).has_table("assessment_student_reports"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE assessment_student_reports (
                    id VARCHAR(32) PRIMARY KEY,
                    assessment_id VARCHAR(32) NOT NULL REFERENCES assessments(id),
                    student_id VARCHAR(32) NOT NULL REFERENCES student_profiles(id),
                    submission_id VARCHAR(32) REFERENCES assessment_submissions(id),
                    assessment_title VARCHAR(255) NOT NULL,
                    subject VARCHAR(128) NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    max_score INTEGER NOT NULL DEFAULT 0,
                    accuracy_pct INTEGER NOT NULL DEFAULT 0,
                    class_avg_pct INTEGER,
                    rank_in_class INTEGER,
                    total_in_class INTEGER,
                    time_spent_min INTEGER NOT NULL DEFAULT 0,
                    submitted_at VARCHAR(32) NOT NULL,
                    subject_scores TEXT NOT NULL DEFAULT '[]',
                    strong_topics TEXT NOT NULL DEFAULT '[]',
                    weak_topics TEXT NOT NULL DEFAULT '[]',
                    summary TEXT NOT NULL DEFAULT '',
                    summary_source VARCHAR(16) NOT NULL DEFAULT 'rule-based',
                    computed_at VARCHAR(32) NOT NULL,
                    UNIQUE (assessment_id, student_id)
                )
                """
            )
        )


def run_migrations(engine: Engine) -> None:
    ensure_batch_schedule_timing(engine)
    ensure_assessment_student_reports(engine)
