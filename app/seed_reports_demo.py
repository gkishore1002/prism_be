"""Add demo students and batch links for reports — safe to run on an existing database."""

from __future__ import annotations

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.content import Batch, BatchStudent
from app.models.user import StudentProfile, User

INST_ID = "inst-1"

EXTRA_STUDENTS: list[tuple[str, str, str, int, int, bool, int]] = [
    # id, name, email, health, readiness, improving, critical_gaps
    ("stu-2", "Sneha Patel", "sneha@brightpath.edu", 85, 88, True, 0),
    ("stu-3", "Rohan Das", "rohan@brightpath.edu", 54, 48, False, 3),
    ("stu-4", "Kavya Iyer", "kavya@brightpath.edu", 78, 76, True, 1),
    ("stu-5", "Aditya Nair", "aditya@brightpath.edu", 82, 80, True, 0),
    ("stu-6", "Priya Menon", "priya.m@brightpath.edu", 91, 89, True, 0),
    ("stu-7", "Vikram Singh", "vikram@brightpath.edu", 67, 62, False, 2),
    ("stu-8", "Ananya Reddy", "ananya@brightpath.edu", 88, 86, True, 0),
    ("stu-9", "Rahul Kapoor", "rahul@brightpath.edu", 59, 55, False, 2),
    ("stu-10", "Meera Joshi", "meera@brightpath.edu", 74, 71, True, 1),
    ("stu-11", "Dev Sharma", "dev@brightpath.edu", 71, 69, True, 1),
    ("stu-12", "Isha Gupta", "isha@brightpath.edu", 83, 81, True, 0),
]


def seed_reports_demo() -> None:
    db = SessionLocal()
    pwd = hash_password(settings.demo_password)
    added = 0
    try:
        batch = db.query(Batch).filter(Batch.institution_id == INST_ID).first()
        if not batch:
            print("No batch found — add curriculum via UI first.")
            return
        batch_id = batch.id
        batch_name = batch.name

        for sid, name, email, health, readiness, improving, gaps in EXTRA_STUDENTS:
            if db.get(User, sid):
                continue
            db.add(
                User(
                    id=sid,
                    institution_id=INST_ID,
                    name=name,
                    email=email,
                    password_hash=pwd,
                    role="student",
                    roles="student",
                )
            )
            db.add(
                StudentProfile(
                    id=sid,
                    user_id=sid,
                    board="CBSE",
                    grade="Grade 8",
                    batch=batch_name,
                    center_id="c1",
                    health=health,
                    health_status="good" if health >= 70 else "weak",
                    readiness=readiness,
                    last_assessment="2026-07-10",
                    critical_gaps=gaps,
                    improving=improving,
                )
            )
            existing_link = (
                db.query(BatchStudent)
                .filter(BatchStudent.batch_id == batch_id, BatchStudent.student_id == sid)
                .first()
            )
            if not existing_link:
                db.add(BatchStudent(batch_id=batch_id, student_id=sid))
            added += 1

        db.commit()
        print(f"Reports demo seed complete — {added} new student(s) added.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_reports_demo()
