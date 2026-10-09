"""Syllabus book upload (Vertex outline JSON) and question topic mapping."""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.content import SyllabusBook
from app.models.user import User
from app.models.institution import Institution
from app.schemas import (
    ApproveMcqsRequest,
    ApproveMcqsResponse,
    GenerateMcqsRequest,
    GenerateMcqsResponse,
    GeneratedMcqOut,
    McqTopicSelection,
    QuestionCreate,
    SyllabusBookOut,
    SyllabusOutlineApprove,
    SyllabusOutlineUpdate,
    TopicMapItemOut,
    TopicMapRequest,
    TopicMapResponse,
)
from app.services import syllabus_books as books_svc
from app.services import vertex_summary as vertex_svc
from app.services.tenant_context import (
    close_tenant_db,
    open_tenant_db,
    safe_reset_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["syllabus-books"], route_class=CamelCaseAPIRoute)

MAX_BOOK_BYTES = 20 * 1024 * 1024


def _outline_counts(raw: str) -> tuple[int, int, dict | None]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return 0, 0, None
    if not isinstance(data, dict):
        return 0, 0, None
    chapters = data.get("chapters") or []
    topic_count = sum(len(ch.get("topics") or []) for ch in chapters if isinstance(ch, dict))
    return len(chapters), topic_count, data


def _book_out(book: SyllabusBook, *, include_json: bool = False) -> SyllabusBookOut:
    chapter_count, topic_count, data = _outline_counts(book.analysis_json)
    return SyllabusBookOut(
        id=book.id,
        board=book.board,
        grade=book.grade,
        subject=book.subject,
        title=book.title,
        filename=book.filename,
        status=book.status,  # type: ignore[arg-type]
        analysis_json=data if include_json and book.status == "analyzed" else None,
        error_message=book.error_message or "",
        created_by=book.created_by,
        created_at=book.created_at,
        chapter_count=chapter_count,
        topic_count=topic_count,
        has_source_text=bool((getattr(book, "source_text", None) or "").strip()),
    )


def _require_ai_mcq_premium(db: Session, institution_id: str) -> None:
    inst = db.get(Institution, institution_id)
    if not inst or not bool(getattr(inst, "ai_mcq_from_books", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI MCQ generation from books is a premium feature. Ask your platform admin to enable it.",
        )


def _extract_in_background(
    book_id: str,
    content: bytes,
    filename: str,
    schema_name: str | None,
    institution_id: str,
) -> None:
    from app.services.llm_usage import set_current_institution_id

    tokens = set_tenant_context(schema_name=schema_name or "public", institution_id=institution_id)
    set_current_institution_id(institution_id)
    db = open_tenant_db(schema_name)
    try:
        book = db.get(SyllabusBook, book_id)
        if not book:
            return
        books_svc.analyze_book(db, book, content, filename)
    except Exception:  # noqa: BLE001
        logger.exception("syllabus_book_background_failed book_id=%s", book_id)
    finally:
        set_current_institution_id(None)
        close_tenant_db(db)
        safe_reset_tenant_context(tokens)


@router.get("/syllabus-books", response_model=list[SyllabusBookOut])
def list_syllabus_books(
    board: str | None = Query(None),
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> list[SyllabusBookOut]:
    q = db.query(SyllabusBook).filter(SyllabusBook.institution_id == user.institution_id)
    if board:
        q = q.filter(SyllabusBook.board == board)
    if grade:
        q = q.filter(SyllabusBook.grade == books_svc.normalize_grade(grade))
    if subject:
        q = q.filter(SyllabusBook.subject == subject)
    rows = q.order_by(SyllabusBook.created_at.desc()).all()
    return [_book_out(row) for row in rows]


@router.get("/syllabus-books/{book_id}", response_model=SyllabusBookOut)
def get_syllabus_book(
    book_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> SyllabusBookOut:
    book = db.get(SyllabusBook, book_id)
    if not book or book.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return _book_out(book, include_json=True)


@router.post("/syllabus-books", response_model=SyllabusBookOut, status_code=status.HTTP_201_CREATED)
async def upload_syllabus_book(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    board: str = Form(...),
    grade: str = Form(...),
    subject: str = Form(...),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> SyllabusBookOut:
    filename = file.filename or "book.pdf"
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in books_svc.SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Upload a PDF or TXT textbook.",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(content) > MAX_BOOK_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is too large. Maximum size is 20 MB.",
        )
    if not settings.vertex_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vertex AI is disabled. Enable VERTEX to summarize books.",
        )

    book = books_svc.create_book_record(
        db,
        institution_id=user.institution_id,
        board=board,
        grade=grade,
        subject=subject,
        title=(title or "").strip() or filename.rsplit(".", 1)[0],
        filename=filename,
        created_by=user.id,
    )
    schema_name = getattr(request.state, "tenant_schema", None)
    background_tasks.add_task(
        _extract_in_background,
        book.id,
        content,
        filename,
        schema_name,
        user.institution_id,
    )
    return _book_out(book)


@router.delete("/syllabus-books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_syllabus_book(
    book_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    book = db.get(SyllabusBook, book_id)
    if book and book.institution_id == user.institution_id:
        db.delete(book)
        db.commit()


def _require_analyzed_book(db: Session, book_id: str, institution_id: str) -> SyllabusBook:
    book = db.get(SyllabusBook, book_id)
    if not book or book.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    if book.status != "analyzed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Book has not been analyzed yet. Wait until status is Analyzed.",
        )
    return book


@router.put("/syllabus-books/{book_id}/outline", response_model=SyllabusBookOut)
def update_syllabus_book_outline(
    book_id: str,
    body: SyllabusOutlineUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> SyllabusBookOut:
    """Save user-edited chapters/topics and sync any new topics into curriculum."""
    book = _require_analyzed_book(db, book_id, user.institution_id)
    if not body.chapters:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one chapter before saving the outline.",
        )
    outline = books_svc.save_book_outline(
        db, book, [chapter.model_dump() for chapter in body.chapters]
    )
    books_svc.persist_outline_to_curriculum(
        db,
        user.institution_id,
        book.board,
        book.grade,
        book.subject,
        outline["chapters"],
    )
    db.commit()
    db.refresh(book)
    return _book_out(book, include_json=True)


@router.post("/syllabus-books/{book_id}/approve", response_model=SyllabusBookOut)
def approve_syllabus_book(
    book_id: str,
    body: SyllabusOutlineApprove = SyllabusOutlineApprove(),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> SyllabusBookOut:
    """Approve summarized outline (optionally after edits) and sync topics to curriculum."""
    book = _require_analyzed_book(db, book_id, user.institution_id)
    if body.chapters is not None:
        if not body.chapters:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Add at least one chapter before approving.",
            )
        outline = books_svc.save_book_outline(
            db, book, [chapter.model_dump() for chapter in body.chapters]
        )
        chapters = outline["chapters"]
    else:
        _, _, data = _outline_counts(book.analysis_json)
        chapters = (data or {}).get("chapters") or []
    if not chapters:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No chapters found in the book outline.",
        )
    books_svc.persist_outline_to_curriculum(
        db, user.institution_id, book.board, book.grade, book.subject, chapters
    )
    db.commit()
    db.refresh(book)
    return _book_out(book, include_json=True)


@router.post("/syllabus-books/{book_id}/import-topics", status_code=status.HTTP_200_OK)
def import_book_topics_to_curriculum(
    book_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    """Re-run persist_outline_to_curriculum for an already-analyzed book.

    Idempotent — topics that already exist are skipped by _find_or_create_topic.
    """
    book = _require_analyzed_book(db, book_id, user.institution_id)
    _, _, data = _outline_counts(book.analysis_json)
    chapters = (data or {}).get("chapters") or []
    if not chapters:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No chapters found in the book outline.",
        )
    topics_added = books_svc.persist_outline_to_curriculum(
        db, user.institution_id, book.board, book.grade, book.subject, chapters
    )
    db.commit()
    return {"status": "imported", "topicsAdded": topics_added}


@router.post("/syllabus-books/map-topics", response_model=TopicMapResponse)
def map_question_topics(
    body: TopicMapRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> TopicMapResponse:
    if not body.questions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No questions to map")

    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    for item in body.questions:
        if not item.chapter.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Row {item.row}: chapter is required before topics can be mapped",
            )
        key = (item.board.strip(), books_svc.normalize_grade(item.grade), item.subject.strip())
        groups[key].append(item)

    mappings: list[TopicMapItemOut] = []
    book_ids: list[str] = []
    used_heuristic = not settings.vertex_enabled

    for (board, grade, subject), items in groups.items():
        books = books_svc.books_for_scope(db, user.institution_id, board, grade, subject)
        if not books:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=books_svc.missing_book_message(
                    db, user.institution_id, board, grade, subject
                ),
            )
        book_ids.extend(b.id for b in books)
        outline = books_svc.merge_outlines(books)
        payload = [
            {
                "row": q.row,
                "chapter": q.chapter,
                "text": q.text,
                "topic": q.topic,
            }
            for q in items
        ]
        raw = vertex_svc.map_question_topics(outline, payload)
        for mapped in raw:
            mappings.append(
                TopicMapItemOut(
                    row=int(mapped["row"]),
                    topic=str(mapped.get("topic") or ""),
                    chapter=str(mapped.get("chapter") or ""),
                )
            )

    return TopicMapResponse(mappings=mappings, book_ids=book_ids, used_heuristic=used_heuristic)


def _resolve_mcq_selections(body: GenerateMcqsRequest) -> list[McqTopicSelection]:
    """Build unique chapter/topic pairs from multi-select and/or legacy fields."""
    seen: set[tuple[str, str]] = set()
    out: list[McqTopicSelection] = []
    for item in body.selections or []:
        chapter = (item.chapter or "").strip()
        topic = (item.topic or "").strip()
        if not chapter or not topic:
            continue
        key = (chapter.lower(), topic.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(McqTopicSelection(chapter=chapter, topic=topic))
    if not out:
        chapter = (body.chapter or "").strip()
        topic = (body.topic or "").strip()
        if chapter and topic:
            out.append(McqTopicSelection(chapter=chapter, topic=topic))
    return out[:20]


def _distribute_counts(total: int, buckets: int) -> list[int]:
    """Spread ``total`` questions across ``buckets`` (at least 1 each when possible)."""
    if buckets <= 0:
        return []
    total = max(1, int(total))
    if total < buckets:
        # Prefer one question on the first ``total`` selections.
        return [1 if i < total else 0 for i in range(buckets)]
    base, rem = divmod(total, buckets)
    return [base + (1 if i < rem else 0) for i in range(buckets)]


@router.post("/syllabus-books/{book_id}/generate-mcqs", response_model=GenerateMcqsResponse)
def generate_mcqs_from_book(
    book_id: str,
    body: GenerateMcqsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> GenerateMcqsResponse:
    """Premium: generate MCQ preview from topic-scoped book excerpts (not persisted)."""
    _require_ai_mcq_premium(db, user.institution_id)
    if not settings.vertex_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vertex AI is disabled. Enable VERTEX to generate MCQs.",
        )
    selections = _resolve_mcq_selections(body)
    if not selections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one chapter and topic.",
        )
    book = _require_analyzed_book(db, book_id, user.institution_id)
    source = (getattr(book, "source_text", None) or "").strip()
    if not source:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This book has no stored text for generation. Re-upload the PDF/TXT to enable AI MCQs.",
        )

    per_topic_counts = _distribute_counts(body.count, len(selections))
    questions: list[GeneratedMcqOut] = []
    for selection, n in zip(selections, per_topic_counts, strict=True):
        if n <= 0:
            continue
        excerpt = books_svc.slice_topic_excerpt(
            source, chapter=selection.chapter, topic=selection.topic
        )
        if not excerpt:
            continue
        raw_questions = vertex_svc.generate_mcqs_from_book_text(
            excerpt=excerpt,
            chapter=selection.chapter,
            topic=selection.topic,
            difficulty=body.difficulty,
            count=n,
            avoid_stems=body.avoid_stems,
        )
        for q in raw_questions or []:
            questions.append(
                GeneratedMcqOut(
                    text=q["text"],
                    option_a=q["optionA"],
                    option_b=q["optionB"],
                    option_c=q["optionC"],
                    option_d=q["optionD"],
                    correct_answer=q["correctAnswer"],  # type: ignore[arg-type]
                    marks=int(q.get("marks") or 1),
                    difficulty=q.get("difficulty") or body.difficulty,  # type: ignore[arg-type]
                    chapter=selection.chapter,
                    topic=selection.topic,
                )
            )

    if not questions:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI did not return usable MCQs. Try again or adjust chapters/topics.",
        )

    label_chapter = ", ".join(dict.fromkeys(s.chapter for s in selections))
    label_topic = ", ".join(dict.fromkeys(s.topic for s in selections))
    return GenerateMcqsResponse(
        book_id=book.id,
        board=book.board,
        grade=book.grade,
        subject=book.subject,
        chapter=label_chapter[:255],
        topic=label_topic[:255],
        difficulty=body.difficulty,
        questions=questions,
        selections=selections,
    )


@router.post(
    "/syllabus-books/{book_id}/approve-mcqs",
    response_model=ApproveMcqsResponse,
    status_code=status.HTTP_201_CREATED,
)
def approve_mcqs_from_book(
    book_id: str,
    body: ApproveMcqsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> ApproveMcqsResponse:
    """Premium: save edited MCQ preview into the question bank (no Vertex call)."""
    _require_ai_mcq_premium(db, user.institution_id)
    book = _require_analyzed_book(db, book_id, user.institution_id)
    from app.api.v1.questions import _persist_question

    question_ids: list[str] = []
    for idx, item in enumerate(body.questions, start=1):
        if not (item.text or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: text is required",
            )
        if not (item.option_a or "").strip() or not (item.option_b or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: options A and B are required",
            )
        if item.correct_answer not in {"A", "B", "C", "D"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: correct answer must be A, B, C, or D",
            )
        chapter = (item.chapter or body.chapter or "").strip()
        topic = (item.topic or body.topic or "").strip()
        if not chapter or not topic:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {idx}: chapter and topic are required",
            )
        create = QuestionCreate(
            board=book.board,
            grade=book.grade,
            subject=book.subject,
            chapter=chapter,
            topic=topic,
            text=item.text.strip(),
            difficulty=item.difficulty or body.difficulty,
            marks=max(1, min(5, int(item.marks or 1))),
            question_type="mcq",
            option_a=item.option_a.strip(),
            option_b=item.option_b.strip(),
            option_c=(item.option_c or "").strip() or None,
            option_d=(item.option_d or "").strip() or None,
            correct_answer=item.correct_answer,
        )
        question = _persist_question(
            db,
            user.institution_id,
            create,
            question_status=body.status,
        )
        question_ids.append(question.id)
    db.commit()
    return ApproveMcqsResponse(
        saved=len(question_ids),
        question_ids=question_ids,
        status=body.status,
    )
