from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db, require_roles
from app.core.pagination import PaginatedOut, paginate_query
from app.core.routing import CamelCaseAPIRoute
from app.core.security import hash_password
from app.models.academic import Board, Chapter, Grade, Question, Subject, Topic
from app.models.assessment import AssessmentSubmission
from app.models.content import Batch, BatchStudent
from app.models.institution import Center
from app.models.user import StudentProfile, User
from app.schemas import (
    AddBoardRequest,
    AddGradeRequest,
    AddSubjectRequest,
    AddTopicRequest,
    CurriculumBoardOut,
    CurriculumGradeOut,
    CurriculumSubjectOut,
    CurriculumTopicOut,
    StudentCreate,
    StudentMasterOut,
    StudentSummaryOut,
    StudentUpdate,
    TutorBatchCreate,
    TutorBatchOut,
    TutorBatchUpdate,
    UpdateBoardRequest,
    UpdateGradeRequest,
    UpdateSubjectRequest,
    UpdateTopicRequest,
)

router = APIRouter(tags=["curriculum", "students", "batches"], route_class=CamelCaseAPIRoute)


def _slug(value: str) -> str:
    return value.lower().replace(" ", "-").replace("/", "-")


def _unique_row_id(db: Session, model: type, base_id: str) -> str:
    candidate = base_id
    suffix = 1
    while db.get(model, candidate):
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


def _unique_subject_id(db: Session, grade_id: str, subject_name: str) -> str:
    base = f"subj-{_slug(grade_id)}-{_slug(subject_name)}"
    return _unique_row_id(db, Subject, base)


def _build_curriculum_tree(db: Session, institution_id: str) -> list[CurriculumBoardOut]:
    from app.services.analytics_recompute import topic_mastery_rows

    mastery_by_topic = {
        row["topic_id"]: row["mastery"]
        for row in topic_mastery_rows(db, institution_id)
    }
    boards = db.query(Board).filter(Board.institution_id == institution_id).all()
    result: list[CurriculumBoardOut] = []
    for board in boards:
        grades_out: list[CurriculumGradeOut] = []
        for grade in board.grades:
            subjects_out: list[CurriculumSubjectOut] = []
            for subject in grade.subjects:
                topics_out: list[CurriculumTopicOut] = []
                for chapter in subject.chapters:
                    for topic in chapter.topics:
                        q_count = db.query(Question).filter(Question.topic_id == topic.id).count()
                        topics_out.append(
                            CurriculumTopicOut(
                                name=topic.name,
                                questions=q_count,
                                mastery=mastery_by_topic.get(topic.id, 0),
                            )
                        )
                subjects_out.append(CurriculumSubjectOut(name=subject.name, topics=topics_out))
            grades_out.append(CurriculumGradeOut(grade=grade.name, subjects=subjects_out))
        result.append(CurriculumBoardOut(board=board.name, grades=grades_out))
    return result


def _find_or_create_topic(
    db: Session, institution_id: str, board: str, grade: str, subject: str, topic_name: str
) -> Topic:
    board_row = (
        db.query(Board)
        .filter(Board.institution_id == institution_id, Board.name == board)
        .first()
    )
    if not board_row:
        board_row = Board(id=f"board-{board.lower()}", institution_id=institution_id, name=board, code=board.upper())
        db.add(board_row)
        db.flush()

    grade_row = db.query(Grade).filter(Grade.board_id == board_row.id, Grade.name == grade).first()
    if not grade_row:
        grade_row = Grade(id=f"grade-{grade.lower().replace(' ', '-')}", board_id=board_row.id, name=grade, level=8)
        db.add(grade_row)
        db.flush()

    subject_row = (
        db.query(Subject).filter(Subject.grade_id == grade_row.id, Subject.name == subject).first()
    )
    if not subject_row:
        subject_row = Subject(
            id=_unique_subject_id(db, grade_row.id, subject),
            grade_id=grade_row.id,
            name=subject,
        )
        db.add(subject_row)
        db.flush()

    chapter_row = (
        db.query(Chapter).filter(Chapter.subject_id == subject_row.id).first()
    )
    if not chapter_row:
        chapter_row = Chapter(
            id=_unique_row_id(db, Chapter, f"ch-{_slug(subject_row.id)}"),
            subject_id=subject_row.id,
            name=subject,
            order=1,
        )
        db.add(chapter_row)
        db.flush()

    topic_row = (
        db.query(Topic).filter(Topic.chapter_id == chapter_row.id, Topic.name == topic_name).first()
    )
    if not topic_row:
        topic_row = Topic(
            id=_unique_row_id(db, Topic, f"top-{_slug(chapter_row.id)}-{_slug(topic_name)}"),
            chapter_id=chapter_row.id,
            name=topic_name,
        )
        db.add(topic_row)
        db.flush()
    return topic_row


def _get_board(db: Session, institution_id: str, board_name: str) -> Board:
    board = (
        db.query(Board)
        .filter(Board.institution_id == institution_id, Board.name == board_name)
        .first()
    )
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    return board


def _get_grade(db: Session, board: Board, grade_name: str) -> Grade:
    grade = db.query(Grade).filter(Grade.board_id == board.id, Grade.name == grade_name).first()
    if not grade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grade not found")
    return grade


def _get_subject(db: Session, grade: Grade, subject_name: str) -> Subject:
    subject = (
        db.query(Subject).filter(Subject.grade_id == grade.id, Subject.name == subject_name).first()
    )
    if not subject:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")
    return subject


def _get_topic(db: Session, board_name: str, grade_name: str, subject_name: str, topic_name: str, institution_id: str) -> Topic:
    board = _get_board(db, institution_id, board_name)
    grade = _get_grade(db, board, grade_name)
    subject = _get_subject(db, grade, subject_name)
    for chapter in subject.chapters:
        topic = db.query(Topic).filter(Topic.chapter_id == chapter.id, Topic.name == topic_name).first()
        if topic:
            return topic
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")


def _get_batch(db: Session, batch_id: str, institution_id: str) -> Batch:
    batch = db.get(Batch, batch_id)
    if not batch or batch.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return batch


def _student_batch_ids(db: Session, student_id: str) -> list[str]:
    return [
        row.batch_id
        for row in db.query(BatchStudent).filter(BatchStudent.student_id == student_id).all()
    ]


def _sync_student_batch_memberships(db: Session, student_id: str, batch_ids: list[str], institution_id: str) -> None:
    desired = set(batch_ids)
    current_rows = db.query(BatchStudent).filter(BatchStudent.student_id == student_id).all()
    current = {row.batch_id for row in current_rows}
    for batch_id in desired - current:
        batch = _get_batch(db, batch_id, institution_id)
        _assign_student_to_batch_row(db, batch, student_id)
    for batch_id in current - desired:
        db.query(BatchStudent).filter(
            BatchStudent.student_id == student_id,
            BatchStudent.batch_id == batch_id,
        ).delete()
    _sync_profile_batch(db, student_id)


def _student_master_out(db: Session, profile: StudentProfile) -> StudentMasterOut:
    return StudentMasterOut(
        id=profile.id,
        name=profile.user.name,
        board=profile.board,
        grade=profile.grade,
        batch=profile.batch,
        batch_ids=_student_batch_ids(db, profile.id),
        center_id=profile.center_id,
        academic_year=profile.academic_year,
        school_name=profile.school_name,
        email=profile.user.email,
        status=profile.status,  # type: ignore[arg-type]
    )


def _sync_profile_batch(db: Session, student_id: str) -> None:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return
    memberships = (
        db.query(BatchStudent)
        .filter(BatchStudent.student_id == student_id)
        .all()
    )
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


def _assign_student_to_batch_row(
    db: Session, batch: Batch, student_id: str, *, replace_existing: bool = False
) -> None:
    if replace_existing:
        db.query(BatchStudent).filter(BatchStudent.student_id == student_id).delete()
    existing = (
        db.query(BatchStudent)
        .filter(BatchStudent.batch_id == batch.id, BatchStudent.student_id == student_id)
        .first()
    )
    if not existing:
        db.add(BatchStudent(batch_id=batch.id, student_id=student_id))
    _sync_profile_batch(db, student_id)


def _unique_batch_id(db: Session, name: str) -> str:
    base_id = f"batch-{name.lower().replace(' ', '-')}"
    batch_id = base_id
    suffix = 1
    while db.get(Batch, batch_id):
        batch_id = f"{base_id}-{suffix}"
        suffix += 1
    return batch_id


@router.get("/curriculum", response_model=list[CurriculumBoardOut])
def get_curriculum(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CurriculumBoardOut]:
    return _build_curriculum_tree(db, user.institution_id)


@router.post("/curriculum/boards", response_model=CurriculumBoardOut, status_code=status.HTTP_201_CREATED)
def add_board(
    body: AddBoardRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> CurriculumBoardOut:
    name = body.name.strip()
    if db.query(Board).filter(Board.institution_id == user.institution_id, Board.name == name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Board already exists")
    board = Board(
        id=f"board-{name.lower().replace(' ', '-')}",
        institution_id=user.institution_id,
        name=name,
        code=name.upper(),
    )
    grade = Grade(id=f"grade-{name.lower()}-8", board_id=board.id, name="Grade 8", level=8)
    subject = Subject(id=f"subj-{name.lower()}-math", grade_id=grade.id, name="Mathematics")
    db.add_all([board, grade, subject])
    db.commit()
    return CurriculumBoardOut(board=name, grades=[CurriculumGradeOut(grade="Grade 8", subjects=[CurriculumSubjectOut(name="Mathematics", topics=[])])])


@router.post("/curriculum/grades", status_code=status.HTTP_201_CREATED)
def add_grade(
    body: AddGradeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    board = (
        db.query(Board)
        .filter(Board.institution_id == user.institution_id, Board.name == body.board)
        .first()
    )
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    if db.query(Grade).filter(Grade.board_id == board.id, Grade.name == body.grade).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Grade already exists")
    grade_id = _unique_row_id(db, Grade, f"grade-{_slug(board.id)}-{_slug(body.grade)}")
    grade = Grade(
        id=grade_id,
        board_id=board.id,
        name=body.grade,
        level=8,
    )
    subject = Subject(
        id=_unique_subject_id(db, grade_id, "Mathematics"),
        grade_id=grade.id,
        name="Mathematics",
    )
    db.add_all([grade, subject])
    db.commit()
    return {"status": "created"}


@router.post("/curriculum/subjects", status_code=status.HTTP_201_CREATED)
def add_subject(
    body: AddSubjectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    board = (
        db.query(Board).filter(Board.institution_id == user.institution_id, Board.name == body.board).first()
    )
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    grade = db.query(Grade).filter(Grade.board_id == board.id, Grade.name == body.grade).first()
    if not grade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grade not found")
    if db.query(Subject).filter(Subject.grade_id == grade.id, Subject.name == body.subject).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subject already exists")
    subject_name = body.subject.strip()
    subject = Subject(
        id=_unique_subject_id(db, grade.id, subject_name),
        grade_id=grade.id,
        name=subject_name,
    )
    db.add(subject)
    db.commit()
    return {"status": "created"}


@router.post("/curriculum/topics", status_code=status.HTTP_201_CREATED)
def add_topic(
    body: AddTopicRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    _find_or_create_topic(db, user.institution_id, body.board, body.grade, body.subject, body.topic)
    db.commit()
    return {"status": "created"}


@router.patch("/curriculum/boards", status_code=status.HTTP_200_OK)
def update_board(
    body: UpdateBoardRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    board = _get_board(db, user.institution_id, body.board)
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is required")
    if (
        new_name != board.name
        and db.query(Board)
        .filter(Board.institution_id == user.institution_id, Board.name == new_name)
        .first()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Board already exists")
    old_name = board.name
    board.name = new_name
    board.code = new_name.upper()
    db.query(Question).filter(
        Question.institution_id == user.institution_id,
        Question.board == old_name,
    ).update({Question.board: new_name})
    db.query(Batch).filter(
        Batch.institution_id == user.institution_id,
        Batch.board == old_name,
    ).update({Batch.board: new_name})
    db.commit()
    return {"status": "updated", "board": new_name}


@router.delete("/curriculum/boards", status_code=status.HTTP_204_NO_CONTENT)
def delete_board(
    board: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    board_row = _get_board(db, user.institution_id, board)
    db.delete(board_row)
    db.commit()


@router.patch("/curriculum/grades", status_code=status.HTTP_200_OK)
def update_grade(
    body: UpdateGradeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    board = _get_board(db, user.institution_id, body.board)
    grade = _get_grade(db, board, body.grade)
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is required")
    if (
        new_name != grade.name
        and db.query(Grade).filter(Grade.board_id == board.id, Grade.name == new_name).first()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Grade already exists")
    old_name = grade.name
    grade.name = new_name
    db.query(Question).filter(
        Question.institution_id == user.institution_id,
        Question.board == body.board,
        Question.grade == old_name,
    ).update({Question.grade: new_name})
    db.query(Batch).filter(
        Batch.institution_id == user.institution_id,
        Batch.board == body.board,
        Batch.grade == old_name,
    ).update({Batch.grade: new_name})
    db.commit()
    return {"status": "updated", "grade": new_name}


@router.delete("/curriculum/grades", status_code=status.HTTP_204_NO_CONTENT)
def delete_grade(
    board: str = Query(...),
    grade: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    board_row = _get_board(db, user.institution_id, board)
    grade_row = _get_grade(db, board_row, grade)
    db.delete(grade_row)
    db.commit()


@router.patch("/curriculum/subjects", status_code=status.HTTP_200_OK)
def update_subject(
    body: UpdateSubjectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    board = _get_board(db, user.institution_id, body.board)
    grade = _get_grade(db, board, body.grade)
    subject = _get_subject(db, grade, body.subject)
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is required")
    if (
        new_name != subject.name
        and db.query(Subject).filter(Subject.grade_id == grade.id, Subject.name == new_name).first()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subject already exists")
    old_name = subject.name
    subject.name = new_name
    db.query(Question).filter(
        Question.institution_id == user.institution_id,
        Question.board == body.board,
        Question.grade == body.grade,
        Question.subject == old_name,
    ).update({Question.subject: new_name})
    db.query(Batch).filter(
        Batch.institution_id == user.institution_id,
        Batch.board == body.board,
        Batch.grade == body.grade,
        Batch.subject == old_name,
    ).update({Batch.subject: new_name})
    db.commit()
    return {"status": "updated", "subject": new_name}


@router.delete("/curriculum/subjects", status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(
    board: str = Query(...),
    grade: str = Query(...),
    subject: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    board_row = _get_board(db, user.institution_id, board)
    grade_row = _get_grade(db, board_row, grade)
    subject_row = _get_subject(db, grade_row, subject)
    db.delete(subject_row)
    db.commit()


@router.patch("/curriculum/topics", status_code=status.HTTP_200_OK)
def update_topic(
    body: UpdateTopicRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    topic = _get_topic(db, body.board, body.grade, body.subject, body.topic, user.institution_id)
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is required")
    old_name = topic.name
    topic.name = new_name
    db.query(Question).filter(Question.topic_id == topic.id).update({Question.topic_name: new_name})
    db.query(Question).filter(
        Question.institution_id == user.institution_id,
        Question.board == body.board,
        Question.grade == body.grade,
        Question.subject == body.subject,
        Question.topic_name == old_name,
    ).update({Question.topic_name: new_name})
    db.commit()
    return {"status": "updated", "topic": new_name}


@router.delete("/curriculum/topics", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(
    board: str = Query(...),
    grade: str = Query(...),
    subject: str = Query(...),
    topic: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    topic_row = _get_topic(db, board, grade, subject, topic, user.institution_id)
    db.delete(topic_row)
    db.commit()


def _student_summary(profile: StudentProfile) -> StudentSummaryOut:
    return StudentSummaryOut(
        id=profile.id,
        name=profile.user.name,
        grade=profile.grade,
        health=profile.health,
        status=profile.health_status,  # type: ignore[arg-type]
        readiness=profile.readiness,
        last_assessment=profile.last_assessment,
        critical_gaps=profile.critical_gaps,
        improving=profile.improving,
        board=profile.board,
        batch=profile.batch,
        center_id=profile.center_id,
        academic_year=profile.academic_year,
    )


@router.get("/students", response_model=PaginatedOut[StudentSummaryOut] | list[StudentSummaryOut])
def list_students(
    board: str | None = Query(None),
    grade: str | None = Query(None),
    batch: str | None = Query(None),
    center: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedOut[StudentSummaryOut] | list[StudentSummaryOut]:
    q = (
        db.query(StudentProfile)
        .join(User)
        .filter(User.institution_id == user.institution_id)
    )
    if board:
        q = q.filter(StudentProfile.board.ilike(board))
    if grade:
        q = q.filter(StudentProfile.grade == grade)
    if batch:
        batch_row = (
            db.query(Batch)
            .filter(Batch.institution_id == user.institution_id, Batch.name == batch)
            .first()
        )
        if batch_row:
            q = q.join(BatchStudent, BatchStudent.student_id == StudentProfile.id).filter(
                BatchStudent.batch_id == batch_row.id
            )
        else:
            q = q.filter(StudentProfile.batch == batch)
    if center:
        q = q.filter(StudentProfile.center_id == center)
    if page is None and limit is None:
        return [_student_summary(p) for p in q.all()]
    items, total, page_n, limit_n, pages = paginate_query(q, page or 1, limit)
    return PaginatedOut(
        items=[_student_summary(p) for p in items],
        total=total,
        page=page_n,
        limit=limit_n,
        pages=pages,
    )


@router.get("/students/{student_id}", response_model=StudentMasterOut)
def get_student(
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StudentMasterOut:
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return _student_master_out(db, profile)


@router.post("/students", response_model=StudentMasterOut, status_code=status.HTTP_201_CREATED)
def create_student(
    body: StudentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> StudentMasterOut:
    sid = f"stu-{len(db.query(StudentProfile).all()) + 10}"
    email = body.email or f"{sid}@brightpath.edu"
    center_id = body.center_id
    if not center_id:
        default_center = (
            db.query(Center)
            .filter(Center.institution_id == user.institution_id)
            .order_by(Center.name)
            .first()
        )
        if default_center:
            center_id = default_center.id
    new_user = User(
        id=sid,
        institution_id=user.institution_id,
        name=body.name,
        email=email,
        password_hash=hash_password(settings.demo_password),
        role="student",
    )
    profile = StudentProfile(
        id=sid,
        user_id=sid,
        board=body.board,
        grade=body.grade,
        batch=body.batch,
        center_id=center_id,
        academic_year=body.academic_year,
        school_name=body.school_name,
    )
    db.add_all([new_user, profile])
    db.flush()
    if body.batch_id:
        batch_row = _get_batch(db, body.batch_id, user.institution_id)
        _assign_student_to_batch_row(db, batch_row, sid)
    elif body.batch and body.batch.strip():
        batch_row = (
            db.query(Batch)
            .filter(
                Batch.institution_id == user.institution_id,
                Batch.name == body.batch.strip(),
            )
            .first()
        )
        if batch_row:
            _assign_student_to_batch_row(db, batch_row, sid)
    db.commit()
    from app.services.centers import sync_center_counts

    sync_center_counts(db, user.institution_id, commit=True)
    return get_student(sid, db, user)


@router.patch("/students/{student_id}", response_model=StudentMasterOut)
def update_student(
    student_id: str,
    body: StudentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> StudentMasterOut:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    if body.name:
        profile.user.name = body.name
    if body.board:
        profile.board = body.board
    if body.grade:
        profile.grade = body.grade
    if body.batch_ids is not None:
        _sync_student_batch_memberships(db, student_id, body.batch_ids, user.institution_id)
    elif body.batch is not None:
        batch_row = (
            db.query(Batch)
            .filter(
                Batch.institution_id == user.institution_id,
                Batch.name == body.batch.strip(),
            )
            .first()
        )
        if batch_row:
            _sync_student_batch_memberships(db, student_id, [batch_row.id], user.institution_id)
        else:
            profile.batch = body.batch
    if body.center_id is not None:
        profile.center_id = body.center_id
    if body.status:
        profile.status = body.status
    db.commit()
    from app.services.centers import sync_center_counts

    sync_center_counts(db, user.institution_id, commit=True)
    return get_student(student_id, db, user)


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    db.query(BatchStudent).filter(BatchStudent.student_id == student_id).delete()
    db.query(AssessmentSubmission).filter(AssessmentSubmission.student_id == student_id).delete()
    student_user = profile.user
    db.delete(profile)
    db.delete(student_user)
    db.commit()
    from app.services.centers import sync_center_counts

    sync_center_counts(db, user.institution_id, commit=True)


def _batch_out(db: Session, batch: Batch) -> TutorBatchOut:
    student_ids = [row.student_id for row in db.query(BatchStudent).filter(BatchStudent.batch_id == batch.id).all()]
    return TutorBatchOut(
        id=batch.id,
        name=batch.name,
        board=batch.board,
        grade=batch.grade,
        subject=batch.subject,
        schedule_timing=batch.schedule_timing,
        student_ids=student_ids,
        avg_score=batch.avg_score,
    )


@router.get("/batches", response_model=list[TutorBatchOut])
def list_batches(
    board: str | None = Query(None),
    grade: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TutorBatchOut]:
    q = db.query(Batch).filter(Batch.institution_id == user.institution_id)
    if board:
        q = q.filter(Batch.board == board)
    if grade:
        q = q.filter(Batch.grade == grade)
    return [_batch_out(db, b) for b in q.all()]


@router.get("/batches/{batch_id}", response_model=TutorBatchOut)
def get_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TutorBatchOut:
    batch = _get_batch(db, batch_id, user.institution_id)
    return _batch_out(db, batch)


@router.get("/batches/{batch_id}/students", response_model=list[StudentSummaryOut])
def list_batch_students(
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[StudentSummaryOut]:
    batch = _get_batch(db, batch_id, user.institution_id)
    rows = db.query(BatchStudent).filter(BatchStudent.batch_id == batch.id).all()
    if not rows:
        return []
    student_ids = [row.student_id for row in rows]
    profiles = (
        db.query(StudentProfile)
        .join(User)
        .filter(StudentProfile.id.in_(student_ids), User.institution_id == user.institution_id)
        .all()
    )
    by_id = {p.id: p for p in profiles}
    return [_student_summary(by_id[sid]) for sid in student_ids if sid in by_id]


@router.post("/batches", response_model=TutorBatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(
    body: TutorBatchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> TutorBatchOut:
    name = body.name.strip()
    if (
        db.query(Batch)
        .filter(
            Batch.institution_id == user.institution_id,
            Batch.board == body.board,
            Batch.grade == body.grade,
            Batch.name == name,
        )
        .first()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Batch already exists for this board and grade")
    batch_id = _unique_batch_id(db, name)
    schedule_timing = body.schedule_timing.strip() if body.schedule_timing else None
    batch = Batch(
        id=batch_id,
        institution_id=user.institution_id,
        name=name,
        board=body.board,
        grade=body.grade,
        subject=body.subject,
        schedule_timing=schedule_timing or None,
    )
    db.add(batch)
    db.flush()
    for sid in body.student_ids:
        _assign_student_to_batch_row(db, batch, sid)
    db.commit()
    return _batch_out(db, batch)


@router.patch("/batches/{batch_id}", response_model=TutorBatchOut)
def update_batch(
    batch_id: str,
    body: TutorBatchUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> TutorBatchOut:
    batch = _get_batch(db, batch_id, user.institution_id)
    if body.name:
        batch.name = body.name
    if body.subject is not None:
        batch.subject = body.subject
    if body.schedule_timing is not None:
        batch.schedule_timing = body.schedule_timing.strip() or None
    if body.avg_score is not None:
        batch.avg_score = body.avg_score
    db.commit()
    return _batch_out(db, batch)


@router.delete("/batches/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    batch = _get_batch(db, batch_id, user.institution_id)
    member_rows = (
        db.query(BatchStudent)
        .filter(BatchStudent.batch_id == batch_id)
        .all()
    )
    student_ids = [row.student_id for row in member_rows]
    db.query(BatchStudent).filter(BatchStudent.batch_id == batch_id).delete()
    for student_id in student_ids:
        _sync_profile_batch(db, student_id)
    db.delete(batch)
    db.commit()


@router.post("/batches/{batch_id}/students/{student_id}", status_code=status.HTTP_201_CREATED)
def add_student_to_batch(
    batch_id: str,
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict[str, str]:
    batch = _get_batch(db, batch_id, user.institution_id)
    if not db.get(StudentProfile, student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    _assign_student_to_batch_row(db, batch, student_id)
    db.commit()
    return {"status": "assigned"}


@router.delete("/batches/{batch_id}/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_student_from_batch(
    batch_id: str,
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    row = (
        db.query(BatchStudent)
        .filter(BatchStudent.batch_id == batch_id, BatchStudent.student_id == student_id)
        .first()
    )
    if row:
        db.delete(row)
        db.flush()
        _sync_profile_batch(db, student_id)
        db.commit()
