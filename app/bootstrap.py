"""Create an empty database with login accounts and a default HQ center — no curriculum.

Runs automatically on API startup when the database has no institution (see app.main).

Manual usage (from prism_be, with venv active):

  python -m app.bootstrap ^
    --inst-name "My Academy" ^
    --inst-code "MYACAD" ^
    --admin-email "admin@myacademy.edu" ^
    --password "your-secure-password"

Optional extra accounts (no curriculum data):

  --tutor-email "tutor@myacademy.edu" --tutor-name "Priya Sharma"
  --student-email "student@myacademy.edu" --student-name "Arjun Mehta"
"""

from __future__ import annotations

import argparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
import app.models  # noqa: F401
from app.models.institution import Center, Institution
from app.models.user import StudentProfile, User
from app.utils import to_json_list

DEFAULT_CENTER_ID = "c1"


def _bootstrap_into_db(
    db: Session,
    *,
    inst_name: str,
    inst_code: str,
    admin_name: str,
    admin_email: str,
    password: str,
    tutor_name: str | None = None,
    tutor_email: str | None = None,
    student_name: str | None = None,
    student_email: str | None = None,
    create_default_center: bool = True,
) -> bool:
    """Insert institution, default HQ center, and login accounts. Returns True if created."""
    code = inst_code.strip().upper()
    if db.query(Institution).first():
        return False
    if db.query(Institution).filter(Institution.code == code).first():
        return False

    inst_id = "inst-1"
    pwd = hash_password(password)

    db.add(
        Institution(
            id=inst_id,
            name=inst_name.strip(),
            code=code,
            type="coaching",
            board_ids=to_json_list([]),
        )
    )

    if create_default_center:
        db.add(
            Center(
                id=DEFAULT_CENTER_ID,
                institution_id=inst_id,
                name=f"{inst_name.strip()} · HQ",
                city="",
            )
        )

    db.add(
        User(
            id="adm-1",
            institution_id=inst_id,
            name=admin_name.strip(),
            email=admin_email.strip().lower(),
            password_hash=pwd,
            role="admin",
            roles="admin",
        )
    )

    if tutor_email:
        db.add(
            User(
                id="tut-1",
                institution_id=inst_id,
                name=(tutor_name or "Tutor").strip(),
                email=tutor_email.strip().lower(),
                password_hash=pwd,
                role="tutor",
                roles="tutor",
            )
        )

    if student_email:
        sid = "stu-1"
        db.add(
            User(
                id=sid,
                institution_id=inst_id,
                name=(student_name or "Student").strip(),
                email=student_email.strip().lower(),
                password_hash=pwd,
                role="student",
                roles="student",
            )
        )
        db.add(
            StudentProfile(
                id=sid,
                user_id=sid,
                board="",
                grade="",
                batch="",
                center_id=DEFAULT_CENTER_ID if create_default_center else "",
            )
        )

    db.commit()
    return True


def bootstrap_if_empty(db: Session) -> bool:
    """Bootstrap default institution when the database is empty. Used by API startup."""
    if not settings.auto_bootstrap:
        return False
    created = _bootstrap_into_db(
        db,
        inst_name=settings.bootstrap_inst_name,
        inst_code=settings.bootstrap_inst_code,
        admin_name=settings.bootstrap_admin_name,
        admin_email=settings.bootstrap_admin_email,
        password=settings.demo_password,
        tutor_name=settings.bootstrap_tutor_name,
        tutor_email=settings.bootstrap_tutor_email,
        student_name=settings.bootstrap_student_name,
        student_email=settings.bootstrap_student_email,
        create_default_center=True,
    )
    if created:
        _print_credentials(
            code=settings.bootstrap_inst_code.upper(),
            password=settings.demo_password,
            admin_email=settings.bootstrap_admin_email,
            tutor_email=settings.bootstrap_tutor_email,
            student_email=settings.bootstrap_student_email,
            prefix="Auto-bootstrapped empty database.",
        )
    return created


def ensure_default_centers(db: Session) -> int:
    """Create HQ center for institutions that have none. Returns count created."""
    created = 0
    for inst in db.query(Institution).all():
        center = db.query(Center).filter(Center.institution_id == inst.id).first()
        if center:
            continue
        center_id = DEFAULT_CENTER_ID if inst.id == "inst-1" else f"ctr-{inst.id}-hq"
        db.add(
            Center(
                id=center_id,
                institution_id=inst.id,
                name=f"{inst.name} · HQ",
                city="",
            )
        )
        db.flush()
        for profile in (
            db.query(StudentProfile)
            .join(User)
            .filter(
                User.institution_id == inst.id,
                (StudentProfile.center_id == "") | (StudentProfile.center_id.is_(None)),
            )
            .all()
        ):
            profile.center_id = center_id
        created += 1
    if created:
        db.commit()
    return created


def bootstrap(
    *,
    inst_name: str,
    inst_code: str,
    admin_name: str,
    admin_email: str,
    password: str,
    tutor_name: str | None = None,
    tutor_email: str | None = None,
    student_name: str | None = None,
    student_email: str | None = None,
) -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        code = inst_code.strip().upper()
        if db.query(Institution).filter(Institution.code == code).first():
            print(f"Institution {code} already exists — bootstrap skipped.")
            return

        created = _bootstrap_into_db(
            db,
            inst_name=inst_name,
            inst_code=inst_code,
            admin_name=admin_name,
            admin_email=admin_email,
            password=password,
            tutor_name=tutor_name,
            tutor_email=tutor_email,
            student_name=student_name,
            student_email=student_email,
            create_default_center=True,
        )
        if not created:
            if db.query(Institution).first():
                print("Database already has an institution — bootstrap skipped.")
            return
        _print_credentials(
            code=code,
            password=password,
            admin_email=admin_email,
            tutor_email=tutor_email,
            student_email=student_email,
            prefix="Empty database bootstrapped.",
        )
    finally:
        db.close()


def _print_credentials(
    *,
    code: str,
    password: str,
    admin_email: str,
    tutor_email: str | None,
    student_email: str | None,
    prefix: str,
) -> None:
    print(prefix)
    print()
    print("Login at http://localhost:5173/login")
    print("  Use INSTITUTION CODE (not center ID):")
    print(f"  Institution code : {code}")
    print(f"  Password (all)   : {password}")
    print(f"  Admin            : {admin_email.strip().lower()}")
    if tutor_email:
        print(f"  Tutor            : {tutor_email.strip().lower()}")
    if student_email:
        print(f"  Student          : {student_email.strip().lower()}")
    print()
    print("A default HQ center was created. Add curriculum and more centers via the admin UI.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap empty Prism database")
    parser.add_argument("--inst-name", default="My Academy", help="Institution display name")
    parser.add_argument("--inst-code", default="MYACAD", help="Institution code for login")
    parser.add_argument("--admin-name", default="Admin User", help="Admin display name")
    parser.add_argument("--admin-email", default="admin@myacademy.edu", help="Admin login email")
    parser.add_argument("--password", default=settings.demo_password, help="Password for all accounts")
    parser.add_argument("--tutor-name", default="Tutor User")
    parser.add_argument("--tutor-email", default=None, help="Optional tutor login email")
    parser.add_argument("--student-name", default="Student User")
    parser.add_argument("--student-email", default=None, help="Optional student login email")
    args = parser.parse_args()

    bootstrap(
        inst_name=args.inst_name,
        inst_code=args.inst_code,
        admin_name=args.admin_name,
        admin_email=args.admin_email,
        password=args.password,
        tutor_name=args.tutor_name,
        tutor_email=args.tutor_email,
        student_name=args.student_name,
        student_email=args.student_email,
    )


if __name__ == "__main__":
    main()
