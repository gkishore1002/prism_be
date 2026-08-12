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
        text=q.text,
        status=q.status,  # type: ignore[arg-type]
        option_a=q.option_a,
        option_b=q.option_b,
        option_c=q.option_c,
        option_d=q.option_d,
        correct_answer=None if hide_answer else q.correct_answer,
    )


def _paper_out(p: QuestionPaper) -> QuestionPaperOut:
    return QuestionPaperOut(
        id=p.id,
        name=p.name,
        board=p.board,
        grade=p.grade,
        subject=p.subject,
        question_ids=from_json_list(p.question_ids),
        topics=from_json_list(p.topics),
        total_marks=p.total_marks,
        created_at=p.created_at,
        created_by=p.created_by,
        source=p.source,  # type: ignore[arg-type]
        parent_paper_id=p.parent_paper_id,
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


def _persist_question(db: Session, institution_id: str, body: QuestionCreate) -> Question:
    topic = _find_or_create_topic(
        db, institution_id, body.board, body.grade, body.subject, body.topic
    )
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
        text=body.text,
        difficulty=body.difficulty,
        marks=body.marks,
        question_type=body.question_type,
        option_a=body.option_a,
        option_b=body.option_b,
        option_c=body.option_c,
        option_d=body.option_d,
        correct_answer=body.correct_answer,
    )
    db.add(question)
    return question


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
    ]:
        val = getattr(body, field)
        if val is not None:
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
    if subject:
        q = q.filter(QuestionPaper.subject == subject)
    if page is None and limit is None:
        return [_paper_out(p) for p in q.all()]
    items, total, page_n, limit_n, pages = paginate_query(q, page or 1, limit)
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
    paper = QuestionPaper(
        id=f"qp-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name,
        board=body.board,
        grade=body.grade,
        subject=body.subject,
        question_ids=to_json_list(body.question_ids),
        topics=to_json_list(topics),
        total_marks=total,
        created_at=date.today().isoformat(),
        created_by=user.id,
        source=body.source,
        parent_paper_id=body.parent_paper_id,
    )
    db.add(paper)
    db.commit()
    return _paper_out(paper)


@router.post("/question-papers/bulk", response_model=QuestionPaperOut, status_code=status.HTTP_201_CREATED)
def create_question_paper_bulk(
    body: QuestionPaperBulkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionPaperOut:
    if not body.questions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one question is required")

    created: list[Question] = []
    for idx, q_body in enumerate(body.questions, start=1):
        if not q_body.text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: text is required",
            )
        if q_body.question_type == "mcq":
            if not q_body.option_a or not q_body.option_b:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {idx}: MCQs require options A and B",
                )
            if not q_body.correct_answer:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {idx}: correct answer is required for MCQs",
                )
        created.append(_persist_question(db, user.institution_id, q_body))

    db.flush()
    first = created[0]
    board = first.board
    grade = first.grade
    subject = first.subject
    question_ids = [q.id for q in created]
    topics = sorted({q.topic_name for q in created})
    total = sum(q.marks for q in created)

    paper = QuestionPaper(
        id=f"qp-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name.strip(),
        board=board,
        grade=grade,
        subject=subject,
        question_ids=to_json_list(question_ids),
        topics=to_json_list(topics),
        total_marks=total,
        created_at=date.today().isoformat(),
        created_by=user.id,
        source=body.source,
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
    return create_question_paper(
        QuestionPaperCreate(
            name=body.name,
            board=parent.board,
            grade=parent.grade,
            subject=parent.subject,
            question_ids=body.question_ids,
            source="custom",
            parent_paper_id=parent.id,
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
    if body.question_ids is not None:
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
        db.delete(paper)
        db.commit()
