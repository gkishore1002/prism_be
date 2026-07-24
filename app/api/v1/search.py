from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload
from app.core.routing import CamelCaseAPIRoute
from app.models.academic import Board, Chapter, Grade, Question, Subject, Topic
from app.models.content import QuestionPaper
from app.models.user import StudentProfile, User
from app.schemas import SearchResultItem, SearchResultsOut

router = APIRouter(prefix="/search", tags=["search"], route_class=CamelCaseAPIRoute)


def _portal_prefix(role: str) -> str:
    if role == "admin":
        return "/admin"
    if role == "tutor":
        return "/tutor"
    return "/student"


@router.get("", response_model=SearchResultsOut)
def search_portal(
    q: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> SearchResultsOut:
    query = q.strip()
    if not query:
        return SearchResultsOut(query=q, students=[], topics=[], questions=[], papers=[])

    role = get_effective_role(payload, user)
    prefix = _portal_prefix(role)
    pattern = f"%{query}%"
    per_kind = max(2, limit // 4)

    students: list[SearchResultItem] = []
    if role in ("admin", "tutor"):
        rows = (
            db.query(StudentProfile, User)
            .join(User)
            .filter(User.institution_id == user.institution_id)
            .filter(
                (User.name.ilike(pattern))
                | (StudentProfile.batch.ilike(pattern))
                | (StudentProfile.grade.ilike(pattern))
            )
            .limit(per_kind)
            .all()
        )
        for profile, u in rows:
            href = (
                f"{prefix}/students/{profile.id}/report"
                if role == "admin"
                else f"{prefix}/students"
            )
            students.append(
                SearchResultItem(
                    id=profile.id,
                    kind="student",
                    title=u.name,
                    subtitle=f"{profile.board} · {profile.grade} · {profile.batch or 'No batch'}",
                    href=href,
                )
            )

    topic_rows = (
        db.query(Topic, Board.name, Grade.name, Subject.name)
        .join(Chapter, Topic.chapter_id == Chapter.id)
        .join(Subject, Chapter.subject_id == Subject.id)
        .join(Grade, Subject.grade_id == Grade.id)
        .join(Board, Grade.board_id == Board.id)
        .filter(Board.institution_id == user.institution_id)
        .filter(Topic.name.ilike(pattern))
        .limit(per_kind)
        .all()
    )
    topics = [
        SearchResultItem(
            id=topic.id,
            kind="topic",
            title=topic.name,
            subtitle=f"{board_name} · {grade_name} · {subject_name}",
            href=f"{prefix}/curriculum" if role != "student" else f"{prefix}/reports",
        )
        for topic, board_name, grade_name, subject_name in topic_rows
    ]

    question_rows = (
        db.query(Question)
        .filter(Question.institution_id == user.institution_id)
        .filter(
            (Question.text.ilike(pattern))
            | (Question.topic_name.ilike(pattern))
            | (Question.chapter.ilike(pattern))
        )
        .limit(per_kind)
        .all()
    )
    questions = [
        SearchResultItem(
            id=q_row.id,
            kind="question",
            title=(q_row.text[:120] + "…") if len(q_row.text) > 120 else q_row.text,
            subtitle=f"{q_row.board} · {q_row.grade} · {q_row.topic_name}",
            href=f"{prefix}/question-bank" if role != "student" else f"{prefix}/assessments",
        )
        for q_row in question_rows
    ]

    papers: list[SearchResultItem] = []
    if role in ("admin", "tutor"):
        paper_rows = (
            db.query(QuestionPaper)
            .filter(QuestionPaper.institution_id == user.institution_id)
            .filter(QuestionPaper.name.ilike(pattern))
            .limit(per_kind)
            .all()
        )
        papers = [
            SearchResultItem(
                id=paper.id,
                kind="paper",
                title=paper.name,
                subtitle=f"{paper.board} · {paper.grade} · {paper.subject}",
                href=f"{prefix}/question-bank/papers/{paper.id}",
            )
            for paper in paper_rows
        ]

    return SearchResultsOut(
        query=query,
        students=students,
        topics=topics,
        questions=questions,
        papers=papers,
    )
