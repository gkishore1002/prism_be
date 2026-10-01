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


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _month_label(dt: datetime) -> str:
    return dt.strftime("%b %Y")


def monthly_trend_from_events(events: list[dict]) -> list[dict]:
    by_month: dict[str, list[int]] = defaultdict(list)
    labels: dict[str, str] = {}
    for event in events:
        dt = _parse_event_date(str(event["date"]))
        if not dt:
            continue
        key = _month_key(dt)
        by_month[key].append(event["pct"])
        labels[key] = _month_label(dt)
    if not by_month:
        return []
    ordered = sorted(by_month.items(), key=lambda item: item[0])
    return [
        {"month": labels.get(month, month), "score": round(mean(scores))}
        for month, scores in ordered
    ]


def student_score_events(db: Session, institution_id: str, student_id: str) -> list[dict]:
    """Marks + attended Prism assessments only — excludes absents and practice."""
    from app.services.subjects_list import canonicalize_subject_name, subjects_from_stored, subjects_label

    events: list[dict] = []
    for row in marks_for_students(db, institution_id, [student_id]):
        if row.max_marks is not None and int(row.max_marks) <= 0:
            continue
        events.append(
            {
                "pct": row.percentage,
                "subject": canonicalize_subject_name(row.subject) or row.subject,
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
            AssessmentSubmission.status == "attended",
            AssessmentSubmission.max_score > 0,
            Assessment.mode != "practice",
        )
        .all()
    )
    for sub in subs:
        assessment = db.get(Assessment, sub.assessment_id)
        if not assessment:
            continue
        pct = round((sub.score / sub.max_score) * 100) if sub.max_score else 0
        subjects = subjects_from_stored(assessment.subject, getattr(assessment, "subjects", None))
        subject_label = subjects_label(subjects) or canonicalize_subject_name(assessment.subject) or assessment.subject
        # Multi-subject papers emit one event per known subject only when we can
        # attribute scores; otherwise one labelled event with the paper subjects.
        per_subject = submission_subject_scores(
            db,
            sub,
            fallback_subjects=subjects,
            total_score=sub.score,
            total_max=sub.max_score,
        )
        if len(per_subject) > 1:
            for row in per_subject:
                events.append(
                    {
                        "pct": int(row["accuracy"]),
                        "subject": row["subject"],
                        "date": sub.submitted_at,
                        "source": "assessment",
                        "title": assessment.title,
                        "scored": float(row["score"]),
                        "maxMarks": int(row["maxScore"]),
                        "assessmentId": assessment.id,
                        "sessionId": assessment.id,
                    }
                )
        else:
            events.append(
                {
                    "pct": pct,
                    "subject": (per_subject[0]["subject"] if per_subject else subject_label),
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
        .filter(
            Assessment.institution_id == institution_id,
            AssessmentSubmission.status == "attended",
            Assessment.mode != "practice",
        )
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
                        answer_count = len(attempts) if student_id else sum(len(v) for v in per_student.values())
                        rows.append(
                            {
                                "board": board.name,
                                "grade": grade.name,
                                "subject": subject.name,
                                "chapter": chapter.name,
                                "topic": topic.name,
                                "topic_id": topic.id,
                                "questions": q_count,
                                "answers": answer_count,
                                "mastery": min(100, max(0, mastery)),
                            }
                        )
    return rows


def subject_scores_for_student(db: Session, institution_id: str, student_id: str) -> list[dict]:
    """Subject health from marks + attended assessments only (not topic mastery)."""
    by_subject: dict[str, list[int]] = defaultdict(list)
    all_events = student_score_events(db, institution_id, student_id)
    for event in all_events:
        by_subject[event["subject"]].append(event["pct"])

    subjects_out = []
    for subject, scores in sorted(by_subject.items()):
        score = round(mean(scores))
        subjects_out.append(
            {
                "subjectId": subject.lower().replace(" ", "-"),
                "subjectName": subject,
                "health": score,
                "status": _health_status(score),
                "trend": subject_trend_from_events(all_events, subject),
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


def submission_topic_breakdown(db: Session, submission: AssessmentSubmission | None) -> list[dict]:
    """Per-topic accuracy for one exam attempt."""
    if submission is None:
        return []
    try:
        answers = json.loads(submission.answers or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(answers, list):
        return []

    buckets: dict[str, dict] = {}
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
        concept = (question.topic_name or "").strip()
        subject = (question.subject or "").strip()
        chapter = (question.chapter or "").strip()
        if question.topic_id:
            topic_row = db.get(Topic, question.topic_id)
            if topic_row:
                if not concept:
                    concept = (topic_row.name or "").strip()
                if topic_row.chapter:
                    if not chapter:
                        chapter = (topic_row.chapter.name or "").strip()
                    if not subject and topic_row.chapter.subject:
                        subject = (topic_row.chapter.subject.name or "").strip()
        if not concept:
            concept = "Untagged"
        key = f"{subject}|{chapter}|{concept}"
        rec = buckets.setdefault(
            key,
            {"concept": concept, "subject": subject, "chapter": chapter, "correct": 0, "total": 0},
        )
        rec["total"] += 1
        if correct:
            rec["correct"] += 1

    rows = []
    for rec in buckets.values():
        total = int(rec["total"]) or 1
        rows.append(
            {
                "concept": rec["concept"],
                "subject": rec["subject"],
                "chapter": rec["chapter"],
                "correct": rec["correct"],
                "total": rec["total"],
                "masteryPct": round((rec["correct"] / total) * 100),
            }
        )
    rows.sort(key=lambda item: (item["masteryPct"], item["concept"]))
    return rows


def submission_subject_scores(
    db: Session,
    submission: AssessmentSubmission | None,
    *,
    fallback_subjects: list[str] | None = None,
    total_score: int | None = None,
    total_max: int | None = None,
) -> list[dict]:
    """Per-subject score rows for one exam attempt (from question subjects).

    Falls back to a single overall row using fallback_subjects / totals when
    answers are missing or questions are untagged.
    """
    from app.services.subjects_list import (
        canonicalize_subject_name,
        primary_subject,
        subjects_label,
    )

    fallback = [canonicalize_subject_name(s) for s in (fallback_subjects or []) if (s or "").strip()]
    fallback = [s for s in fallback if s]
    overall_score = int(total_score if total_score is not None else (submission.score if submission else 0))
    overall_max = int(total_max if total_max is not None else (submission.max_score if submission else 0))
    overall_acc = round((overall_score / overall_max) * 100) if overall_max else 0

    def _overall_row() -> list[dict]:
        label = subjects_label(fallback) or primary_subject(fallback, "Overall")
        return [
            {
                "subject": label,
                "score": overall_score,
                "maxScore": overall_max,
                "accuracy": overall_acc,
            }
        ]

    if submission is None:
        return _overall_row()

    try:
        answers = json.loads(submission.answers or "[]")
    except json.JSONDecodeError:
        return _overall_row()
    if not isinstance(answers, list) or not answers:
        return _overall_row()

    buckets: dict[str, dict[str, int]] = {}
    for ans in answers:
        if not isinstance(ans, dict):
            continue
        qid = dict_get(ans, "question_id", "questionId")
        if not qid:
            continue
        question = db.get(Question, qid)
        if not question:
            continue
        subject = canonicalize_subject_name(question.subject)
        if not subject and question.topic_id:
            topic_row = db.get(Topic, question.topic_id)
            if topic_row and topic_row.chapter and topic_row.chapter.subject:
                subject = canonicalize_subject_name(topic_row.chapter.subject.name)
        if not subject:
            subject = primary_subject(fallback, "Overall")
        selected = dict_get(ans, "selected_option", "selectedOption", default="")
        correct = bool(
            question.correct_answer
            and selected
            and str(selected).upper() == question.correct_answer.upper()
        )
        marks = max(1, int(getattr(question, "marks", None) or 1))
        rec = buckets.setdefault(subject, {"score": 0, "maxScore": 0})
        rec["maxScore"] += marks
        if correct:
            rec["score"] += marks

    if not buckets:
        return _overall_row()

    # If every question landed in one bucket that is "Overall" but we know the
    # paper subjects, prefer the paper label for display.
    if len(buckets) == 1 and fallback:
        only_key = next(iter(buckets))
        if only_key.casefold() in {"overall", ""}:
            return _overall_row()

    rows = []
    for subject, rec in sorted(buckets.items(), key=lambda item: item[0].casefold()):
        max_score = int(rec["maxScore"])
        score = int(rec["score"])
        rows.append(
            {
                "subject": subject,
                "score": score,
                "maxScore": max_score,
                "accuracy": round((score / max_score) * 100) if max_score else 0,
            }
        )
    return rows


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
        profile.health = 0
        profile.health_status = "weak"
        profile.readiness = 0
        profile.critical_gaps = 0
        profile.improving = False

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


def score_delta_from_events(events: list[dict]) -> int | None:
    """Change in average score (percentage points) between recent and prior assessments."""
    if len(events) < 2:
        return None
    scores = [int(e["pct"]) for e in events]
    recent = scores[-3:] if len(scores) >= 3 else scores[-1:]
    prior = scores[-6:-3] if len(scores) >= 6 else scores[: max(0, len(scores) - len(recent))]
    if not prior:
        return None
    return round(mean(recent) - mean(prior))


def cohort_score_improvement(
    db: Session, institution_id: str, student_ids: list[str]
) -> int:
    """Average score growth across students who have at least two score events."""
    deltas: list[int] = []
    for sid in student_ids:
        delta = score_delta_from_events(student_score_events(db, institution_id, sid))
        if delta is not None:
            deltas.append(delta)
    return round(mean(deltas)) if deltas else 0


def subject_trend_from_events(events: list[dict], subject: str) -> int:
    """Score delta for one subject from mark/assessment events."""
    subject_events = [e for e in events if e.get("subject") == subject]
    delta = score_delta_from_events(subject_events)
    return delta if delta is not None else 0


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
    return []


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
