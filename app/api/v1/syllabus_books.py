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
from app.schemas import (
    SyllabusBookOut,
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
    )


def _extract_in_background(
    book_id: str,
    content: bytes,
    filename: str,
    schema_name: str | None,
    institution_id: str,
) -> None:
    tokens = set_tenant_context(schema_name=schema_name or "public", institution_id=institution_id)
    db = open_tenant_db(schema_name)
    try:
        book = db.get(SyllabusBook, book_id)
        if not book:
            return
        books_svc.analyze_book(db, book, content, filename)
    except Exception:  # noqa: BLE001
        logger.exception("syllabus_book_background_failed book_id=%s", book_id)
    finally:
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
