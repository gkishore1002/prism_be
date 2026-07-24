import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.questions import _question_out
from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.pagination import PaginatedOut, paginate_query
from app.core.routing import CamelCaseAPIRoute
from app.models.academic import Question
from app.models.assessment import Assessment, AssessmentSubmission
from app.models.user import StudentProfile, User
from app.services.assessment_queries import list_assessments_for_student
from app.services.submissions import (
    existing_submission,
    update_assessment_class_avg,
    update_student_profile_after_submission,
)
from app.services.assessment_report import build_and_store_assessment_report
from app.schemas import (
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


def _assessment_out(a: Assessment, *, student_submitted: bool = False) -> AssessmentOut:
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
    return [_assessment_out(a, student_submitted=submitted) for a, submitted in rows]


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
        if a.status != "live":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Assessment is not live yet. Wait for your tutor to start it.",
            )
        if existing_submission(db, assessment_id, profile.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Assessment already submitted. Results will be shared later.",
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
    if body.title:
        a.title = body.title
    if body.status:
        a.status = body.status
    if body.scheduled_at:
        a.scheduled_at = body.scheduled_at
    if body.assigned_student_ids is not None:
        a.assigned_student_ids = to_json_list(body.assigned_student_ids)
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

    if assessment.status != "live":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Assessment is not live yet. Wait for your tutor to start it.",
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
    if not assessment:
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
