import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.curriculum import _find_or_create_topic
from app.core.deps import get_current_user, get_db, require_roles
from app.core.pagination import PaginatedOut, paginate_query
from app.core.routing import CamelCaseAPIRoute
from app.models.academic import Question
from app.models.content import QuestionPaper
from app.models.user import User
from app.schemas import (
    CustomPaperCreate,
    QuestionCreate,
    QuestionOut,
    QuestionPaperBulkCreate,
    QuestionPaperCreate,
    QuestionPaperOut,
    QuestionPaperUpdate,
    QuestionUpdate,
)
from app.utils import from_json_list, to_json_list
from app.services.syllabus_books import fill_blank_question_topics
from app.services import question_media as media_svc
from app.services.subjects_list import (
    normalize_subjects,
    primary_subject,
    subjects_from_stored,
    subjects_json,
    subjects_overlap,
)

router = APIRouter(tags=["questions", "question-papers"], route_class=CamelCaseAPIRoute)


def _question_out(q: Question, *, hide_answer: bool = False) -> QuestionOut:
    return QuestionOut(
        id=q.id,
        board=q.board,
        grade=q.grade,
        subject=q.subject,
        chapter=q.chapter,
        topic=q.topic_name,
        difficulty=q.difficulty,  # type: ignore[arg-type]
        marks=q.marks,
        question_type=q.question_type,  # type: ignore[arg-type]
        text=q.text or "",
        status=q.status,  # type: ignore[arg-type]
        option_a=q.option_a,
        option_b=q.option_b,
        option_c=q.option_c,
        option_d=q.option_d,
        correct_answer=None if hide_answer else q.correct_answer,
        text_image_key=q.text_image_key,
        option_a_image_key=q.option_a_image_key,
        option_b_image_key=q.option_b_image_key,
        option_c_image_key=q.option_c_image_key,
        option_d_image_key=q.option_d_image_key,
        text_image_url=media_svc.media_url_for_key(q.text_image_key),
        option_a_image_url=media_svc.media_url_for_key(q.option_a_image_key),
        option_b_image_url=media_svc.media_url_for_key(q.option_b_image_key),
        option_c_image_url=media_svc.media_url_for_key(q.option_c_image_key),
        option_d_image_url=media_svc.media_url_for_key(q.option_d_image_key),
    )


def _paper_out(p: QuestionPaper) -> QuestionPaperOut:
    subjects = subjects_from_stored(p.subject, getattr(p, "subjects", None))
    paper_status = getattr(p, "status", None) or "published"
    if paper_status not in ("draft", "published"):
        paper_status = "published"
    return QuestionPaperOut(
        id=p.id,
        name=p.name,
        board=p.board,
        grade=p.grade,
        subject=primary_subject(subjects, p.subject),
        subjects=subjects,
        question_ids=from_json_list(p.question_ids),
        topics=from_json_list(p.topics),
        total_marks=p.total_marks,
        created_at=p.created_at,
        created_by=p.created_by,
        source=p.source,  # type: ignore[arg-type]
        parent_paper_id=p.parent_paper_id,
        status=paper_status,  # type: ignore[arg-type]
    )


@router.get("/questions", response_model=PaginatedOut[QuestionOut] | list[QuestionOut])
def list_questions(
    board: str | None = Query(None),
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedOut[QuestionOut] | list[QuestionOut]:
    q = db.query(Question).filter(Question.institution_id == user.institution_id)
    if board:
        q = q.filter(Question.board == board)
    if grade:
        q = q.filter(Question.grade == grade)
    if subject:
        q = q.filter(Question.subject == subject)
    if page is None and limit is None:
        return [_question_out(row) for row in q.all()]
    items, total, page_n, limit_n, pages = paginate_query(q, page or 1, limit)
    return PaginatedOut(
        items=[_question_out(row) for row in items],
        total=total,
        page=page_n,
        limit=limit_n,
        pages=pages,
    )


@router.get("/questions/{question_id}", response_model=QuestionOut)
def get_question(
    question_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QuestionOut:
    q = db.get(Question, question_id)
    if not q or q.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    return _question_out(q)


def _normalize_grade(grade: str) -> str:
    return grade if grade.startswith("Grade") else f"Grade {grade}"


def _persist_question(
    db: Session,
    institution_id: str,
    body: QuestionCreate,
    *,
    question_status: str = "active",
) -> Question:
    topic = _find_or_create_topic(
        db,
        institution_id,
        body.board,
        body.grade,
        body.subject,
        body.topic,
        chapter_name=body.chapter,
    )
    for key in (
        body.text_image_key,
        body.option_a_image_key,
        body.option_b_image_key,
        body.option_c_image_key,
        body.option_d_image_key,
    ):
        if key:
            media_svc.assert_key_owned(key, institution_id)
    qid = f"q-{uuid.uuid4().hex[:8]}"
    question = Question(
        id=qid,
        topic_id=topic.id,
        institution_id=institution_id,
        board=body.board,
        grade=_normalize_grade(body.grade),
        subject=body.subject,
        chapter=body.chapter,
        topic_name=body.topic,
        text=(body.text or "").strip() or ("(image)" if body.text_image_key else ""),
        difficulty=body.difficulty,
        marks=body.marks,
        question_type=body.question_type,
        status=question_status,
        option_a=body.option_a,
        option_b=body.option_b,
        option_c=body.option_c,
        option_d=body.option_d,
        correct_answer=body.correct_answer,
        text_image_key=body.text_image_key,
        option_a_image_key=body.option_a_image_key,
        option_b_image_key=body.option_b_image_key,
        option_c_image_key=body.option_c_image_key,
        option_d_image_key=body.option_d_image_key,
    )
    db.add(question)
    return question


def _validate_bulk_questions(questions: list[QuestionCreate], *, strict: bool) -> None:
    if strict and not questions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one question is required")
    for idx, q_body in enumerate(questions, start=1):
        has_stem = bool((q_body.text or "").strip() or q_body.text_image_key)
        if strict and not has_stem:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: text or stem image is required",
            )
        if strict and not (q_body.chapter or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: chapter is required",
            )
        if q_body.question_type == "mcq" and strict:
            has_a = bool((q_body.option_a or "").strip() or q_body.option_a_image_key)
            has_b = bool((q_body.option_b or "").strip() or q_body.option_b_image_key)
            if not has_a or not has_b:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {idx}: MCQs require options A and B (text or image)",
                )
            if not q_body.correct_answer:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {idx}: correct answer is required for MCQs",
                )


@router.post("/questions", response_model=QuestionOut, status_code=status.HTTP_201_CREATED)
def create_question(
    body: QuestionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionOut:
    question = _persist_question(db, user.institution_id, body)
    db.commit()
    db.refresh(question)
    return _question_out(question)


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def update_question(
    question_id: str,
    body: QuestionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionOut:
    q = db.get(Question, question_id)
    if not q or q.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    for field, attr in [
        ("text", "text"),
        ("difficulty", "difficulty"),
        ("marks", "marks"),
        ("status", "status"),
        ("option_a", "option_a"),
        ("option_b", "option_b"),
        ("option_c", "option_c"),
        ("option_d", "option_d"),
        ("correct_answer", "correct_answer"),
        ("text_image_key", "text_image_key"),
        ("option_a_image_key", "option_a_image_key"),
        ("option_b_image_key", "option_b_image_key"),
        ("option_c_image_key", "option_c_image_key"),
        ("option_d_image_key", "option_d_image_key"),
    ]:
        val = getattr(body, field)
        if val is not None:
            if field.endswith("_image_key") and val:
                media_svc.assert_key_owned(val, user.institution_id)
            setattr(q, attr, val)
    db.commit()
    return _question_out(q)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    question_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    q = db.get(Question, question_id)
    if q:
        db.delete(q)
        db.commit()


@router.get("/question-papers", response_model=PaginatedOut[QuestionPaperOut] | list[QuestionPaperOut])
def list_question_papers(
    board: str | None = Query(None),
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedOut[QuestionPaperOut] | list[QuestionPaperOut]:
    q = db.query(QuestionPaper).filter(QuestionPaper.institution_id == user.institution_id)
    if board:
        q = q.filter(QuestionPaper.board == board)
    if grade:
        q = q.filter(QuestionPaper.grade == grade)
    # Default to published so assessment pickers exclude drafts; pass status=all|draft to include.
    want_status = (status_filter or "published").strip().lower()
    if want_status == "draft":
        q = q.filter(QuestionPaper.status == "draft")
    elif want_status == "all":
        pass
    else:
        q = q.filter((QuestionPaper.status == "published") | (QuestionPaper.status.is_(None)))
    q = q.order_by(QuestionPaper.created_at.desc(), QuestionPaper.id.desc())
    rows = q.all()
    if subject:
        want = normalize_subjects(subject)
        rows = [p for p in rows if subjects_overlap(want, subjects_from_stored(p.subject, getattr(p, "subjects", None)))]
    if page is None and limit is None:
        return [_paper_out(p) for p in rows]
    total = len(rows)
    page_n = page or 1
    limit_n = limit or 20
    pages = max(1, (total + limit_n - 1) // limit_n) if total else 1
    start = (page_n - 1) * limit_n
    items = rows[start : start + limit_n]
    return PaginatedOut(
        items=[_paper_out(p) for p in items],
        total=total,
        page=page_n,
        limit=limit_n,
        pages=pages,
    )


@router.get("/question-papers/{paper_id}", response_model=QuestionPaperOut)
def get_question_paper(
    paper_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QuestionPaperOut:
    paper = db.get(QuestionPaper, paper_id)
    if not paper or paper.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return _paper_out(paper)


@router.post("/question-papers", response_model=QuestionPaperOut, status_code=status.HTTP_201_CREATED)
def create_question_paper(
    body: QuestionPaperCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionPaperOut:
    questions = (
        db.query(Question)
        .filter(Question.id.in_(body.question_ids), Question.institution_id == user.institution_id)
        .all()
    )
    topics = sorted({q.topic_name for q in questions})
    total = sum(q.marks for q in questions)
    subjects = normalize_subjects(body.subjects, body.subject, [q.subject for q in questions])
    paper_status = body.status if body.status in ("draft", "published") else "published"
    paper = QuestionPaper(
        id=f"qp-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name,
        board=body.board,
        grade=body.grade,
        subject=primary_subject(subjects, body.subject),
        subjects=subjects_json(subjects),
        question_ids=to_json_list(body.question_ids),
        topics=to_json_list(topics),
        total_marks=total,
        created_at=date.today().isoformat(),
        created_by=user.id,
        source=body.source,
        parent_paper_id=body.parent_paper_id,
        status=paper_status,
    )
    db.add(paper)
    db.commit()
    return _paper_out(paper)


def _replace_paper_questions(
    db: Session,
    *,
    institution_id: str,
    paper: QuestionPaper,
    question_bodies: list[QuestionCreate],
    question_status: str,
) -> list[Question]:
    """Delete prior paper-owned draft questions and recreate from payload."""
    old_ids = from_json_list(paper.question_ids)
    if old_ids:
        old_qs = (
            db.query(Question)
            .filter(Question.id.in_(old_ids), Question.institution_id == institution_id)
            .all()
        )
        for oq in old_qs:
            if (oq.status or "active") == "draft":
                db.delete(oq)
    filled = fill_blank_question_topics(db, institution_id, list(question_bodies))
    created = [
        _persist_question(db, institution_id, q_body, question_status=question_status)
        for q_body in filled
    ]
    return created


@router.post("/question-papers/bulk", response_model=QuestionPaperOut, status_code=status.HTTP_201_CREATED)
def create_question_paper_bulk(
    body: QuestionPaperBulkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionPaperOut:
    paper_status = body.status if body.status in ("draft", "published") else "published"
    is_draft = paper_status == "draft"
    _validate_bulk_questions(body.questions, strict=not is_draft)

    if is_draft and not body.questions:
        # Allow empty draft shell — store paper metadata only.
        paper_name = (body.name or "").strip() or "Untitled draft"
        if body.paper_id:
            paper = db.get(QuestionPaper, body.paper_id)
            if not paper or paper.institution_id != user.institution_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
            if (getattr(paper, "status", None) or "published") != "draft":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only drafts can be upserted via bulk")
            paper.name = paper_name
            paper.question_ids = to_json_list([])
            paper.topics = to_json_list([])
            paper.total_marks = 0
            paper.status = "draft"
            db.commit()
            return _paper_out(paper)
        paper = QuestionPaper(
            id=f"qp-{uuid.uuid4().hex[:8]}",
            institution_id=user.institution_id,
            name=paper_name,
            board="",
            grade="",
            subject="",
            subjects=subjects_json([]),
            question_ids=to_json_list([]),
            topics=to_json_list([]),
            total_marks=0,
            created_at=date.today().isoformat(),
            created_by=user.id,
            source=body.source,
            status="draft",
        )
        db.add(paper)
        db.commit()
        return _paper_out(paper)

    q_status = "draft" if is_draft else "active"

    if body.paper_id:
        paper = db.get(QuestionPaper, body.paper_id)
        if not paper or paper.institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
        if (getattr(paper, "status", None) or "published") != "draft" and is_draft:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot overwrite a published paper as draft")
        created = _replace_paper_questions(
            db,
            institution_id=user.institution_id,
            paper=paper,
            question_bodies=list(body.questions),
            question_status=q_status,
        )
        db.flush()
        first = created[0]
        subjects = normalize_subjects([q.subject for q in created])
        paper.name = (body.name or "").strip() or paper.name
        paper.board = first.board
        paper.grade = first.grade
        paper.subject = primary_subject(subjects, first.subject)
        paper.subjects = subjects_json(subjects)
        paper.question_ids = to_json_list([q.id for q in created])
        paper.topics = to_json_list(sorted({q.topic_name for q in created}))
        paper.total_marks = sum(q.marks for q in created)
        paper.source = body.source
        paper.status = paper_status
        db.commit()
        return _paper_out(paper)

    filled = fill_blank_question_topics(db, user.institution_id, list(body.questions))
    created = [
        _persist_question(db, user.institution_id, q_body, question_status=q_status)
        for q_body in filled
    ]
    db.flush()
    first = created[0]
    board = first.board
    grade = first.grade
    subjects = normalize_subjects([q.subject for q in created])
    subject = primary_subject(subjects, first.subject)
    question_ids = [q.id for q in created]
    topics = sorted({q.topic_name for q in created})
    total = sum(q.marks for q in created)

    paper = QuestionPaper(
        id=f"qp-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=(body.name or "").strip() or ("Untitled draft" if is_draft else "Untitled paper"),
        board=board,
        grade=grade,
        subject=subject,
        subjects=subjects_json(subjects),
        question_ids=to_json_list(question_ids),
        topics=to_json_list(topics),
        total_marks=total,
        created_at=date.today().isoformat(),
        created_by=user.id,
        source=body.source,
        status=paper_status,
    )
    db.add(paper)
    db.commit()
    return _paper_out(paper)


@router.post("/question-papers/custom", response_model=QuestionPaperOut, status_code=status.HTTP_201_CREATED)
def create_custom_paper(
    body: CustomPaperCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionPaperOut:
    parent = db.get(QuestionPaper, body.parent_paper_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent paper not found")
    if (getattr(parent, "status", None) or "published") == "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create a custom paper from a draft parent",
        )
    parent_subjects = subjects_from_stored(parent.subject, getattr(parent, "subjects", None))
    return create_question_paper(
        QuestionPaperCreate(
            name=body.name,
            board=parent.board,
            grade=parent.grade,
            subject=primary_subject(parent_subjects, parent.subject),
            subjects=parent_subjects,
            question_ids=body.question_ids,
            source="custom",
            parent_paper_id=parent.id,
            status="published",
        ),
        db,
        user,
    )


@router.patch("/question-papers/{paper_id}", response_model=QuestionPaperOut)
def update_question_paper(
    paper_id: str,
    body: QuestionPaperUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionPaperOut:
    paper = db.get(QuestionPaper, paper_id)
    if not paper or paper.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    if body.name is not None:
        paper.name = body.name.strip()
    if body.questions is not None:
        paper_status = getattr(paper, "status", None) or "published"
        q_status = "draft" if paper_status == "draft" else "active"
        if body.status == "draft":
            q_status = "draft"
        _validate_bulk_questions(body.questions, strict=paper_status != "draft" and body.status != "draft")
        created = _replace_paper_questions(
            db,
            institution_id=user.institution_id,
            paper=paper,
            question_bodies=list(body.questions),
            question_status=q_status,
        )
        db.flush()
        if created:
            first = created[0]
            subjects = normalize_subjects([q.subject for q in created])
            paper.board = first.board
            paper.grade = first.grade
            paper.subject = primary_subject(subjects, first.subject)
            paper.subjects = subjects_json(subjects)
            paper.question_ids = to_json_list([q.id for q in created])
            paper.topics = to_json_list(sorted({q.topic_name for q in created}))
            paper.total_marks = sum(q.marks for q in created)
        else:
            paper.question_ids = to_json_list([])
            paper.topics = to_json_list([])
            paper.total_marks = 0
    elif body.question_ids is not None:
        questions = (
            db.query(Question)
            .filter(Question.id.in_(body.question_ids), Question.institution_id == user.institution_id)
            .all()
        )
        if len(questions) != len(body.question_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid question IDs")
        paper.question_ids = to_json_list(body.question_ids)
        paper.topics = to_json_list(sorted({q.topic_name for q in questions}))
        paper.total_marks = sum(q.marks for q in questions)
    if body.status is not None:
        if body.status == "published":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use POST /question-papers/{id}/publish to publish a draft",
            )
        paper.status = body.status
    db.commit()
    return _paper_out(paper)


@router.post("/question-papers/{paper_id}/publish", response_model=QuestionPaperOut)
def publish_question_paper(
    paper_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionPaperOut:
    paper = db.get(QuestionPaper, paper_id)
    if not paper or paper.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    qids = from_json_list(paper.question_ids)
    if not qids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot publish an empty paper")
    questions = (
        db.query(Question)
        .filter(Question.id.in_(qids), Question.institution_id == user.institution_id)
        .all()
    )
    if len(questions) != len(qids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Paper has missing questions")
    # Re-validate like bulk publish
    for idx, q in enumerate(questions, start=1):
        has_stem = bool((q.text or "").strip() and q.text != "(image)") or bool(q.text_image_key)
        if not has_stem and not q.text_image_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: text or stem image is required",
            )
        if not (q.chapter or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: chapter is required",
            )
        if q.question_type == "mcq":
            has_a = bool((q.option_a or "").strip() or q.option_a_image_key)
            has_b = bool((q.option_b or "").strip() or q.option_b_image_key)
            if not has_a or not has_b:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {idx}: MCQs require options A and B",
                )
            if not q.correct_answer:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {idx}: correct answer is required for MCQs",
                )
        q.status = "active"
    paper.status = "published"
    paper.total_marks = sum(q.marks for q in questions)
    paper.topics = to_json_list(sorted({q.topic_name for q in questions}))
    db.commit()
    return _paper_out(paper)


@router.delete("/question-papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question_paper(
    paper_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    paper = db.get(QuestionPaper, paper_id)
    if paper:
        # Soft-clean draft questions owned by this paper
        qids = from_json_list(paper.question_ids)
        if qids and (getattr(paper, "status", None) or "published") == "draft":
            for oq in (
                db.query(Question)
                .filter(Question.id.in_(qids), Question.institution_id == user.institution_id)
                .all()
            ):
                if (oq.status or "active") == "draft":
                    db.delete(oq)
        db.delete(paper)
        db.commit()
