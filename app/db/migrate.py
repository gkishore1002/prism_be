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


def ensure_assessment_created_at(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("assessments", schema=schema_kw):
        return
    columns = {c["name"] for c in inspector.get_columns("assessments", schema=schema_kw)}
    if "created_at" in columns:
        return
    table = f"{schema}.assessments" if schema_kw else "assessments"
    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''")
        )
        # Best-effort backfill so existing rows sort near their schedule time
        conn.execute(
            text(
                f"UPDATE {table} SET created_at = scheduled_at "
                "WHERE (created_at IS NULL OR created_at = '') AND scheduled_at != ''"
            )
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


def ensure_student_overall_reports(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if inspector.has_table("student_overall_reports", schema=schema_kw):
        return
    students_ref = f"{schema}.student_profiles(id)" if schema_kw else "student_profiles(id)"
    table = f"{schema}.student_overall_reports" if schema_kw else "student_overall_reports"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    student_id VARCHAR(32) PRIMARY KEY REFERENCES {students_ref},
                    summary TEXT NOT NULL DEFAULT '',
                    summary_ta TEXT NOT NULL DEFAULT '',
                    summary_source VARCHAR(16) NOT NULL DEFAULT 'rule-based',
                    computed_at VARCHAR(32) NOT NULL DEFAULT ''
                )
                """
            )
        )


def _inst_ref(engine: Engine) -> str:
    if engine.dialect.name == "postgresql":
        return "public.institutions(id)"
    return "institutions(id)"


def ensure_academic_years(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    table = f"{schema}.academic_years" if schema_kw else "academic_years"
    if inspector.has_table("academic_years", schema=schema_kw):
        return
    bool_default = _bool_default(engine, sqlite_value="0")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id VARCHAR(32) PRIMARY KEY,
                    institution_id VARCHAR(32) NOT NULL REFERENCES {_inst_ref(engine)},
                    name VARCHAR(16) NOT NULL,
                    start_date VARCHAR(32) NOT NULL DEFAULT '',
                    end_date VARCHAR(32) NOT NULL DEFAULT '',
                    is_current BOOLEAN NOT NULL DEFAULT {bool_default},
                    UNIQUE (institution_id, name)
                )
                """
            )
        )


def ensure_student_enrollments(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if inspector.has_table("student_enrollments", schema=schema_kw):
        return
    prefix = f"{schema}." if schema_kw else ""
    table = f"{prefix}student_enrollments"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id VARCHAR(32) PRIMARY KEY,
                    student_id VARCHAR(32) NOT NULL REFERENCES {prefix}student_profiles(id),
                    academic_year_id VARCHAR(32) NOT NULL REFERENCES {prefix}academic_years(id),
                    board VARCHAR(64) NOT NULL,
                    grade VARCHAR(64) NOT NULL,
                    batch_id VARCHAR(32) REFERENCES {prefix}batches(id),
                    center_id VARCHAR(32) REFERENCES {prefix}centers(id),
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    enrolled_at VARCHAR(32) NOT NULL DEFAULT '',
                    completed_at VARCHAR(32),
                    UNIQUE (student_id, academic_year_id)
                )
                """
            )
        )


def ensure_staff_assignments(engine: Engine, schema: str | None = None) -> None:
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if inspector.has_table("staff_assignments", schema=schema_kw):
        return
    prefix = f"{schema}." if schema_kw else ""
    table = f"{prefix}staff_assignments"
    inst_fk = "public.institutions(id)" if engine.dialect.name == "postgresql" else f"{prefix}institutions(id)"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id VARCHAR(32) PRIMARY KEY,
                    staff_id VARCHAR(32) NOT NULL REFERENCES {prefix}users(id),
                    institution_id VARCHAR(32) NOT NULL REFERENCES {inst_fk},
                    academic_year_id VARCHAR(32) NOT NULL REFERENCES {prefix}academic_years(id),
                    center_id VARCHAR(32) NOT NULL REFERENCES {prefix}centers(id),
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    start_date VARCHAR(32) NOT NULL DEFAULT '',
                    end_date VARCHAR(32),
                    UNIQUE (staff_id, academic_year_id)
                )
                """
            )
        )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_staff_assignments_staff_id ON {table} (staff_id)"
            )
        )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_staff_assignments_year_id ON {table} (academic_year_id)"
            )
        )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_staff_assignments_center_id ON {table} (center_id)"
            )
        )


def backfill_staff_assignments(engine: Engine, schema: str | None = None) -> None:
    """Create current-year staff_assignments from user_center_access (idempotent)."""
    import uuid
    from datetime import date

    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("staff_assignments", schema=schema_kw):
        return
    if not inspector.has_table("academic_years", schema=schema_kw):
        return
    if not inspector.has_table("user_center_access", schema=schema_kw):
        return
    if not inspector.has_table("users", schema=schema_kw):
        return

    prefix = f"{schema}." if schema_kw else ""
    today = date.today().isoformat()

    def nid() -> str:
        return f"sas-{uuid.uuid4().hex[:10]}"

    with engine.begin() as conn:
        years = conn.execute(
            text(
                f"""
                SELECT id, institution_id FROM {prefix}academic_years
                WHERE is_current = true
                """
            )
        ).fetchall()
        # Fallback: latest year name per institution if none marked current
        if not years:
            years = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (institution_id) id, institution_id
                    FROM {prefix}academic_years
                    ORDER BY institution_id, name DESC
                    """
                )
            ).fetchall() if engine.dialect.name == "postgresql" else []
            if not years:
                # SQLite: pick max name per institution
                inst_ids = [
                    r[0]
                    for r in conn.execute(
                        text(f"SELECT DISTINCT institution_id FROM {prefix}academic_years")
                    ).fetchall()
                ]
                years = []
                for inst_id in inst_ids:
                    row = conn.execute(
                        text(
                            f"""
                            SELECT id, institution_id FROM {prefix}academic_years
                            WHERE institution_id = :iid
                            ORDER BY name DESC
                            LIMIT 1
                            """
                        ),
                        {"iid": inst_id},
                    ).fetchone()
                    if row:
                        years.append(row)

        year_by_inst = {r[1]: r[0] for r in years}
        if not year_by_inst:
            return

        # Staff = users with admin or tutor in role/roles
        staff_rows = conn.execute(
            text(
                f"""
                SELECT id, institution_id, role, roles
                FROM {prefix}users
                WHERE role IN ('admin', 'tutor')
                   OR (roles IS NOT NULL AND (
                        roles LIKE '%admin%' OR roles LIKE '%tutor%'
                   ))
                """
            )
        ).fetchall()

        for staff_id, institution_id, _role, _roles in staff_rows:
            year_id = year_by_inst.get(institution_id)
            if not year_id:
                continue
            exists = conn.execute(
                text(
                    f"""
                    SELECT 1 FROM {prefix}staff_assignments
                    WHERE staff_id = :sid AND academic_year_id = :yid
                    LIMIT 1
                    """
                ),
                {"sid": staff_id, "yid": year_id},
            ).fetchone()
            if exists:
                continue
            center = conn.execute(
                text(
                    f"""
                    SELECT center_id FROM {prefix}user_center_access
                    WHERE user_id = :uid
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """
                ),
                {"uid": staff_id},
            ).fetchone()
            if not center:
                continue
            conn.execute(
                text(
                    f"""
                    INSERT INTO {prefix}staff_assignments
                    (id, staff_id, institution_id, academic_year_id, center_id, status, start_date, end_date)
                    VALUES (:id, :sid, :iid, :yid, :cid, 'active', :start, NULL)
                    """
                ),
                {
                    "id": nid(),
                    "sid": staff_id,
                    "iid": institution_id,
                    "yid": year_id,
                    "cid": center[0],
                    "start": today,
                },
            )


def ensure_enrollment_year_columns(engine: Engine, schema: str | None = None) -> None:
    """Add academic_year_id / enrollment_id / current_enrollment_id columns."""
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    prefix = f"{schema}." if schema_kw else ""

    def _cols(table: str) -> set[str]:
        if not inspector.has_table(table, schema=schema_kw):
            return set()
        return {c["name"] for c in inspector.get_columns(table, schema=schema_kw)}

    with engine.begin() as conn:
        if "academic_year_id" not in _cols("batches") and inspector.has_table("batches", schema=schema_kw):
            conn.execute(
                text(f"ALTER TABLE {prefix}batches ADD COLUMN academic_year_id VARCHAR(32)")
            )
        if "academic_year_id" not in _cols("assessments") and inspector.has_table(
            "assessments", schema=schema_kw
        ):
            conn.execute(
                text(f"ALTER TABLE {prefix}assessments ADD COLUMN academic_year_id VARCHAR(32)")
            )
        if "enrollment_id" not in _cols("assessment_submissions") and inspector.has_table(
            "assessment_submissions", schema=schema_kw
        ):
            conn.execute(
                text(f"ALTER TABLE {prefix}assessment_submissions ADD COLUMN enrollment_id VARCHAR(32)")
            )
        if "current_enrollment_id" not in _cols("student_profiles") and inspector.has_table(
            "student_profiles", schema=schema_kw
        ):
            conn.execute(
                text(
                    f"ALTER TABLE {prefix}student_profiles ADD COLUMN current_enrollment_id VARCHAR(32)"
                )
            )


def backfill_academic_enrollments(engine: Engine, schema: str | None = None) -> None:
    """Create years + enrollments from existing student_profiles / batches / assessments."""
    import uuid
    from datetime import datetime, timezone

    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("academic_years", schema=schema_kw):
        return
    if not inspector.has_table("student_profiles", schema=schema_kw):
        return

    prefix = f"{schema}." if schema_kw else ""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def nid(pfx: str) -> str:
        return f"{pfx}-{uuid.uuid4().hex[:10]}"

    with engine.begin() as conn:
        # Institution IDs from users joined to profiles
        inst_rows = conn.execute(
            text(
                f"""
                SELECT DISTINCT u.institution_id
                FROM {prefix}users u
                INNER JOIN {prefix}student_profiles sp ON sp.user_id = u.id
                """
            )
        ).fetchall()
        # Also institutions that have batches/centers even without students
        extra = conn.execute(
            text(f"SELECT DISTINCT institution_id FROM {prefix}batches")
        ).fetchall() if inspector.has_table("batches", schema=schema_kw) else []
        institution_ids = {r[0] for r in inst_rows} | {r[0] for r in extra}
        if not institution_ids and inspector.has_table("centers", schema=schema_kw):
            institution_ids = {
                r[0]
                for r in conn.execute(text(f"SELECT DISTINCT institution_id FROM {prefix}centers")).fetchall()
            }

        for institution_id in institution_ids:
            # Collect year names from profiles
            year_names = [
                r[0]
                for r in conn.execute(
                    text(
                        f"""
                        SELECT DISTINCT sp.academic_year
                        FROM {prefix}student_profiles sp
                        INNER JOIN {prefix}users u ON u.id = sp.user_id
                        WHERE u.institution_id = :iid AND sp.academic_year IS NOT NULL AND sp.academic_year != ''
                        """
                    ),
                    {"iid": institution_id},
                ).fetchall()
            ]
            if not year_names:
                year_names = ["2025-26"]

            existing_years = {
                r[0]: r[1]
                for r in conn.execute(
                    text(
                        f"SELECT name, id FROM {prefix}academic_years WHERE institution_id = :iid"
                    ),
                    {"iid": institution_id},
                ).fetchall()
            }
            year_id_by_name: dict[str, str] = dict(existing_years)
            for name in sorted(set(year_names)):
                if name not in year_id_by_name:
                    yid = nid("ay")
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO {prefix}academic_years
                            (id, institution_id, name, start_date, end_date, is_current)
                            VALUES (:id, :iid, :name, '', '', :cur)
                            """
                        ),
                        {
                            "id": yid,
                            "iid": institution_id,
                            "name": name,
                            "cur": False,
                        },
                    )
                    year_id_by_name[name] = yid

            # Ensure one current year
            current = conn.execute(
                text(
                    f"""
                    SELECT id FROM {prefix}academic_years
                    WHERE institution_id = :iid AND is_current = {_bool_default(engine, sqlite_value='1')}
                    LIMIT 1
                    """
                ),
                {"iid": institution_id},
            ).fetchone()
            if not current:
                # Prefer lexicographically latest name
                best_name = sorted(year_id_by_name.keys())[-1]
                conn.execute(
                    text(
                        f"""
                        UPDATE {prefix}academic_years SET is_current = {_bool_default(engine, sqlite_value='1')}
                        WHERE id = :id
                        """
                    ),
                    {"id": year_id_by_name[best_name]},
                )
                current_year_id = year_id_by_name[best_name]
            else:
                current_year_id = current[0]

            # Stamp batches missing academic_year_id
            if inspector.has_table("batches", schema=schema_kw):
                conn.execute(
                    text(
                        f"""
                        UPDATE {prefix}batches
                        SET academic_year_id = :yid
                        WHERE institution_id = :iid
                          AND (academic_year_id IS NULL OR academic_year_id = '')
                        """
                    ),
                    {"yid": current_year_id, "iid": institution_id},
                )

            # Stamp assessments
            if inspector.has_table("assessments", schema=schema_kw):
                conn.execute(
                    text(
                        f"""
                        UPDATE {prefix}assessments
                        SET academic_year_id = :yid
                        WHERE institution_id = :iid
                          AND (academic_year_id IS NULL OR academic_year_id = '')
                        """
                    ),
                    {"yid": current_year_id, "iid": institution_id},
                )

            # Create enrollments for profiles missing current_enrollment_id
            profiles = conn.execute(
                text(
                    f"""
                    SELECT sp.id, sp.board, sp.grade, sp.center_id, sp.academic_year, sp.status,
                           sp.current_enrollment_id
                    FROM {prefix}student_profiles sp
                    INNER JOIN {prefix}users u ON u.id = sp.user_id
                    WHERE u.institution_id = :iid
                    """
                ),
                {"iid": institution_id},
            ).fetchall()

            for row in profiles:
                (
                    student_id,
                    board,
                    grade,
                    center_id,
                    academic_year_name,
                    profile_status,
                    current_enrollment_id,
                ) = row
                if current_enrollment_id:
                    continue
                yname = academic_year_name or "2025-26"
                yid = year_id_by_name.get(yname) or current_year_id
                # Resolve batch_id from batch_students
                batch_id = None
                if inspector.has_table("batch_students", schema=schema_kw):
                    brow = conn.execute(
                        text(
                            f"""
                            SELECT bs.batch_id FROM {prefix}batch_students bs
                            INNER JOIN {prefix}batches b ON b.id = bs.batch_id
                            WHERE bs.student_id = :sid AND b.institution_id = :iid
                            LIMIT 1
                            """
                        ),
                        {"sid": student_id, "iid": institution_id},
                    ).fetchone()
                    if brow:
                        batch_id = brow[0]
                enr_status = "active" if (profile_status or "active") == "active" else "inactive"
                # Skip if enrollment already exists for year
                exists = conn.execute(
                    text(
                        f"""
                        SELECT id FROM {prefix}student_enrollments
                        WHERE student_id = :sid AND academic_year_id = :yid
                        """
                    ),
                    {"sid": student_id, "yid": yid},
                ).fetchone()
                if exists:
                    enr_id = exists[0]
                else:
                    enr_id = nid("enr")
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO {prefix}student_enrollments
                            (id, student_id, academic_year_id, board, grade, batch_id, center_id,
                             status, enrolled_at, completed_at)
                            VALUES (:id, :sid, :yid, :board, :grade, :batch_id, :center_id,
                                    :status, :enrolled_at, NULL)
                            """
                        ),
                        {
                            "id": enr_id,
                            "sid": student_id,
                            "yid": yid,
                            "board": board or "",
                            "grade": grade or "",
                            "batch_id": batch_id,
                            "center_id": center_id or None,
                            "status": enr_status,
                            "enrolled_at": now,
                        },
                    )
                conn.execute(
                    text(
                        f"""
                        UPDATE {prefix}student_profiles
                        SET current_enrollment_id = :enr
                        WHERE id = :sid
                        """
                    ),
                    {"enr": enr_id, "sid": student_id},
                )

            # Backfill submission enrollment_id
            if inspector.has_table("assessment_submissions", schema=schema_kw):
                conn.execute(
                    text(
                        f"""
                        UPDATE {prefix}assessment_submissions
                        SET enrollment_id = (
                            SELECT sp.current_enrollment_id
                            FROM {prefix}student_profiles sp
                            WHERE sp.id = {prefix}assessment_submissions.student_id
                        )
                        WHERE enrollment_id IS NULL
                        """
                    )
                )


def ensure_question_image_columns(engine: Engine, schema: str | None = None) -> None:
    """Add stem/option image key columns on questions."""
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    if not inspector.has_table("questions", schema=schema_kw):
        return
    columns = {c["name"] for c in inspector.get_columns("questions", schema=schema_kw)}
    table = f"{schema}.questions" if schema_kw else "questions"
    additions = [
        "text_image_key",
        "option_a_image_key",
        "option_b_image_key",
        "option_c_image_key",
        "option_d_image_key",
    ]
    with engine.begin() as conn:
        for col in additions:
            if col not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR(255)"))


def ensure_multi_subjects_columns(engine: Engine, schema: str | None = None) -> None:
    """Add subjects JSON columns on batches, question_papers, and assessments."""
    inspector = inspect(engine)
    schema_kw = schema if schema and schema != "public" else None
    targets = ("batches", "question_papers", "assessments")
    with engine.begin() as conn:
        for table_name in targets:
            if not inspector.has_table(table_name, schema=schema_kw):
                continue
            columns = {c["name"] for c in inspector.get_columns(table_name, schema=schema_kw)}
            table = f"{schema}.{table_name}" if schema_kw else table_name
            if "subjects" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN subjects TEXT NOT NULL DEFAULT '[]'"))
            # Backfill from legacy single subject when subjects is still empty.
            conn.execute(
                text(
                    f"UPDATE {table} SET subjects = "
                    "'[\"' || REPLACE(REPLACE(TRIM(subject), '\\\\', '\\\\\\\\'), '\"', '\\\\\"') || '\"]' "
                    "WHERE (subjects IS NULL OR subjects = '' OR subjects = '[]') "
                    "AND subject IS NOT NULL AND TRIM(subject) <> ''"
                )
            )


def run_migrations(engine: Engine) -> None:
    ensure_batch_schedule_timing(engine)
    ensure_assessment_student_reports(engine)
    ensure_student_overall_reports(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_student_overall_reports)
    ensure_assessment_available_until(engine)
    ensure_assessment_shuffle_questions(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_assessment_shuffle_questions)
    ensure_assessment_created_at(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_assessment_created_at)
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

    # Academic years + enrollments
    ensure_academic_years(engine)
    ensure_enrollment_year_columns(engine)
    ensure_student_enrollments(engine)
    backfill_academic_enrollments(engine)
    ensure_staff_assignments(engine)
    backfill_staff_assignments(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_academic_years)
        patch_all_tenant_schemas(engine, ensure_enrollment_year_columns)
        patch_all_tenant_schemas(engine, ensure_student_enrollments)
        patch_all_tenant_schemas(engine, backfill_academic_enrollments)
        patch_all_tenant_schemas(engine, ensure_staff_assignments)
        patch_all_tenant_schemas(engine, backfill_staff_assignments)

    ensure_question_image_columns(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_question_image_columns)

    ensure_multi_subjects_columns(engine)
    if is_multi_schema_enabled():
        patch_all_tenant_schemas(engine, ensure_multi_subjects_columns)

    _ensure_institution_schema_name(engine)
