"""Upload textbooks, extract chapter/topic JSON via Vertex, map question topics."""

from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models.content import SyllabusBook
from app.services import vertex_summary as vertex_svc

logger = logging.getLogger(__name__)

INLINE_PDF_CAP = 20 * 1024 * 1024
SUPPORTED_BOOK_EXTENSIONS = {"pdf", "txt"}


def normalize_grade(grade: str) -> str:
    value = (grade or "").strip()
    if not value:
        return value
    return value if value.lower().startswith("grade") else f"Grade {value}"


def _fold_label(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def boards_match(a: str, b: str) -> bool:
    return _fold_label(a) == _fold_label(b)


def grades_match(a: str, b: str) -> bool:
    return normalize_grade(a).lower() == normalize_grade(b).lower()


def subjects_match(a: str, b: str) -> bool:
    """Case/space-insensitive; also match 'Python' ↔ 'Python Programming'."""
    left, right = _fold_label(a), _fold_label(b)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 4:
        return False
    return shorter in longer


def books_for_scope(
    db: Session, institution_id: str, board: str, grade: str, subject: str
) -> list[SyllabusBook]:
    rows = (
        db.query(SyllabusBook)
        .filter(
            SyllabusBook.institution_id == institution_id,
            SyllabusBook.status == "analyzed",
        )
        .all()
    )
    return [
        row
        for row in rows
        if boards_match(row.board, board)
        and grades_match(row.grade, grade)
        and subjects_match(row.subject, subject)
    ]


def missing_book_message(
    db: Session, institution_id: str, board: str, grade: str, subject: str
) -> str:
    scope = f"{board} · {grade} · {subject}"
    rows = (
        db.query(SyllabusBook)
        .filter(SyllabusBook.institution_id == institution_id)
        .order_by(SyllabusBook.created_at.desc())
        .all()
    )
    same_scope = [
        row
        for row in rows
        if boards_match(row.board, board)
        and grades_match(row.grade, grade)
        and subjects_match(row.subject, subject)
    ]
    analyzing = next((row for row in same_scope if row.status == "analyzing"), None)
    if analyzing:
        return (
            f'Syllabus book "{analyzing.title}" for {scope} is still being summarized. '
            "Wait until status is Analyzed, then click Update topics."
        )
    failed = next((row for row in same_scope if row.status == "failed"), None)
    if failed:
        reason = (failed.error_message or "summarization failed").strip()
        return (
            f'Syllabus book "{failed.title}" for {scope} failed ({reason}). '
            "Re-upload the textbook in Question Bank → Books."
        )
    nearby = [
        row
        for row in rows
        if row.status == "analyzed"
        and boards_match(row.board, board)
        and grades_match(row.grade, grade)
    ]
    if nearby:
        labels = ", ".join(sorted({row.subject for row in nearby}))
        return (
            f"No analyzed syllabus book for {scope}. "
            f"Analyzed books for this grade are tagged: {labels}. "
            "Upload a book with this subject, or change the Subject column to match."
        )
    if rows:
        labels = ", ".join(
            sorted({f"{row.board} · {row.grade} · {row.subject}" for row in rows[:6]})
        )
        return (
            f"No analyzed syllabus book for {scope}. "
            f"Uploaded books: {labels}. Board, grade, and subject must match the questions."
        )
    return (
        f"No analyzed syllabus book for {scope}. "
        "Upload a textbook in Question Bank → Books first, then wait until it is Analyzed."
    )


def pdf_to_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_to_text_failed: %s", exc)
        return ""


def prepare_book_for_llm(content: bytes, filename: str) -> tuple[bytes | None, str]:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext == "txt":
        text = content.decode("utf-8", errors="ignore")
        return None, text
    pdf_bytes = content if len(content) <= INLINE_PDF_CAP else None
    text = pdf_to_text(content)
    return pdf_bytes, text


def persist_outline_to_curriculum(
    db: Session,
    institution_id: str,
    board: str,
    grade: str,
    subject: str,
    chapters: list[dict],
) -> None:
    from app.api.v1.curriculum import _find_or_create_topic

    for chapter in chapters:
        title = str(chapter.get("title") or "").strip()
        if not title:
            continue
        topics = chapter.get("topics") or []
        if not topics:
            _find_or_create_topic(db, institution_id, board, grade, subject, title, chapter_name=title)
            continue
        for topic in topics:
            name = str(topic).strip()
            if name:
                _find_or_create_topic(
                    db, institution_id, board, grade, subject, name, chapter_name=title
                )


def analyze_book(db: Session, book: SyllabusBook, content: bytes, filename: str) -> None:
    try:
        pdf_bytes, pdf_text = prepare_book_for_llm(content, filename)
        if not pdf_bytes and not (pdf_text or "").strip():
            raise RuntimeError("Could not read any text from the uploaded file. Use PDF or TXT.")
        outline = vertex_svc.extract_book_outline(
            subject_name=book.subject,
            pdf_bytes=pdf_bytes,
            pdf_text=pdf_text,
        )
        persist_outline_to_curriculum(
            db, book.institution_id, book.board, book.grade, book.subject, outline["chapters"]
        )
        book.analysis_json = json.dumps(outline)
        book.status = "analyzed"
        book.error_message = ""
        db.add(book)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("syllabus_book_extract_failed book_id=%s", book.id)
        book.status = "failed"
        book.error_message = str(exc)[:500]
        db.add(book)
        db.commit()


def create_book_record(
    db: Session,
    *,
    institution_id: str,
    board: str,
    grade: str,
    subject: str,
    title: str,
    filename: str,
    created_by: str | None,
) -> SyllabusBook:
    book = SyllabusBook(
        id=f"bk-{uuid.uuid4().hex[:8]}",
        institution_id=institution_id,
        board=board.strip(),
        grade=normalize_grade(grade),
        subject=subject.strip(),
        title=title.strip() or filename.rsplit(".", 1)[0],
        filename=filename,
        status="analyzing",
        analysis_json="{}",
        error_message="",
        created_by=created_by,
        created_at=date.today().isoformat(),
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def merge_outlines(books: list[SyllabusBook]) -> dict:
    chapters: list[dict] = []
    seen: set[str] = set()
    for book in books:
        try:
            data = json.loads(book.analysis_json or "{}")
        except json.JSONDecodeError:
            data = {}
        for ch in data.get("chapters") or []:
            title = str(ch.get("title") or "").strip()
            key = title.lower()
            if not title or key in seen:
                if key in seen:
                    existing = next((c for c in chapters if c["title"].lower() == key), None)
                    if existing:
                        for topic in ch.get("topics") or []:
                            t = str(topic).strip()
                            if t and t not in existing["topics"]:
                                existing["topics"].append(t)
                continue
            seen.add(key)
            topics = [str(t).strip() for t in (ch.get("topics") or []) if str(t).strip()]
            chapters.append({"title": title, "topics": topics})
    return {"chapters": chapters}


def fill_blank_question_topics(
    db: Session,
    institution_id: str,
    questions: list,
) -> list:
    """Assign topics for questions that have none, using analyzed syllabus books.

    Falls back to the chapter name when no book/mapping is available.
    `questions` are pydantic models with board/grade/subject/chapter/text/topic.
    """
    from app.services import vertex_summary as vertex_svc

    pending = [i for i, q in enumerate(questions) if not str(getattr(q, "topic", "") or "").strip()]
    if not pending:
        return questions

    mapped: dict[int, str] = {}
    groups: dict[tuple[str, str, str], list[int]] = {}
    for i in pending:
        q = questions[i]
        key = (str(q.board).strip(), normalize_grade(str(q.grade)), str(q.subject).strip())
        groups.setdefault(key, []).append(i)

    for (board, grade, subject), idxs in groups.items():
        books = books_for_scope(db, institution_id, board, grade, subject)
        if not books:
            continue
        outline = merge_outlines(books)
        payload = [
            {
                "row": i,
                "chapter": str(questions[i].chapter or ""),
                "text": str(questions[i].text or ""),
                "topic": "",
            }
            for i in idxs
        ]
        try:
            raw = vertex_svc.map_question_topics(outline, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fill_blank_question_topics_failed %s/%s/%s: %s", board, grade, subject, exc)
            continue
        for item in raw:
            topic = str(item.get("topic") or "").strip()
            row = item.get("row")
            if topic and row is not None:
                mapped[int(row)] = topic

    filled = []
    for i, q in enumerate(questions):
        topic = str(getattr(q, "topic", "") or "").strip() or mapped.get(i) or str(q.chapter or "").strip()
        if topic != str(getattr(q, "topic", "") or "").strip() and hasattr(q, "model_copy"):
            q = q.model_copy(update={"topic": topic})
        filled.append(q)
    return filled


