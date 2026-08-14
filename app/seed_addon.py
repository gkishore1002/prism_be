"""
Addon seed: adds a new branch (c4 · Pune), 3 staff, and 10 students.
Run with:  python -m app.seed_addon
Idempotent — safe to run multiple times.
"""

from datetime import datetime, timezone

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
import app.models  # noqa: F401 — registers all mappers
from app.models.content import Batch, BatchStudent
from app.models.institution import Center
from app.models.user import StudentProfile, User
from app.utils import to_json_list

INST_ID = "inst-1"
CENTER_ID = "c4"
BATCH_B_ID = "batch-b"


def seed_addon() -> None:
    db = SessionLocal()
    try:
        pwd = hash_password(settings.demo_password)
        now = datetime.now(timezone.utc).isoformat()

        # ── 1. New branch ────────────────────────────────────────────────────
        if not db.get(Center, CENTER_ID):
            db.add(
                Center(
                    id=CENTER_ID,
                    institution_id=INST_ID,
                    name="BrightPath · Pune",
                    city="Pune",
                    code="PUNE",
                    student_count=0,
                )
            )
            print("[+] Created center: BrightPath - Pune (c4)")
        else:
            print("[=] Center c4 already exists - skipping")

        # ── 2. Staff (tutors) ─────────────────────────────────────────────────
        staff = [
            ("tut-2", "Anjali Verma",   "anjali@brightpath.edu",  "tutor"),
            ("tut-3", "Siddharth Rao",  "siddharth@brightpath.edu", "tutor"),
            ("tut-4", "Meghna Pillai",  "meghna@brightpath.edu",  "tutor"),
        ]
        for uid, name, email, role in staff:
            if not db.get(User, uid):
                db.add(
                    User(
                        id=uid,
                        institution_id=INST_ID,
                        name=name,
                        email=email,
                        password_hash=pwd,
                        role=role,
                        roles=role,
                    )
                )
                print(f"[+] Created staff: {name} ({email})")
            else:
                print(f"[=] Staff {uid} already exists - skipping")

        # ── 3. Students ───────────────────────────────────────────────────────
        # Mix across centers: 6 → c4 (Pune), 2 → c2 (Borivali), 2 → c3 (Thane)
        new_students = [
            # id,        name,               email,                           center, health, readiness, status,     improving, gaps
            ("stu-13", "Tanvi Kulkarni",    "tanvi@brightpath.edu",          "c4",   76,    73,  "excellent", True,  0),
            ("stu-14", "Rohan Joshi",       "rohan.j@brightpath.edu",        "c4",   61,    57,  "fair",      False, 2),
            ("stu-15", "Shreya Patil",      "shreya@brightpath.edu",         "c4",   88,    85,  "excellent", True,  0),
            ("stu-16", "Aryan Desai",       "aryan@brightpath.edu",          "c4",   53,    49,  "weak",      False, 3),
            ("stu-17", "Nisha More",        "nisha@brightpath.edu",          "c4",   79,    77,  "good",      True,  1),
            ("stu-18", "Kunal Pawar",       "kunal@brightpath.edu",          "c4",   92,    90,  "excellent", True,  0),
            ("stu-19", "Divya Sharma",      "divya.s@brightpath.edu",        "c2",   66,    63,  "fair",      False, 2),
            ("stu-20", "Harish Nambiar",    "harish@brightpath.edu",         "c2",   81,    79,  "good",      True,  0),
            ("stu-21", "Preethi Suresh",    "preethi@brightpath.edu",        "c3",   58,    54,  "weak",      False, 3),
            ("stu-22", "Lakshmi Iyer",      "lakshmi@brightpath.edu",        "c3",   84,    82,  "good",      True,  0),
        ]

        health_status_map = {
            "excellent": "excellent",
            "good": "good",
            "fair": "fair",
            "weak": "weak",
        }

        for sid, name, email, center_id, health, readiness, hs_label, improving, gaps in new_students:
            if not db.get(User, sid):
                db.add(
                    User(
                        id=sid,
                        institution_id=INST_ID,
                        name=name,
                        email=email,
                        password_hash=pwd,
                        role="student",
                        roles="student",
                        grade_id="g8",
                        board_id="cbse",
                    )
                )
            if not db.get(StudentProfile, sid):
                db.add(
                    StudentProfile(
                        id=sid,
                        user_id=sid,
                        board="CBSE",
                        grade="Grade 8",
                        batch="Batch B",
                        center_id=center_id,
                        health=health,
                        health_status=health_status_map[hs_label],
                        readiness=readiness,
                        last_assessment="2026-08-01",
                        critical_gaps=gaps,
                        improving=improving,
                        status="active",
                    )
                )
                print(f"[+] Created student: {name} -> center {center_id} | health={health}")
            else:
                print(f"[=] Student {sid} already exists - skipping")

        # ── 4. Batch B (for Pune center) ──────────────────────────────────────
        if not db.get(Batch, BATCH_B_ID):
            db.add(
                Batch(
                    id=BATCH_B_ID,
                    institution_id=INST_ID,
                    name="Batch B",
                    board="CBSE",
                    grade="Grade 8",
                    subject="Mathematics",
                    avg_score=75,
                )
            )
            print("[+] Created: Batch B")

        # Enroll Pune students in Batch B
        pune_student_ids = ["stu-13", "stu-14", "stu-15", "stu-16", "stu-17", "stu-18"]
        for sid in pune_student_ids:
            exists = (
                db.query(BatchStudent)
                .filter(BatchStudent.batch_id == BATCH_B_ID, BatchStudent.student_id == sid)
                .first()
            )
            if not exists:
                db.add(BatchStudent(batch_id=BATCH_B_ID, student_id=sid))

        # ── 5. Commit ─────────────────────────────────────────────────────────
        db.commit()

        print()
        print("=" * 55)
        print("ADDON SEED COMPLETE")
        print("=" * 55)
        print("  New branch : BrightPath - Pune (c4)")
        print("  New staff  : Anjali Verma, Siddharth Rao, Meghna Pillai")
        print("  New students: stu-13 to stu-22 (10 total)")
        print("  Batch B    : 6 Pune students enrolled")
        print(f"  Password for all: {settings.demo_password}")
        print("=" * 55)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_addon()
