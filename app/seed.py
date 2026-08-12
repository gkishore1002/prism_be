"""Seed the database with demo data matching prism_fe mocks."""

from datetime import date, datetime, timedelta, timezone

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
import app.models  # noqa: F401
from app.models.academic import Board, Chapter, Grade, Question, Subject, Topic
from app.models.assessment import Assessment, AssessmentSubmission
from app.models.content import Batch, BatchStudent, QuestionPaper
from app.models.csc import ReportCollectionLog
from app.models.branch_access import UserCenterAccess
from app.models.institution import Center, Institution
from app.models.notification import Notification
from app.models.user import StudentProfile, User
from app.utils import to_json_list

INST_ID = "inst-1"
CSC_TEST_STUDENT_ID = "stu-csc-test"
CSC_TEST_EMAIL = "csc.lapsed@brightpath.edu"
CSC_LAPSED_DAYS = 95


def ensure_csc_inactivity_demo(db) -> None:
    """Upsert a student whose last CSC visit was 95+ days ago (login blocked as student)."""
    institution = db.get(Institution, INST_ID)
    if not institution:
        return

    pwd = hash_password(settings.demo_password)
    lapsed_on = (date.today() - timedelta(days=CSC_LAPSED_DAYS)).isoformat()
    collected_at = f"{lapsed_on}T10:00"

    user = db.get(User, CSC_TEST_STUDENT_ID)
    if not user:
        db.add(
            User(
                id=CSC_TEST_STUDENT_ID,
                institution_id=INST_ID,
                name="CSC Lapsed Test",
                email=CSC_TEST_EMAIL,
                password_hash=pwd,
                role="student",
                grade_id="g8",
                board_id="cbse",
            )
        )

    profile = db.get(StudentProfile, CSC_TEST_STUDENT_ID)
    if not profile:
        db.add(
            StudentProfile(
                id=CSC_TEST_STUDENT_ID,
                user_id=CSC_TEST_STUDENT_ID,
                board="CBSE",
                grade="Grade 8",
                batch="Batch A",
                center_id="c1",
                health=62,
                health_status="fair",
                readiness=58,
                last_assessment="2025-10-01",
                critical_gaps=2,
                improving=False,
                status="active",
                disable_reason=None,
                last_csc_interaction_at=lapsed_on,
            )
        )
    else:
        profile.status = "active"
        profile.disable_reason = None
        profile.last_csc_interaction_at = lapsed_on

    batch = db.get(Batch, "batch-a")
    if batch:
        existing = (
            db.query(BatchStudent)
            .filter(
                BatchStudent.batch_id == "batch-a",
                BatchStudent.student_id == CSC_TEST_STUDENT_ID,
            )
            .first()
        )
        if not existing:
            db.add(BatchStudent(batch_id="batch-a", student_id=CSC_TEST_STUDENT_ID))

    log = db.get(ReportCollectionLog, "rcl-csc-test")
    if not log:
        db.add(
            ReportCollectionLog(
                id="rcl-csc-test",
                student_id=CSC_TEST_STUDENT_ID,
                report_kind="monthly",
                report_ref="seed-csc-lapsed",
                collected_at=collected_at,
                collected_by_user_id="tut-1",
                guardian_name="Test Guardian",
                notes="Seed data — CSC visit over 90 days ago for login-disable testing.",
            )
        )

    db.commit()
    print(
        f"CSC inactivity test student ready:\n"
        f"  Email: {CSC_TEST_EMAIL}\n"
        f"  Password: {settings.demo_password}\n"
        f"  Institution: {institution.code}\n"
        f"  Last CSC visit: {lapsed_on} ({CSC_LAPSED_DAYS} days ago)\n"
        f"  Expected: student login blocked with CSC inactivity message."
    )


def ensure_branch_access_demo(db) -> None:
    """Upsert multi-branch admin demo users on existing installations."""
    institution = db.get(Institution, INST_ID)
    if not institution:
        return
    pwd = hash_password(settings.demo_password)
    owner = db.get(User, "adm-1")
    if owner and owner.role == "admin":
        owner.is_owner = True

    for uid, name, email, centers in [
        ("adm-ravi", "Ravi Admin", "ravi@brightpath.edu", ["c1", "c2"]),
        ("adm-priya", "Priya Admin", "priya.admin@brightpath.edu", ["c3"]),
    ]:
        user = db.get(User, uid)
        if not user:
            db.add(
                User(
                    id=uid,
                    institution_id=INST_ID,
                    name=name,
                    email=email,
                    password_hash=pwd,
                    role="admin",
                    roles="admin",
                    is_owner=False,
                )
            )
        for center_id in centers:
            exists = (
                db.query(UserCenterAccess)
                .filter(UserCenterAccess.user_id == uid, UserCenterAccess.center_id == center_id)
                .first()
            )
            if not exists:
                db.add(
                    UserCenterAccess(
                        id=f"uca-{uid}-{center_id}",
                        user_id=uid,
                        center_id=center_id,
                        created_at="2026-01-01T00:00:00+00:00",
                        created_by="adm-1",
                    )
                )
    db.commit()


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.get(Institution, INST_ID):
            ensure_csc_inactivity_demo(db)
            ensure_branch_access_demo(db)
            print("Database already seeded — CSC test student and branch admins ensured.")
            return

        pwd = hash_password(settings.demo_password)

        db.add(
            Institution(
                id=INST_ID,
                name="BrightPath Academy",
                code="BRIGHTPATH",
                type="coaching",
                board_ids=to_json_list(["cbse"]),
            )
        )

        for c in [
            ("c1", "BrightPath · Andheri (HQ)", "Mumbai", "CHENNAI", 980),
            ("c2", "BrightPath · Borivali", "Borivali", "BANGALORE", 620),
            ("c3", "BrightPath · Thane", "Thane", "COIMBATORE", 410),
        ]:
            db.add(Center(id=c[0], institution_id=INST_ID, name=c[1], city=c[2], code=c[3], student_count=c[4]))

        users = [
            User(id="stu-1", institution_id=INST_ID, name="Arjun Mehta", email="arjun@brightpath.edu", password_hash=pwd, role="student", roles="student", grade_id="g8", board_id="cbse"),
            User(id="tut-1", institution_id=INST_ID, name="Priya Sharma", email="priya@brightpath.edu", password_hash=pwd, role="tutor", roles="tutor"),
            User(id="adm-1", institution_id=INST_ID, name="Rajesh Kumar", email="rajesh@brightpath.edu", password_hash=pwd, role="admin", roles="admin", is_owner=True),
            User(id="adm-ravi", institution_id=INST_ID, name="Ravi Admin", email="ravi@brightpath.edu", password_hash=pwd, role="admin", roles="admin", is_owner=False),
            User(id="adm-priya", institution_id=INST_ID, name="Priya Admin", email="priya.admin@brightpath.edu", password_hash=pwd, role="admin", roles="admin", is_owner=False),
            User(id="demo-1", institution_id=INST_ID, name="Demo User", email="demo@prism.app", password_hash=pwd, role="student", roles="student,tutor,admin"),
        ]
        db.add_all(users)

        db.add_all([
            UserCenterAccess(id="uca-ravi-c1", user_id="adm-ravi", center_id="c1", created_at="2026-01-01T00:00:00+00:00", created_by="adm-1"),
            UserCenterAccess(id="uca-ravi-c2", user_id="adm-ravi", center_id="c2", created_at="2026-01-01T00:00:00+00:00", created_by="adm-1"),
            UserCenterAccess(id="uca-priya-c3", user_id="adm-priya", center_id="c3", created_at="2026-01-01T00:00:00+00:00", created_by="adm-1"),
        ])

        extra_students = [
            ("stu-2", "Sneha Patel", "sneha@brightpath.edu"),
            ("stu-3", "Rohan Das", "rohan@brightpath.edu"),
            ("stu-4", "Kavya Iyer", "kavya@brightpath.edu"),
            ("stu-5", "Aditya Nair", "aditya@brightpath.edu"),
            ("stu-6", "Priya Menon", "priya.m@brightpath.edu"),
            ("stu-7", "Vikram Singh", "vikram@brightpath.edu"),
            ("stu-8", "Ananya Reddy", "ananya@brightpath.edu"),
            ("stu-9", "Rahul Kapoor", "rahul@brightpath.edu"),
            ("stu-10", "Meera Joshi", "meera@brightpath.edu"),
            ("stu-11", "Dev Sharma", "dev@brightpath.edu"),
            ("stu-12", "Isha Gupta", "isha@brightpath.edu"),
        ]
        for sid, name, email in extra_students:
            db.add(User(id=sid, institution_id=INST_ID, name=name, email=email, password_hash=pwd, role="student"))

        students = [
            StudentProfile(id="stu-1", user_id="stu-1", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=72, health_status="good", readiness=68, last_assessment="2026-06-18", critical_gaps=1, improving=True),
            StudentProfile(id="stu-2", user_id="stu-2", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=85, health_status="excellent", readiness=88, last_assessment="2026-06-17", critical_gaps=0, improving=True),
            StudentProfile(id="stu-3", user_id="stu-3", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c2", health=54, health_status="weak", readiness=48, last_assessment="2026-06-16", critical_gaps=3, improving=False),
            StudentProfile(id="stu-4", user_id="stu-4", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=78, health_status="good", readiness=76, last_assessment="2026-07-08", critical_gaps=1, improving=True),
            StudentProfile(id="stu-5", user_id="stu-5", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=82, health_status="good", readiness=80, last_assessment="2026-07-09", critical_gaps=0, improving=True),
            StudentProfile(id="stu-6", user_id="stu-6", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=91, health_status="excellent", readiness=89, last_assessment="2026-07-10", critical_gaps=0, improving=True),
            StudentProfile(id="stu-7", user_id="stu-7", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c2", health=67, health_status="fair", readiness=62, last_assessment="2026-07-07", critical_gaps=2, improving=False),
            StudentProfile(id="stu-8", user_id="stu-8", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=88, health_status="excellent", readiness=86, last_assessment="2026-07-10", critical_gaps=0, improving=True),
            StudentProfile(id="stu-9", user_id="stu-9", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c2", health=59, health_status="weak", readiness=55, last_assessment="2026-07-06", critical_gaps=2, improving=False),
            StudentProfile(id="stu-10", user_id="stu-10", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=74, health_status="good", readiness=71, last_assessment="2026-07-08", critical_gaps=1, improving=True),
            StudentProfile(id="stu-11", user_id="stu-11", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=71, health_status="good", readiness=69, last_assessment="2026-07-09", critical_gaps=1, improving=True),
            StudentProfile(id="stu-12", user_id="stu-12", board="CBSE", grade="Grade 8", batch="Batch A", center_id="c1", health=83, health_status="good", readiness=81, last_assessment="2026-07-10", critical_gaps=0, improving=True),
        ]
        db.add_all(students)

        board = Board(id="cbse", institution_id=INST_ID, name="CBSE", code="CBSE")
        grade = Grade(id="g8", board_id="cbse", name="Grade 8", level=8)
        math = Subject(id="math", grade_id="g8", name="Mathematics", icon="calculator", color="#6366f1")
        science = Subject(id="science", grade_id="g8", name="Science", icon="flask", color="#10b981")
        ch_algebra = Chapter(id="ch-algebra", subject_id="math", name="Algebra", order=1)
        ch_geometry = Chapter(id="ch-geometry", subject_id="math", name="Geometry", order=2)
        ch_physics = Chapter(id="ch-physics", subject_id="science", name="Physics", order=1)
        db.add_all([board, grade, math, science, ch_algebra, ch_geometry, ch_physics])

        topics = [
            Topic(id="t-linear", chapter_id="ch-algebra", name="Linear Equations", weight=0.25),
            Topic(id="t-mensuration", chapter_id="ch-geometry", name="Mensuration", weight=0.3),
            Topic(id="t-circles", chapter_id="ch-geometry", name="Circles", weight=0.25),
            Topic(id="t-motion", chapter_id="ch-physics", name="Motion & Force", weight=0.35),
        ]
        db.add_all(topics)

        questions = [
            Question(id="q-1", topic_id="t-linear", institution_id=INST_ID, board="CBSE", grade="Grade 8", subject="Mathematics", chapter="Algebra", topic_name="Linear Equations", text="Solve for x: 2x + 3 = 11", difficulty="medium", marks=2, question_type="mcq", option_a="4", option_b="5", option_c="6", option_d="7", correct_answer="A"),
            Question(id="q-2", topic_id="t-mensuration", institution_id=INST_ID, board="CBSE", grade="Grade 8", subject="Mathematics", chapter="Geometry", topic_name="Mensuration", text="A cylinder has radius 7 cm and height 10 cm. Find its volume.", difficulty="hard", marks=3, question_type="mcq", option_a="1540 cm³", option_b="770 cm³", option_c="220 cm³", option_d="440 cm³", correct_answer="A"),
            Question(id="q-3", topic_id="t-circles", institution_id=INST_ID, board="CBSE", grade="Grade 8", subject="Mathematics", chapter="Geometry", topic_name="Circles", text="The circumference of a circle is 44 cm. Find its radius.", difficulty="medium", marks=2, question_type="mcq", option_a="7 cm", option_b="14 cm", option_c="22 cm", option_d="3.5 cm", correct_answer="A"),
            Question(id="q-5", topic_id="t-motion", institution_id=INST_ID, board="CBSE", grade="Grade 8", subject="Science", chapter="Physics", topic_name="Motion & Force", text="A car travels 120 km in 2 hours. What is its average speed?", difficulty="easy", marks=1, question_type="mcq", option_a="60 km/h", option_b="40 km/h", option_c="80 km/h", option_d="120 km/h", correct_answer="A"),
        ]
        db.add_all(questions)

        db.add(
            QuestionPaper(
                id="qp-1",
                institution_id=INST_ID,
                name="Algebra Unit — Linear & Quadratic",
                board="CBSE",
                grade="Grade 8",
                subject="Mathematics",
                question_ids=to_json_list(["q-1"]),
                topics=to_json_list(["Linear Equations"]),
                total_marks=2,
                created_at="2026-06-01",
                created_by="tut-1",
                source="upload",
            )
        )

        batch = Batch(id="batch-a", institution_id=INST_ID, name="Batch A", board="CBSE", grade="Grade 8", subject="Mathematics", avg_score=72)
        db.add(batch)
        db.add_all([
            BatchStudent(batch_id="batch-a", student_id="stu-1"),
            BatchStudent(batch_id="batch-a", student_id="stu-2"),
            BatchStudent(batch_id="batch-a", student_id="stu-3"),
            BatchStudent(batch_id="batch-a", student_id="stu-4"),
            BatchStudent(batch_id="batch-a", student_id="stu-5"),
            BatchStudent(batch_id="batch-a", student_id="stu-6"),
            BatchStudent(batch_id="batch-a", student_id="stu-7"),
            BatchStudent(batch_id="batch-a", student_id="stu-8"),
            BatchStudent(batch_id="batch-a", student_id="stu-9"),
            BatchStudent(batch_id="batch-a", student_id="stu-10"),
            BatchStudent(batch_id="batch-a", student_id="stu-11"),
            BatchStudent(batch_id="batch-a", student_id="stu-12"),
        ])

        db.add_all([
            Assessment(id="ta-1", institution_id=INST_ID, title="Chapter Test — Mensuration", board="CBSE", grade="Grade 8", subject="Mathematics", scope="chapter", mode="assessment", batch_name="Batch A", question_count=25, duration_minutes=45, scheduled_at="2026-06-28", status="scheduled", center_ids=to_json_list(["c1", "c2"]), selected_question_ids=to_json_list(["q-2", "q-3"]), assigned_student_ids=to_json_list(["stu-1", "stu-2"]), created_by_tutor_id="tut-1", chapter="Geometry"),
            Assessment(id="ta-2", institution_id=INST_ID, title="Topic Quiz — Linear Equations", board="CBSE", grade="Grade 8", subject="Mathematics", scope="topic", mode="practice", batch_name="Batch A", question_count=10, duration_minutes=15, scheduled_at="2026-06-25", status="live", center_ids=to_json_list(["c1"]), selected_question_ids=to_json_list(["q-1"]), assigned_student_ids=to_json_list(["stu-1", "stu-2"]), created_by_tutor_id="tut-1", topic="Linear Equations"),
        ])

        db.add(
            AssessmentSubmission(
                id="sub-1",
                assessment_id="ta-2",
                student_id="stu-1",
                score=1,
                max_score=1,
                time_spent_min=8,
                submitted_at="2026-06-25",
                status="attended",
                answers=to_json_list([{"questionId": "q-1", "selectedOption": "A", "correct": True}]),
            )
        )


        now = datetime.now(timezone.utc).isoformat()
        db.add_all([
            Notification(id="n-1", institution_id=INST_ID, role="student", kind="warning", title="Practice streak at risk", message="Complete 10 minutes of Algebra practice today.", created_at=now, read=False, href="/student/assessments"),
            Notification(id="n-2", institution_id=INST_ID, role="tutor", kind="info", title="New assessment submissions", message="Batch A: 12 students completed Topic Quiz.", created_at=now, read=False, href="/tutor/assessments"),
            Notification(id="n-3", institution_id=INST_ID, role="admin", kind="risk", title="At-risk students", message="3 students trending down in Algebra.", created_at=now, read=True, href="/admin/students"),
        ])

        db.commit()
        ensure_csc_inactivity_demo(db)
        print("Database seeded successfully.")
        print("Demo login: arjun@brightpath.edu / demo123 (institution code: BRIGHTPATH)")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
