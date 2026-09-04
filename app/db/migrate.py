"""Lightweight schema patches for dev databases (create_all does not alter tables)."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.tenant import (
    ensure_institution_schema_name as _ensure_institution_schema_name,
    is_multi_schema_enabled,
    patch_all_tenant_schemas,
)


def _table_columns(engine: Engine, table_name: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table_name)}


def _bool_default(engine: Engine, *, sqlite_value: str) -> str:
    if engine.dialect.name == "postgresql":
        return "TRUE" if sqlite_value == "1" else "FALSE"
    return sqlite_value


def ensure_batch_schedule_timing(engine: Engine) -> None:
    if not inspect(engine).has_table("batches"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "batches")
        if "schedule_timing" not in columns:
            conn.execute(text("ALTER TABLE batches ADD COLUMN schedule_timing VARCHAR(128)"))


def ensure_assessment_student_reports(engine: Engine) -> None:
    if inspect(engine).has_table("assessment_student_reports"):
        with engine.begin() as conn:
            columns = _table_columns(engine, "assessment_student_reports")
            if "summary_ta" not in columns:
                conn.execute(text("ALTER TABLE assessment_student_reports ADD COLUMN summary_ta TEXT NOT NULL DEFAULT ''"))
            if "student_message_en" not in columns:
                conn.execute(text("ALTER TABLE assessment_student_reports ADD COLUMN student_message_en TEXT NOT NULL DEFAULT ''"))
            if "student_message_ta" not in columns:
                conn.execute(text("ALTER TABLE assessment_student_reports ADD COLUMN student_message_ta TEXT NOT NULL DEFAULT ''"))
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
                    summary_ta TEXT NOT NULL DEFAULT '',
                    student_message_en TEXT NOT NULL DEFAULT '',
                    student_message_ta TEXT NOT NULL DEFAULT '',
                    summary_source VARCHAR(16) NOT NULL DEFAULT 'rule-based',
                    computed_at VARCHAR(32) NOT NULL,
                    UNIQUE (assessment_id, student_id)
                )
                """
            )
        )


def ensure_assessment_shuffle_questions(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("assessments", schema=schema_kw):
        return
    columns = {c["name"] for c in inspector.get_columns("assessments", schema=schema_kw)}
    if "shuffle_questions" in columns:
        return
    table = f"{schema}.assessments" if schema_kw else "assessments"
    default = _bool_default(engine, sqlite_value="0")
    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN shuffle_questions BOOLEAN NOT NULL DEFAULT {default}")
        )


def ensure_assessment_attempt_progress(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("assessment_submissions", schema=schema_kw):
        return
    columns = {c["name"] for c in inspector.get_columns("assessment_submissions", schema=schema_kw)}
    table = f"{schema}.assessment_submissions" if schema_kw else "assessment_submissions"
    with engine.begin() as conn:
        if "remaining_seconds" not in columns:
            conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN remaining_seconds INTEGER NOT NULL DEFAULT 0")
            )
        if "current_index" not in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN current_index INTEGER NOT NULL DEFAULT 0"))
        if "flagged_ids" not in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN flagged_ids TEXT NOT NULL DEFAULT '[]'"))


def ensure_assessment_available_until(engine: Engine) -> None:
    if not inspect(engine).has_table("assessments"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "assessments")
        if "available_until" not in columns:
            conn.execute(text("ALTER TABLE assessments ADD COLUMN available_until VARCHAR(32) NOT NULL DEFAULT ''"))
            conn.execute(
                text(
                    "UPDATE assessments SET available_until = scheduled_at "
                    "WHERE (available_until IS NULL OR available_until = '') AND scheduled_at != ''"
                )
            )


def ensure_student_csc_fields(engine: Engine) -> None:
    if not inspect(engine).has_table("student_profiles"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "student_profiles")
        if "disable_reason" not in columns:
            conn.execute(text("ALTER TABLE student_profiles ADD COLUMN disable_reason VARCHAR(32)"))
        if "last_csc_interaction_at" not in columns:
            conn.execute(text("ALTER TABLE student_profiles ADD COLUMN last_csc_interaction_at VARCHAR(32)"))


def ensure_assessment_access_requests(engine: Engine) -> None:
    if inspect(engine).has_table("assessment_access_requests"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE assessment_access_requests (
                    id VARCHAR(32) PRIMARY KEY,
                    assessment_id VARCHAR(32) NOT NULL REFERENCES assessments(id),
                    student_id VARCHAR(32) NOT NULL REFERENCES student_profiles(id),
                    reason TEXT NOT NULL DEFAULT '',
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    requested_at VARCHAR(32) NOT NULL,
                    reviewed_by VARCHAR(32),
                    reviewed_at VARCHAR(32),
                    review_notes TEXT,
                    access_granted_until VARCHAR(32)
                )
                """
            )
        )


def ensure_report_collection_logs(engine: Engine) -> None:
    if inspect(engine).has_table("report_collection_logs"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE report_collection_logs (
                    id VARCHAR(32) PRIMARY KEY,
                    student_id VARCHAR(32) NOT NULL REFERENCES student_profiles(id),
                    report_kind VARCHAR(16) NOT NULL,
                    report_ref VARCHAR(64) NOT NULL DEFAULT '',
                    collected_at VARCHAR(32) NOT NULL,
                    collected_by_user_id VARCHAR(32) NOT NULL REFERENCES users(id),
                    guardian_name VARCHAR(255),
                    notes TEXT
                )
                """
            )
        )


def ensure_notification_user_fields(engine: Engine) -> None:
    if not inspect(engine).has_table("notifications"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "notifications")
        if "user_id" not in columns:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN user_id VARCHAR(32) REFERENCES users(id)"))
        if "type" not in columns:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN type VARCHAR(64) NOT NULL DEFAULT 'general'"))
        if "entity_type" not in columns:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN entity_type VARCHAR(32)"))
        if "entity_id" not in columns:
            conn.execute(text("ALTER TABLE notifications ADD COLUMN entity_id VARCHAR(64)"))


def ensure_institution_is_active(engine: Engine) -> None:
    if not inspect(engine).has_table("institutions"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "institutions")
        if "is_active" not in columns:
            default = _bool_default(engine, sqlite_value="1")
            conn.execute(text(f"ALTER TABLE institutions ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT {default}"))


def ensure_institution_policies(engine: Engine) -> None:
    if not inspect(engine).has_table("institutions"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "institutions")
        if "policies_json" not in columns:
            conn.execute(text("ALTER TABLE institutions ADD COLUMN policies_json TEXT NOT NULL DEFAULT '{}'"))


def ensure_center_active(engine: Engine) -> None:
    if not inspect(engine).has_table("centers"):
        return
    with engine.begin() as conn:
        columns = _table_columns(engine, "centers")
        if "active" not in columns:
            default = _bool_default(engine, sqlite_value="1")
            conn.execute(text(f"ALTER TABLE centers ADD COLUMN active BOOLEAN NOT NULL DEFAULT {default}"))


def ensure_audit_logs(engine: Engine) -> None:
    if inspect(engine).has_table("audit_logs"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE audit_logs (
                    id VARCHAR(32) PRIMARY KEY,
                    institution_id VARCHAR(32) NOT NULL REFERENCES institutions(id),
                    actor_user_id VARCHAR(32) NOT NULL,
                    actor_role VARCHAR(16) NOT NULL,
                    action VARCHAR(64) NOT NULL,
                    entity_type VARCHAR(32) NOT NULL,
                    entity_id VARCHAR(64) NOT NULL,
                    previous_state TEXT NOT NULL DEFAULT '',
                    new_state TEXT NOT NULL DEFAULT '',
                    notes TEXT,
                    created_at VARCHAR(32) NOT NULL
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_institution_id ON audit_logs (institution_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs (created_at)"))


def ensure_student_center_index(engine: Engine) -> None:
    if not inspect(engine).has_table("student_profiles"):
        return
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_student_profiles_center_id ON student_profiles (center_id)")
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_student_profiles_institution_status ON student_profiles (status)")
        )


def ensure_student_center_null_cleanup(engine: Engine) -> None:
    """Normalize legacy empty center_id strings to NULL for FK integrity."""
    if not inspect(engine).has_table("student_profiles"):
        return
    with engine.begin() as conn:
        conn.execute(text("UPDATE student_profiles SET center_id = NULL WHERE center_id = ''"))


def ensure_user_is_owner(engine: Engine) -> None:
    if not inspect(engine).has_table("users"):
        return
    cols = _table_columns(engine, "users")
    added = False
    if "is_owner" not in cols:
        added = True
        default = _bool_default(engine, sqlite_value="0")
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE users ADD COLUMN is_owner BOOLEAN NOT NULL DEFAULT {default}"))
    if added:
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET is_owner = 1 WHERE role = 'admin'"))


def ensure_center_code(engine: Engine) -> None:
    if not inspect(engine).has_table("centers"):
        return
    cols = {c["name"] for c in inspect(engine).get_columns("centers")}
    if "code" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE centers ADD COLUMN code VARCHAR(64) NOT NULL DEFAULT ''"))
    with engine.begin() as conn:
        conn.execute(text("UPDATE centers SET code = id WHERE code = '' OR code IS NULL"))


def ensure_user_center_access(engine: Engine) -> None:
    if inspect(engine).has_table("user_center_access"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE user_center_access (
                    id VARCHAR(32) PRIMARY KEY,
                    user_id VARCHAR(32) NOT NULL REFERENCES users(id),
                    center_id VARCHAR(32) NOT NULL REFERENCES centers(id),
                    created_at VARCHAR(32) NOT NULL DEFAULT '',
                    created_by VARCHAR(32) REFERENCES users(id),
                    UNIQUE(user_id, center_id)
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_center_access_user_id ON user_center_access (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_center_access_center_id ON user_center_access (center_id)"))


def ensure_syllabus_books(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if inspector.has_table("syllabus_books", schema=schema_kw):
        return
    table = f"{schema}.syllabus_books" if schema_kw else "syllabus_books"
    inst_fk = "public.institutions(id)" if engine.dialect.name == "postgresql" else "institutions(id)"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id VARCHAR(32) PRIMARY KEY,
                    institution_id VARCHAR(32) NOT NULL REFERENCES {inst_fk},
                    board VARCHAR(64) NOT NULL,
                    grade VARCHAR(64) NOT NULL,
                    subject VARCHAR(128) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    filename VARCHAR(255) NOT NULL DEFAULT '',
                    status VARCHAR(16) NOT NULL DEFAULT 'analyzing',
                    analysis_json TEXT NOT NULL DEFAULT '{{}}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_by VARCHAR(32),
                    created_at VARCHAR(32) NOT NULL DEFAULT ''
                )
                """
            )
        )


def ensure_system_initialization(engine: Engine) -> None:
    if inspect(engine).has_table("system_initialization"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE system_initialization (
                    id VARCHAR(32) PRIMARY KEY,
                    initialized_at VARCHAR(32) NOT NULL,
                    initialized_by_user_id VARCHAR(32) NOT NULL
                )
                """
            )
        )


def ensure_assessment_termination_reason(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("assessment_submissions", schema=schema_kw):
        return
    columns = {c["name"] for c in inspector.get_columns("assessment_submissions", schema=schema_kw)}
    table = f"{schema}.assessment_submissions" if schema_kw else "assessment_submissions"
    if "termination_reason" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN termination_reason VARCHAR(64)"))


def ensure_exam_sessions(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if inspector.has_table("exam_sessions", schema=schema_kw):
        return
    table = f"{schema}.exam_sessions" if schema_kw else "exam_sessions"
    assessments_ref = f"{schema}.assessments(id)" if schema_kw else "assessments(id)"
    students_ref = f"{schema}.student_profiles(id)" if schema_kw else "student_profiles(id)"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id VARCHAR(32) PRIMARY KEY,
                    assessment_id VARCHAR(32) NOT NULL REFERENCES {assessments_ref},
                    student_id VARCHAR(32) NOT NULL REFERENCES {students_ref},
                    device_id VARCHAR(64) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    started_at VARCHAR(32) NOT NULL,
                    last_heartbeat_at VARCHAR(32) NOT NULL DEFAULT '',
                    ended_at VARCHAR(32),
                    user_agent TEXT,
                    ip_address VARCHAR(64)
                )
                """
            )
        )
        idx = f"{schema}." if schema_kw else ""
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_exam_sessions_assessment_student "
                f"ON {idx}exam_sessions (assessment_id, student_id)"
            )
        )


def ensure_exam_violations(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if inspector.has_table("exam_violations", schema=schema_kw):
        return
    table = f"{schema}.exam_violations" if schema_kw else "exam_violations"
    sessions_ref = f"{schema}.exam_sessions(id)" if schema_kw else "exam_sessions(id)"
    assessments_ref = f"{schema}.assessments(id)" if schema_kw else "assessments(id)"
    students_ref = f"{schema}.student_profiles(id)" if schema_kw else "student_profiles(id)"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id VARCHAR(32) PRIMARY KEY,
                    session_id VARCHAR(32) NOT NULL REFERENCES {sessions_ref},
                    assessment_id VARCHAR(32) NOT NULL REFERENCES {assessments_ref},
                    student_id VARCHAR(32) NOT NULL REFERENCES {students_ref},
                    violation_type VARCHAR(32) NOT NULL,
                    occurred_at VARCHAR(32) NOT NULL,
                    user_agent TEXT
                )
                """
            )
        )
        idx = f"{schema}." if schema_kw else ""
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_exam_violations_assessment_student "
                f"ON {idx}exam_violations (assessment_id, student_id)"
            )
        )


def run_migrations(engine: Engine) -> None:
    ensure_batch_schedule_timing(engine)
    ensure_assessment_student_reports(engine)
    ensure_assessment_available_until(engine)
    ensure_assessment_shuffle_questions(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_assessment_shuffle_questions)
    ensure_assessment_attempt_progress(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_assessment_attempt_progress)
    ensure_assessment_termination_reason(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_assessment_termination_reason)
    ensure_exam_sessions(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_exam_sessions)
    ensure_exam_violations(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_exam_violations)
    ensure_student_csc_fields(engine)
    ensure_assessment_access_requests(engine)
    ensure_report_collection_logs(engine)
    ensure_notification_user_fields(engine)
    ensure_institution_is_active(engine)
    ensure_institution_policies(engine)
    ensure_center_active(engine)
    ensure_audit_logs(engine)
    ensure_student_center_index(engine)
    ensure_student_center_null_cleanup(engine)
    ensure_user_is_owner(engine)
    ensure_center_code(engine)
    ensure_user_center_access(engine)
    ensure_system_initialization(engine)
    ensure_syllabus_books(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_syllabus_books)
    _ensure_institution_schema_name(engine)
