import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.questions import _question_out
from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.pagination import PaginatedOut, paginate_query
from app.core.routing import CamelCaseAPIRoute
from app.models.academic import Question
from app.models.assessment import Assessment, AssessmentSubmission
from app.models.csc import AssessmentAccessRequest
from app.models.user import StudentProfile, User
from app.services.assessment_access import (
    can_student_attend,
    default_access_granted_until,
    get_access_request_status,
    has_approved_extension,
    is_past_deadline,
    mark_absent_for_pending_students,
)
from app.services.assessment_queries import list_assessments_for_student
from app.services.submissions import (
    existing_submission,
    update_assessment_class_avg,
    update_student_profile_after_submission,
)
from app.services.assessment_report import build_and_store_assessment_report
from app.services.access_request_review import build_access_request_review_context
from app.services.institution_policies import get_assessment_policy
from app.services.notification_dispatch import notify_reassignment_requested, notify_reassignment_reviewed
from app.services.audit_log import record_audit
from app.schemas import (
    AssessmentAccessRequestCreate,
    AssessmentAccessRequestOut,
    AssessmentAccessRequestReview,
    AssessmentCreate,
    AssessmentOut,
    AssessmentSubmissionCreate,
    AssessmentSubmissionOut,
    AssessmentUpdate,
    AttendanceRecordOut,
    QuestionOut,
)
from app.utils import dict_get, from_json_list, to_json_list

router = APIRouter(tags=["assessments"], route_class=CamelCaseAPIRoute)


def _assessment_out(
    a: Assessment,
    *,
    student_submitted: bool = False,
    student_id: str | None = None,
    db: Session | None = None,
) -> AssessmentOut:
    timing_over = False
    access_request_status = None
    can_attend = False
    if student_id and db is not None:
        submitted = student_submitted or existing_submission(db, a.id, student_id) is not None
        timing_over = is_past_deadline(a, student_id, db) and not submitted
        access_request_status = get_access_request_status(db, a.id, student_id)  # type: ignore[assignment]
        can_attend = can_student_attend(db, a, student_id, has_submission=submitted)
    return AssessmentOut(
        id=a.id,
        title=a.title,
        board=a.board,
        grade=a.grade,
        subject=a.subject,
        scope=a.scope,  # type: ignore[arg-type]
        mode=a.mode,  # type: ignore[arg-type]
        batch_name=a.batch_name,
        question_count=a.question_count,
        duration_minutes=a.duration_minutes,
        scheduled_at=a.scheduled_at,
        available_until=a.available_until or a.scheduled_at,
        status=a.status,  # type: ignore[arg-type]
        class_avg=a.class_avg,
        center_ids=from_json_list(a.center_ids),
        selected_question_ids=from_json_list(a.selected_question_ids),
        assigned_student_ids=from_json_list(a.assigned_student_ids),
        created_by_tutor_id=a.created_by_tutor_id,
        chapter=a.chapter,
        topic=a.topic,
        question_paper_id=a.question_paper_id,
        paper_coverage=a.paper_coverage,  # type: ignore[arg-type]
        selected_topics=from_json_list(a.selected_topics) if a.selected_topics else None,
        student_submitted=student_submitted,
        timing_over=timing_over,
        access_request_status=access_request_status,  # type: ignore[arg-type]
        can_attend=can_attend,
    )


@router.get("/assessments", response_model=PaginatedOut[AssessmentOut] | list[AssessmentOut])
def list_assessments(
    status_filter: str | None = Query(None, alias="status"),
    tutor_id: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedOut[AssessmentOut] | list[AssessmentOut]:
    q = db.query(Assessment).filter(Assessment.institution_id == user.institution_id)
    if status_filter:
        q = q.filter(Assessment.status == status_filter)
    if tutor_id:
        q = q.filter(Assessment.created_by_tutor_id == tutor_id)
    q = q.order_by(Assessment.scheduled_at.desc())
    if page is None and limit is None:
        rows = q.all()
        updated = False
        for a in rows:
            if a.class_avg is None:
                update_assessment_class_avg(db, a.id)
                updated = True
        if updated:
            db.commit()
        return [_assessment_out(a) for a in rows]
    items, total, page_n, limit_n, pages = paginate_query(q, page or 1, limit)
    updated = False
    for a in items:
        if a.class_avg is None:
            update_assessment_class_avg(db, a.id)
            updated = True
    if updated:
        db.commit()
    return PaginatedOut(
        items=[_assessment_out(a) for a in items],
        total=total,
        page=page_n,
        limit=limit_n,
        pages=pages,
    )


def _resolve_student_profile_id(db: Session, user: User, payload: dict, student_id: str) -> str:
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    role = get_effective_role(payload, user)
    if role == "student":
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student profile not found")
        if student_id != profile.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot view another student's assessments")
        return profile.id
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return student_id


@router.get("/assessments/student/{student_id}", response_model=list[AssessmentOut])
def list_student_assessments(
    student_id: str,
    board: str | None = Query(None),
    grade: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list[AssessmentOut]:
    sid = _resolve_student_profile_id(db, user, payload, student_id)
    rows = list_assessments_for_student(
        db,
        user.institution_id,
        sid,
        board=board,
        grade=grade,
    )
    return [
        _assessment_out(a, student_submitted=submitted, student_id=sid, db=db)
        for a, submitted in rows
    ]


def _access_request_out(db: Session, req: AssessmentAccessRequest) -> AssessmentAccessRequestOut:
    assessment = db.get(Assessment, req.assessment_id)
    profile = db.get(StudentProfile, req.student_id)
    return AssessmentAccessRequestOut(
        id=req.id,
        assessment_id=req.assessment_id,
        assessment_title=assessment.title if assessment else req.assessment_id,
        student_id=req.student_id,
        student_name=profile.user.name if profile else req.student_id,
        reason=req.reason,
        status=req.status,  # type: ignore[arg-type]
        requested_at=req.requested_at,
        reviewed_by=req.reviewed_by,
        reviewed_at=req.reviewed_at,
        review_notes=req.review_notes,
        access_granted_until=req.access_granted_until,
    )


@router.get("/assessments/access-requests", response_model=list[AssessmentAccessRequestOut])
def list_access_requests(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
) -> list[AssessmentAccessRequestOut]:
    q = (
        db.query(AssessmentAccessRequest)
        .join(Assessment, Assessment.id == AssessmentAccessRequest.assessment_id)
        .filter(Assessment.institution_id == user.institution_id)
    )
    if status_filter:
        q = q.filter(AssessmentAccessRequest.status == status_filter)
    rows = q.order_by(AssessmentAccessRequest.requested_at.desc()).all()
    return [_access_request_out(db, r) for r in rows]


def _validate_review_body(
    db: Session,
    institution_id: str,
    role: str,
    body: AssessmentAccessRequestReview,
) -> int:
    """Returns validated extension days for approval."""
    policy = get_assessment_policy(db, institution_id)
    if body.status == "rejected" and policy.require_rejection_reason:
        if not (body.review_notes or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejection reason is required by institution policy.",
            )
    if body.status != "approved":
        return body.extension_days
    if role == "tutor" and not policy.allow_tutor_extension:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tutors are not permitted to approve extensions at this institution.",
        )
    extension_days = body.extension_days or policy.default_extension_days
    if extension_days > policy.max_extension_days:
        if role == "admin" and policy.allow_admin_override:
            return extension_days
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Extension cannot exceed {policy.max_extension_days} days.",
        )
    if extension_days < 1:
        extension_days = policy.default_extension_days
    return extension_days


@router.get("/assessments/access-requests/{request_id}/review-context")
def get_access_request_review_context(
    request_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
) -> dict:
    ctx = build_access_request_review_context(db, request_id, user.institution_id)
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return ctx


@router.patch("/assessments/access-requests/{request_id}", response_model=AssessmentAccessRequestOut)
def review_access_request(
    request_id: str,
    body: AssessmentAccessRequestReview,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
) -> AssessmentAccessRequestOut:
    req = db.get(AssessmentAccessRequest, request_id)
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    assessment = db.get(Assessment, req.assessment_id)
    if not assessment or assessment.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Request already reviewed")
    role = get_effective_role(payload, user)
    prev_status = req.status
    extension_days = _validate_review_body(db, user.institution_id, role, body)
    req.status = body.status
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now().isoformat(timespec="minutes")
    req.review_notes = body.review_notes
    if body.status == "approved":
        req.access_granted_until = default_access_granted_until(extension_days)
    profile = db.get(StudentProfile, req.student_id)
    if profile:
        notify_reassignment_reviewed(
            db,
            req,
            assessment,
            profile,
            approved=body.status == "approved",
        )
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=role,
        action=f"reassignment_{body.status}",
        entity_type="access_request",
        entity_id=req.id,
        previous_state={"status": prev_status},
        new_state={
            "status": req.status,
            "accessGrantedUntil": req.access_granted_until,
            "extensionDays": extension_days if body.status == "approved" else None,
        },
        notes=body.review_notes,
    )
    db.commit()
    db.refresh(req)
    return _access_request_out(db, req)


@router.get("/assessments/{assessment_id}", response_model=AssessmentOut)
def get_assessment(
    assessment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AssessmentOut:
    a = db.get(Assessment, assessment_id)
    if not a or a.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
    if a.class_avg is None:
        update_assessment_class_avg(db, assessment_id)
        db.commit()
        db.refresh(a)
    return _assessment_out(a)


@router.get("/assessments/{assessment_id}/questions", response_model=list[QuestionOut])
def get_assessment_questions(
    assessment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list[QuestionOut]:
    a = db.get(Assessment, assessment_id)
    if not a or a.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")

    role = get_effective_role(payload, user)
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    if role == "student":
        if not profile or profile.id not in from_json_list(a.assigned_student_ids):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not assigned to this assessment")
        if profile.status == "inactive":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account is inactive.")
        if existing_submission(db, assessment_id, profile.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Assessment already submitted. Results will be shared later.",
            )
        if is_past_deadline(a, profile.id, db) and not has_approved_extension(db, assessment_id, profile.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Exam timing over. Request reassignment from your tutor or CSC center.",
            )
        if not can_student_attend(db, a, profile.id):
            if a.status != "live":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Assessment is not live yet. Wait for your tutor to start it.",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot attend this assessment at this time.",
            )
        hide_answers = True
    else:
        hide_answers = False

    qids = from_json_list(a.selected_question_ids)
    questions = db.query(Question).filter(Question.id.in_(qids)).all()
    return [_question_out(q, hide_answer=hide_answers) for q in questions]


@router.post("/assessments", response_model=AssessmentOut, status_code=status.HTTP_201_CREATED)
def create_assessment(
    body: AssessmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> AssessmentOut:
    assessment = Assessment(
        id=f"ta-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        title=body.title,
        board=body.board,
        grade=body.grade,
        subject=body.subject,
        scope=body.scope,
        mode=body.mode,
        batch_name=body.batch_name,
        question_count=body.question_count,
        duration_minutes=body.duration_minutes,
        scheduled_at=body.scheduled_at,
        available_until=body.available_until or body.scheduled_at,
        status=body.status,
        center_ids=to_json_list(body.center_ids),
        selected_question_ids=to_json_list(body.selected_question_ids),
        assigned_student_ids=to_json_list(body.assigned_student_ids),
        created_by_tutor_id=user.id,
        chapter=body.chapter,
        topic=body.topic,
        question_paper_id=body.question_paper_id,
        paper_coverage=body.paper_coverage,
        selected_topics=to_json_list(body.selected_topics or []),
    )
    db.add(assessment)
    db.commit()
    return _assessment_out(assessment)


@router.patch("/assessments/{assessment_id}", response_model=AssessmentOut)
def update_assessment(
    assessment_id: str,
    body: AssessmentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> AssessmentOut:
    a = db.get(Assessment, assessment_id)
    if not a or a.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
    prev_status = a.status
    if body.title:
        a.title = body.title
    if body.status:
        a.status = body.status
    if body.scheduled_at:
        a.scheduled_at = body.scheduled_at
    if body.available_until is not None:
        a.available_until = body.available_until
    if body.assigned_student_ids is not None:
        a.assigned_student_ids = to_json_list(body.assigned_student_ids)
    if body.status == "completed" and prev_status != "completed":
        mark_absent_for_pending_students(db, a)
    db.commit()
    return _assessment_out(a)


@router.delete("/assessments/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assessment(
    assessment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    a = db.get(Assessment, assessment_id)
    if not a or a.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
    db.query(AssessmentSubmission).filter(AssessmentSubmission.assessment_id == assessment_id).delete()
    db.delete(a)
    db.commit()


@router.get("/assessments/{assessment_id}/my-submission", response_model=AssessmentSubmissionOut)
def get_my_submission(
    assessment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("student")),
) -> AssessmentSubmissionOut:
    assessment = db.get(Assessment, assessment_id)
    if not assessment or assessment.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student profile not found")
    sub = existing_submission(db, assessment_id, profile.id)
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No submission yet")
    return AssessmentSubmissionOut(
        id=sub.id,
        assessment_id=sub.assessment_id,
        student_id=sub.student_id,
        score=sub.score,
        max_score=sub.max_score,
        time_spent_min=sub.time_spent_min,
        submitted_at=sub.submitted_at,
        status=sub.status,  # type: ignore[arg-type]
    )


@router.post("/assessments/{assessment_id}/submit", response_model=AssessmentSubmissionOut)
def submit_assessment(
    assessment_id: str,
    body: AssessmentSubmissionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("student")),
) -> AssessmentSubmissionOut:
    assessment = db.get(Assessment, assessment_id)
    if not assessment or assessment.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")

    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Student profile not found")

    assigned = from_json_list(assessment.assigned_student_ids)
    if profile.id not in assigned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not assigned to this assessment")

    if profile.status == "inactive":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account is inactive.")

    if is_past_deadline(assessment, profile.id, db) and not has_approved_extension(
        db, assessment_id, profile.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Exam timing over. Request reassignment from your tutor or CSC center.",
        )

    if not can_student_attend(db, assessment, profile.id):
        if assessment.status != "live":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Assessment is not live yet. Wait for your tutor to start it.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot submit this assessment at this time.",
        )

    if existing_submission(db, assessment_id, profile.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Assessment already submitted")

    qids = from_json_list(assessment.selected_question_ids)
    questions = {q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()}
    score = 0
    max_score = sum(q.marks for q in questions.values())
    for ans in body.answers:
        qid = dict_get(ans, "question_id", "questionId")
        selected = dict_get(ans, "selected_option", "selectedOption", default="")
        q = questions.get(qid)
        if q and q.correct_answer and selected and str(selected).upper() == q.correct_answer.upper():
            score += q.marks

    submitted_at = datetime.now().isoformat(timespec="minutes")
    submission = AssessmentSubmission(
        id=f"sub-{uuid.uuid4().hex[:8]}",
        assessment_id=assessment_id,
        student_id=profile.id,
        score=score,
        max_score=max_score,
        time_spent_min=body.time_spent_min,
        submitted_at=submitted_at,
        status="attended",
        answers=json.dumps(body.answers),
    )
    db.add(submission)
    update_student_profile_after_submission(db, profile, score, max_score, submitted_at)
    update_assessment_class_avg(db, assessment_id)
    db.flush()
    build_and_store_assessment_report(db, assessment_id, profile.id, commit=False)
    db.commit()
    return AssessmentSubmissionOut(
        id=submission.id,
        assessment_id=submission.assessment_id,
        student_id=submission.student_id,
        score=submission.score,
        max_score=submission.max_score,
        time_spent_min=submission.time_spent_min,
        submitted_at=submission.submitted_at,
        status=submission.status,  # type: ignore[arg-type]
    )


@router.get("/assessments/{assessment_id}/attendance", response_model=list[AttendanceRecordOut])
def get_attendance(
    assessment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
) -> list[AttendanceRecordOut]:
    assessment = db.get(Assessment, assessment_id)
    if not assessment or assessment.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")

    invited = from_json_list(assessment.assigned_student_ids)
    submissions = {
        s.student_id: s
        for s in db.query(AssessmentSubmission)
        .filter(AssessmentSubmission.assessment_id == assessment_id)
        .all()
    }
    records: list[AttendanceRecordOut] = []
    for sid in invited:
        profile = db.get(StudentProfile, sid)
        sub = submissions.get(sid)
        if sub:
            records.append(
                AttendanceRecordOut(
                    student_id=sid,
                    student_name=profile.user.name if profile else sid,
                    status="attended",
                    score=sub.score,
                    max_score=sub.max_score,
                    time_spent_min=sub.time_spent_min,
                    submitted_at=sub.submitted_at,
                )
            )
        else:
            records.append(
                AttendanceRecordOut(
                    student_id=sid,
                    student_name=profile.user.name if profile else sid,
                    status="pending",
                )
            )
    return records


@router.post(
    "/assessments/{assessment_id}/access-request",
    response_model=AssessmentAccessRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def create_access_request(
    assessment_id: str,
    body: AssessmentAccessRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("student")),
) -> AssessmentAccessRequestOut:
    assessment = db.get(Assessment, assessment_id)
    if not assessment or assessment.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    if not profile or profile.id not in from_json_list(assessment.assigned_student_ids):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not assigned to this assessment")
    if existing_submission(db, assessment_id, profile.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Assessment already submitted")
    if not is_past_deadline(assessment, profile.id, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Access request only needed after exam timing is over.",
        )
    policy = get_assessment_policy(db, user.institution_id)
    pending = (
        db.query(AssessmentAccessRequest)
        .filter(
            AssessmentAccessRequest.assessment_id == assessment_id,
            AssessmentAccessRequest.student_id == profile.id,
            AssessmentAccessRequest.status == "pending",
        )
        .first()
    )
    if pending:
        return _access_request_out(db, pending)
    if not policy.allow_multiple_requests:
        prior = (
            db.query(AssessmentAccessRequest)
            .filter(
                AssessmentAccessRequest.assessment_id == assessment_id,
                AssessmentAccessRequest.student_id == profile.id,
            )
            .first()
        )
        if prior:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A reassignment request already exists for this assessment.",
            )
    req = AssessmentAccessRequest(
        id=f"aar-{uuid.uuid4().hex[:8]}",
        assessment_id=assessment_id,
        student_id=profile.id,
        reason=body.reason.strip(),
        status="pending",
        requested_at=datetime.now().isoformat(timespec="minutes"),
    )
    db.add(req)
    db.flush()
    notify_reassignment_requested(db, req, assessment, profile)
    db.commit()
    db.refresh(req)
    return _access_request_out(db, req)
