"""Bulk CSV import for students and staff."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.content import Batch, BatchStudent
from app.models.institution import Center
from app.models.user import StudentProfile, User
from app.schemas import BulkImportResult, BulkImportRowResult, StaffBulkRow, StudentBulkRow
from app.services.branch_access import assert_actor_can_assign_centers, assert_can_access_center
from app.services.centers import sync_center_counts, validate_center_for_institution
from app.services.user_credentials import normalize_phone, resolve_user_credentials
from app.services.user_roles import add_role, has_role


def _student_template_rows() -> tuple[list[str], list[list[str]]]:
    headers = [
        "name",
        "phone",
        "board",
        "grade",
        "batch",
        "center",
        "academic_year",
        "password",
        "school_name",
    ]
    sample = [
        [
            "Riya Sharma",
            "9876543210",
            "CBSE",
            "Grade 8",
            "Batch A",
            "Main Campus",
            "2025-26",
            "",
            "Delhi Public School",
        ],
    ]
    return headers, sample


def _staff_template_rows(*, include_org_owner: bool) -> tuple[list[str], list[list[str]]]:
    headers = [
        "name",
        "phone",
        "branch_admin",
        "tutor",
        "branches",
        "password",
    ]
    if include_org_owner:
        headers.insert(4, "org_owner")
    sample_row = [
        "Murugavel",
        "9791100112",
        "yes",
        "no",
        "Main Campus",
        "",
    ]
    if include_org_owner:
        sample_row.insert(4, "no")
    return headers, [sample_row]


def _resolve_center_id(
    db: Session,
    institution_id: str,
    *,
    center_id: str = "",
    center_name: str = "",
) -> str | None:
    if center_id.strip():
        validate_center_for_institution(db, center_id.strip(), institution_id)
        return center_id.strip()
    name = center_name.strip()
    if not name:
        return None
    center = (
        db.query(Center)
        .filter(Center.institution_id == institution_id, Center.name.ilike(name))
        .first()
    )
    if not center:
        raise ValueError(f"Branch not found: {name}")
    return center.id


def _resolve_center_ids(
    db: Session,
    institution_id: str,
    center_ids: list[str],
    center_names: list[str],
) -> list[str]:
    resolved: list[str] = []
    for center_id in center_ids:
        if center_id.strip():
            validate_center_for_institution(db, center_id.strip(), institution_id)
            resolved.append(center_id.strip())
    for center_name in center_names:
        center_id = _resolve_center_id(db, institution_id, center_name=center_name)
        if center_id:
            resolved.append(center_id)
    return list(dict.fromkeys(resolved))


def _unique_batch_id(db: Session, name: str) -> str:
    base_id = f"batch-{name.lower().replace(' ', '-')}"
    batch_id = base_id
    suffix = 1
    while db.get(Batch, batch_id):
        batch_id = f"{base_id}-{suffix}"
        suffix += 1
    return batch_id


def _sync_profile_batch(db: Session, student_id: str) -> None:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return
    memberships = db.query(BatchStudent).filter(BatchStudent.student_id == student_id).all()
    if not memberships:
        profile.batch = ""
        return
    batch_names: list[str] = []
    primary_batch: Batch | None = None
    for membership in memberships:
        batch = db.get(Batch, membership.batch_id)
        if batch:
            batch_names.append(batch.name)
            if primary_batch is None:
                primary_batch = batch
    profile.batch = ", ".join(sorted(set(batch_names)))
    if primary_batch:
        profile.board = primary_batch.board
        profile.grade = primary_batch.grade


def _get_or_create_batch(
    db: Session,
    institution_id: str,
    batch_name: str,
    *,
    board: str,
    grade: str,
) -> Batch:
    batch_name = batch_name.strip()
    board = board.strip()
    grade = grade.strip()
    batch_row = (
        db.query(Batch)
        .filter(
            Batch.institution_id == institution_id,
            Batch.board == board,
            Batch.grade == grade,
            Batch.name == batch_name,
        )
        .first()
    )
    if batch_row:
        return batch_row
    batch_row = Batch(
        id=_unique_batch_id(db, batch_name),
        institution_id=institution_id,
        name=batch_name,
        board=board,
        grade=grade,
    )
    db.add(batch_row)
    db.flush()
    return batch_row


def _assign_student_batch(
    db: Session,
    institution_id: str,
    student_id: str,
    batch_name: str,
    *,
    board: str,
    grade: str,
) -> None:
    batch_name = batch_name.strip()
    if not batch_name:
        return
    batch_row = _get_or_create_batch(
        db,
        institution_id,
        batch_name,
        board=board,
        grade=grade,
    )
    existing = (
        db.query(BatchStudent)
        .filter(BatchStudent.batch_id == batch_row.id, BatchStudent.student_id == student_id)
        .first()
    )
    if not existing:
        db.add(BatchStudent(batch_id=batch_row.id, student_id=student_id))
    _sync_profile_batch(db, student_id)


def _create_student_row(
    db: Session,
    *,
    institution_id: str,
    actor: User,
    role: str,
    row: StudentBulkRow,
) -> str:
    center_id = _resolve_center_id(
        db,
        institution_id,
        center_id=row.center_id,
        center_name=row.center_name,
    )
    if not center_id:
        default_center = (
            db.query(Center)
            .filter(Center.institution_id == institution_id)
            .order_by(Center.name)
            .first()
        )
        center_id = default_center.id if default_center else None
    if center_id:
        assert_can_access_center(db, actor, role, center_id)

    email, password = resolve_user_credentials(phone=row.phone, password=row.password)
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError("Phone number already registered")

    sid = f"stu-{uuid.uuid4().hex[:8]}"
    new_user = User(
        id=sid,
        institution_id=institution_id,
        name=row.name.strip(),
        email=email,
        password_hash=hash_password(password),
        role="student",
    )
    profile = StudentProfile(
        id=sid,
        user_id=sid,
        board=row.board.strip(),
        grade=row.grade.strip(),
        batch=row.batch.strip(),
        center_id=center_id,
        academic_year=row.academic_year.strip() or "2025-26",
        school_name=row.school_name.strip() if row.school_name else None,
    )
    db.add_all([new_user, profile])
    db.flush()
    _assign_student_batch(
        db,
        institution_id,
        sid,
        row.batch,
        board=row.board,
        grade=row.grade,
    )
    return sid


def _create_staff_row(
    db: Session,
    *,
    institution_id: str,
    actor: User,
    role: str,
    row: StaffBulkRow,
    allow_org_owner: bool,
) -> str:
    if row.is_owner and not allow_org_owner:
        raise ValueError("Organization owner role requires Organization Admin portal")
    target_roles: list[str] = []
    if row.is_owner or row.is_branch_admin:
        target_roles.append("admin")
    if row.is_tutor:
        target_roles.append("tutor")
    if not target_roles:
        raise ValueError("Select at least one role: branch_admin, tutor, or org_owner")

    center_ids = _resolve_center_ids(db, institution_id, row.center_ids, row.center_names)
    if center_ids:
        assert_actor_can_assign_centers(db, actor, role, center_ids)

    email, password = resolve_user_credentials(phone=row.phone, password=row.password)
    existing = db.query(User).filter(User.email == email).first()

    if existing:
        if existing.institution_id != institution_id:
            raise ValueError("Phone number already registered in another organization")
        if has_role(existing, "student") and not (has_role(existing, "admin") or has_role(existing, "tutor")):
            raise ValueError("This phone belongs to a student account")
        for staff_role in target_roles:
            add_role(existing, staff_role)
        if row.is_owner:
            existing.is_owner = True
        if row.name.strip():
            existing.name = row.name.strip()
        staff = existing
    else:
        staff = User(
            id=f"stf-{uuid.uuid4().hex[:8]}",
            institution_id=institution_id,
            name=row.name.strip(),
            email=email,
            password_hash=hash_password(password),
            role=target_roles[0],
            roles=",".join(target_roles),
            is_owner=row.is_owner,
        )
        db.add(staff)
        db.flush()

    if center_ids and ("admin" in target_roles or "tutor" in target_roles):
        from app.services.branch_access import set_user_center_access

        set_user_center_access(db, user=staff, center_ids=center_ids, actor=actor)

    return staff.id


def import_students_bulk(
    db: Session,
    *,
    institution_id: str,
    actor: User,
    role: str,
    rows: list[StudentBulkRow],
) -> BulkImportResult:
    results: list[BulkImportRowResult] = []
    created = 0
    failed = 0

    for index, row in enumerate(rows, start=1):
        try:
            if not row.name.strip():
                raise ValueError("Name is required")
            digits = normalize_phone(row.phone)
            if len(digits) < 10:
                raise ValueError("Phone must be at least 10 digits")
            student_id = _create_student_row(
                db,
                institution_id=institution_id,
                actor=actor,
                role=role,
                row=row,
            )
            db.commit()
            created += 1
            results.append(
                BulkImportRowResult(row=index, name=row.name.strip(), success=True, id=student_id)
            )
        except HTTPException as exc:
            db.rollback()
            failed += 1
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            results.append(
                BulkImportRowResult(row=index, name=row.name.strip() or f"Row {index}", success=False, error=detail)
            )
        except Exception as exc:
            db.rollback()
            failed += 1
            results.append(
                BulkImportRowResult(
                    row=index,
                    name=row.name.strip() or f"Row {index}",
                    success=False,
                    error=str(exc),
                )
            )

    sync_center_counts(db, institution_id, commit=True)
    return BulkImportResult(created=created, failed=failed, results=results)


def import_staff_bulk(
    db: Session,
    *,
    institution_id: str,
    actor: User,
    role: str,
    rows: list[StaffBulkRow],
    allow_org_owner: bool,
) -> BulkImportResult:
    results: list[BulkImportRowResult] = []
    created = 0
    failed = 0

    for index, row in enumerate(rows, start=1):
        try:
            if not row.name.strip():
                raise ValueError("Name is required")
            digits = normalize_phone(row.phone)
            if len(digits) < 10:
                raise ValueError("Phone must be at least 10 digits")
            staff_id = _create_staff_row(
                db,
                institution_id=institution_id,
                actor=actor,
                role=role,
                row=row,
                allow_org_owner=allow_org_owner,
            )
            db.commit()
            created += 1
            results.append(
                BulkImportRowResult(row=index, name=row.name.strip(), success=True, id=staff_id)
            )
        except HTTPException as exc:
            db.rollback()
            failed += 1
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            results.append(
                BulkImportRowResult(row=index, name=row.name.strip() or f"Row {index}", success=False, error=detail)
            )
        except Exception as exc:
            db.rollback()
            failed += 1
            results.append(
                BulkImportRowResult(
                    row=index,
                    name=row.name.strip() or f"Row {index}",
                    success=False,
                    error=str(exc),
                )
            )

    return BulkImportResult(created=created, failed=failed, results=results)


def student_import_template_csv() -> tuple[list[str], list[list[str]]]:
    return _student_template_rows()


def staff_import_template_csv(*, include_org_owner: bool) -> tuple[list[str], list[list[str]]]:
    return _staff_template_rows(include_org_owner=include_org_owner)
