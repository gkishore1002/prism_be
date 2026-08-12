"""Recompute student profiles and topic mastery from marks + assessment submissions."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from statistics import mean

from sqlalchemy.orm import Session

from app.models.academic import Board, Question, Topic
from app.models.assessment import Assessment, AssessmentSubmission
from app.models.user import StudentProfile, User
from app.services.marks import marks_for_students
from app.utils import dict_get


def _health_status(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 55:
        return "fair"
    if score >= 40:
        return "weak"
    return "critical"


def _parse_event_date(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.split("T", 1)[0] if "T" in value else value[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _month_label(dt: datetime) -> str:
    return dt.strftime("%b")


def student_score_events(db: Session, institution_id: str, student_id: str) -> list[dict]:
    events: list[dict] = []
    for row in marks_for_students(db, institution_id, [student_id]):
        events.append(
            {
                "pct": row.percentage,
                "subject": row.subject,
                "date": row.conducted_on,
                "source": "marks",
                "title": row.assessment_title,
                "scored": float(row.scored_marks),
                "maxMarks": int(row.max_marks),
                "assessmentId": None,
                "sessionId": row.session_id,
            }
        )

    subs = (
        db.query(AssessmentSubmission)
        .join(Assessment, Assessment.id == AssessmentSubmission.assessment_id)
        .filter(
            Assessment.institution_id == institution_id,
            AssessmentSubmission.student_id == student_id,
        )
        .all()
    )
    for sub in subs:
        assessment = db.get(Assessment, sub.assessment_id)
        if not assessment:
            continue
        pct = round((sub.score / sub.max_score) * 100) if sub.max_score else 0
        events.append(
            {
                "pct": pct,
                "subject": assessment.subject,
                "date": sub.submitted_at,
                "source": "assessment",
                "title": assessment.title,
                "scored": float(sub.score),
                "maxMarks": int(sub.max_score),
                "assessmentId": assessment.id,
                "sessionId": assessment.id,
            }
        )

    events.sort(key=lambda e: _parse_event_date(str(e["date"])) or datetime.min)
    return events


def _topic_answer_stats(
    db: Session, institution_id: str, student_ids: set[str] | None = None
) -> dict[str, dict[str, list[int]]]:
    """topic_id -> student_id -> list of 0/100 per question attempt."""
    stats: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    q = (
        db.query(AssessmentSubmission)
        .join(Assessment, Assessment.id == AssessmentSubmission.assessment_id)
        .filter(Assessment.institution_id == institution_id)
    )
    if student_ids is not None:
        q = q.filter(AssessmentSubmission.student_id.in_(student_ids))
    submissions = q.all()

    question_cache: dict[str, Question | None] = {}

    for sub in submissions:
        try:
            answers = json.loads(sub.answers or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(answers, list):
            continue
        for ans in answers:
            if not isinstance(ans, dict):
                continue
            qid = dict_get(ans, "question_id", "questionId")
            if not qid:
                continue
            if qid not in question_cache:
                question_cache[qid] = db.get(Question, qid)
            question = question_cache[qid]
            if not question or not question.topic_id:
                continue
            selected = dict_get(ans, "selected_option", "selectedOption", default="")
            correct = bool(
                question.correct_answer
                and selected
                and str(selected).upper() == question.correct_answer.upper()
            )
            stats[question.topic_id][sub.student_id].append(100 if correct else 0)

    return stats


def topic_mastery_rows(
    db: Session,
    institution_id: str,
    *,
    student_ids: set[str] | None = None,
    student_id: str | None = None,
) -> list[dict]:
    """Curriculum topics with mastery derived from assessment answers (+ marks for subjects only)."""
    answer_stats = _topic_answer_stats(db, institution_id, student_ids)
    subject_scores: dict[str, list[int]] = defaultdict(list)

    target_students = student_ids
    if student_id:
        target_students = {student_id}
    if target_students:
        for sid in target_students:
            for event in student_score_events(db, institution_id, sid):
                subject_scores[event["subject"]].append(event["pct"])

    boards = db.query(Board).filter(Board.institution_id == institution_id).all()
    rows: list[dict] = []
    for board in boards:
        for grade in board.grades:
            for subject in grade.subjects:
                for chapter in subject.chapters:
                    for topic in chapter.topics:
                        per_student = answer_stats.get(topic.id, {})
                        if student_id:
                            attempts = per_student.get(student_id, [])
                            mastery = round(mean(attempts)) if attempts else 0
                        elif per_student:
                            student_avgs = [round(mean(vals)) for vals in per_student.values() if vals]
                            mastery = round(mean(student_avgs)) if student_avgs else 0
                        else:
                            mastery = 0
                        q_count = db.query(Question).filter(Question.topic_id == topic.id).count()
                        rows.append(
                            {
                                "board": board.name,
                                "grade": grade.name,
                                "subject": subject.name,
                                "chapter": chapter.name,
                                "topic": topic.name,
                                "topic_id": topic.id,
                                "questions": q_count,
                                "mastery": min(100, max(0, mastery)),
                            }
                        )
    return rows


def subject_scores_for_student(db: Session, institution_id: str, student_id: str) -> list[dict]:
    by_subject: dict[str, list[int]] = defaultdict(list)
    for event in student_score_events(db, institution_id, student_id):
        by_subject[event["subject"]].append(event["pct"])

    topic_rows = topic_mastery_rows(db, institution_id, student_id=student_id)
    for row in topic_rows:
        if row["mastery"] > 0:
            by_subject[row["subject"]].append(row["mastery"])

    subjects_out = []
    for subject, scores in sorted(by_subject.items()):
        score = round(mean(scores))
        subjects_out.append(
            {
                "subjectId": subject.lower().replace(" ", "-"),
                "subjectName": subject,
                "health": score,
                "status": _health_status(score),
                "trend": 0,
            }
        )
    return subjects_out


def submission_topic_tags(db: Session, institution_id: str, student_id: str, submission: AssessmentSubmission) -> tuple[list[str], list[str]]:
    strong: list[str] = []
    weak: list[str] = []
    try:
        answers = json.loads(submission.answers or "[]")
    except json.JSONDecodeError:
        return strong, weak
    if not isinstance(answers, list):
        return strong, weak

    for ans in answers:
        if not isinstance(ans, dict):
            continue
        qid = dict_get(ans, "question_id", "questionId")
        if not qid:
            continue
        question = db.get(Question, qid)
        if not question:
            continue
        selected = dict_get(ans, "selected_option", "selectedOption", default="")
        correct = bool(
            question.correct_answer
            and selected
            and str(selected).upper() == question.correct_answer.upper()
        )
        label = question.topic_name or question.topic_id
        if correct:
            if label not in strong:
                strong.append(label)
        else:
            if label not in weak:
                weak.append(label)
    return strong[:3], weak[:3]


def recompute_student_profile(db: Session, student_id: str) -> StudentProfile | None:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return None
    institution_id = profile.user.institution_id
    events = student_score_events(db, institution_id, student_id)

    if events:
        scores = [e["pct"] for e in events]
        profile.health = round(mean(scores))
        profile.health_status = _health_status(profile.health)
        profile.readiness = min(100, max(35, profile.health + max(0, len(events) - 3)))
        profile.last_assessment = str(events[-1]["date"])
        recent = scores[-3:] if len(scores) >= 3 else scores
        prior = scores[-6:-3] if len(scores) >= 6 else scores[: max(0, len(scores) - len(recent))]
        profile.improving = round(mean(recent)) >= round(mean(prior)) if prior else True
        topic_rows = topic_mastery_rows(db, institution_id, student_id=student_id)
        profile.critical_gaps = sum(1 for t in topic_rows if 0 < t["mastery"] < 55)
        if profile.critical_gaps == 0 and profile.health < 55:
            profile.critical_gaps = 1
    else:
        profile.health = 50
        profile.health_status = "fair"
        profile.readiness = 50
        profile.critical_gaps = 0
        profile.improving = True

    db.add(profile)
    return profile


def recompute_students(
    db: Session, institution_id: str, student_ids: list[str] | None = None, *, commit: bool = True
) -> int:
    if student_ids is None:
        profiles = (
            db.query(StudentProfile)
            .join(User)
            .filter(User.institution_id == institution_id)
            .all()
        )
        student_ids = [p.id for p in profiles]
    count = 0
    for sid in student_ids:
        if recompute_student_profile(db, sid):
            count += 1
    if commit:
        db.commit()
    return count


def recompute_all_institutions(db: Session) -> int:
    institution_ids = [row[0] for row in db.query(User.institution_id).distinct().all()]
    total = 0
    for inst_id in institution_ids:
        if inst_id:
            total += recompute_students(db, inst_id, commit=False)
    db.commit()
    return total


def monthly_trend_from_events(events: list[dict]) -> list[dict]:
    by_month: dict[str, list[int]] = defaultdict(list)
    for event in events:
        dt = _parse_event_date(str(event["date"]))
        if not dt:
            continue
        by_month[_month_label(dt)].append(event["pct"])
    if not by_month:
        return []
    ordered = sorted(by_month.items(), key=lambda item: datetime.strptime(item[0], "%b"))
    return [{"month": month, "score": round(mean(scores))} for month, scores in ordered]


def institution_monthly_trend(db: Session, institution_id: str) -> list[dict]:
    all_events: list[dict] = []
    profiles = (
        db.query(StudentProfile)
        .join(User)
        .filter(User.institution_id == institution_id)
        .all()
    )
    for profile in profiles:
        all_events.extend(student_score_events(db, institution_id, profile.id))
    trend = monthly_trend_from_events(all_events)
    if trend:
        return trend[-6:]
    students = profiles
    base = round(mean([s.health for s in students])) if students else 50
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    return [{"month": m, "score": base} for m in months]


def subject_health_distribution(db: Session, institution_id: str) -> list[dict]:
    by_subject: dict[str, list[int]] = defaultdict(list)
    profiles = (
        db.query(StudentProfile)
        .join(User)
        .filter(User.institution_id == institution_id)
        .all()
    )
    for profile in profiles:
        for subject in subject_scores_for_student(db, institution_id, profile.id):
            by_subject[subject["subjectName"]].append(subject["health"])
    if not by_subject:
        return []
    return [
        {"subject": subject, "health": round(mean(scores))}
        for subject, scores in sorted(by_subject.items())
    ]
