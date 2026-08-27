"""Rule-based per-topic predictive readiness (v1).

Forecasts likely exam % on a topic from attempt history, difficulty, marks,
recency, and velocity. No ML — transparent formulas that can be swapped later.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Literal

from sqlalchemy.orm import Session

from app.models.academic import Board, Question
from app.models.assessment import Assessment, AssessmentSubmission
from app.models.user import StudentProfile
from app.utils import dict_get

Confidence = Literal["high", "medium", "low"]

HALF_LIFE_DAYS = 45.0
STALE_DAYS = 45
HIGH_CONFIDENCE_MIN_ATTEMPTS = 8
HIGH_CONFIDENCE_MAX_AGE_DAYS = 30
MEDIUM_CONFIDENCE_MIN_ATTEMPTS = 3
VELOCITY_CLAMP = 20.0
MAX_DECAY_PENALTY = 8.0

DIFFICULTY_FACTOR = {
    "easy": 0.8,
    "medium": 1.0,
    "hard": 1.25,
}


@dataclass(frozen=True)
class TopicAttempt:
    """Single question attempt attributed to a topic."""

    correct: bool
    marks: float
    difficulty: str
    submitted_at: datetime


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.split("T", 1)[0] if "T" in value else value[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def difficulty_factor(difficulty: str | None) -> float:
    key = (difficulty or "medium").strip().lower()
    return DIFFICULTY_FACTOR.get(key, 1.0)


def recency_decay(days_ago: float, half_life: float = HALF_LIFE_DAYS) -> float:
    if days_ago <= 0:
        return 1.0
    return math.pow(0.5, days_ago / half_life)


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_attempts(
    attempts: list[TopicAttempt],
    *,
    as_of: datetime | None = None,
) -> dict:
    """Pure forecast from ordered attempts. Empty → zeros / low confidence."""
    as_of = as_of or _now()
    if not attempts:
        return {
            "currentMastery": 0,
            "predictedScore": 0,
            "delta": 0,
            "confidence": "low",
            "attemptCount": 0,
            "drivers": ["no attempts yet"],
            "evidence": 0.0,
            "velocity": 0.0,
        }

    ordered = sorted(attempts, key=lambda a: a.submitted_at)
    raw_scores = [100.0 if a.correct else 0.0 for a in ordered]
    current_mastery = round(mean(raw_scores))

    weighted_sum = 0.0
    weight_total = 0.0
    hard_count = 0
    for a in ordered:
        days_ago = max(0.0, (as_of - a.submitted_at).total_seconds() / 86400.0)
        w = max(0.01, float(a.marks or 1)) * difficulty_factor(a.difficulty) * recency_decay(days_ago)
        score = 100.0 if a.correct else 0.0
        weighted_sum += score * w
        weight_total += w
        if (a.difficulty or "").lower() == "hard":
            hard_count += 1

    evidence = weighted_sum / weight_total if weight_total else float(current_mastery)

    # Velocity: recent half vs prior half (prefer last 5 vs earlier if enough)
    n = len(ordered)
    if n >= 4:
        split = max(1, n // 2)
        if n >= 6:
            recent = raw_scores[-5:]
            prior = raw_scores[:-5] or raw_scores[:split]
        else:
            prior = raw_scores[:split]
            recent = raw_scores[split:]
        velocity = mean(recent) - mean(prior)
    elif n >= 2:
        velocity = raw_scores[-1] - mean(raw_scores[:-1])
    else:
        velocity = 0.0
    velocity = clamp(velocity, -VELOCITY_CLAMP, VELOCITY_CLAMP)

    last_at = ordered[-1].submitted_at
    days_since_last = max(0.0, (as_of - last_at).total_seconds() / 86400.0)
    decay_penalty = 0.0
    if days_since_last > STALE_DAYS:
        # Linear ramp up to MAX_DECAY_PENALTY over another half-life window
        excess = days_since_last - STALE_DAYS
        decay_penalty = min(MAX_DECAY_PENALTY, (excess / HALF_LIFE_DAYS) * MAX_DECAY_PENALTY)

    predicted = clamp(evidence + 0.5 * velocity - decay_penalty)
    predicted_i = round(predicted)
    delta = predicted_i - current_mastery

    attempt_count = n
    if attempt_count >= HIGH_CONFIDENCE_MIN_ATTEMPTS and days_since_last <= HIGH_CONFIDENCE_MAX_AGE_DAYS:
        confidence: Confidence = "high"
    elif attempt_count >= MEDIUM_CONFIDENCE_MIN_ATTEMPTS:
        confidence = "medium"
    else:
        confidence = "low"

    drivers: list[str] = []
    if velocity >= 5:
        drivers.append("improving")
    elif velocity <= -5:
        drivers.append("declining")
    if hard_count == 0 and attempt_count >= 2:
        drivers.append("few hard items")
    if days_since_last > STALE_DAYS:
        drivers.append("stale")
    if attempt_count < MEDIUM_CONFIDENCE_MIN_ATTEMPTS:
        drivers.append("limited evidence")
    if not drivers:
        drivers.append("stable")

    return {
        "currentMastery": current_mastery,
        "predictedScore": predicted_i,
        "delta": delta,
        "confidence": confidence,
        "attemptCount": attempt_count,
        "drivers": drivers,
        "evidence": round(evidence, 2),
        "velocity": round(velocity, 2),
    }


def _collect_student_attempts(
    db: Session,
    institution_id: str,
    student_id: str,
) -> dict[str, list[TopicAttempt]]:
    """topic_id -> attempts for one student."""
    by_topic: dict[str, list[TopicAttempt]] = defaultdict(list)
    submissions = (
        db.query(AssessmentSubmission)
        .join(Assessment, Assessment.id == AssessmentSubmission.assessment_id)
        .filter(
            Assessment.institution_id == institution_id,
            AssessmentSubmission.student_id == student_id,
            AssessmentSubmission.status == "attended",
        )
        .all()
    )
    question_cache: dict[str, Question | None] = {}

    for sub in submissions:
        submitted = _parse_dt(sub.submitted_at) or _now()
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
            by_topic[question.topic_id].append(
                TopicAttempt(
                    correct=correct,
                    marks=float(question.marks or 1),
                    difficulty=question.difficulty or "medium",
                    submitted_at=submitted,
                )
            )
    return by_topic


def compute_topic_readiness(
    db: Session,
    institution_id: str,
    student_id: str,
    *,
    as_of: datetime | None = None,
    only_with_attempts: bool = True,
) -> list[dict]:
    """Per-topic predictive readiness rows for a student."""
    as_of = as_of or _now()
    attempts_by_topic = _collect_student_attempts(db, institution_id, student_id)

    boards = db.query(Board).filter(Board.institution_id == institution_id).all()
    rows: list[dict] = []
    for board in boards:
        for grade in board.grades:
            for subject in grade.subjects:
                for chapter in subject.chapters:
                    for topic in chapter.topics:
                        attempts = attempts_by_topic.get(topic.id, [])
                        if only_with_attempts and not attempts:
                            continue
                        scored = score_attempts(attempts, as_of=as_of)
                        weight = float(topic.weight) if topic.weight is not None else 0.25
                        rows.append(
                            {
                                "topicId": topic.id,
                                "topic": topic.name,
                                "topicName": topic.name,
                                "chapter": chapter.name,
                                "chapterName": chapter.name,
                                "subject": subject.name,
                                "subjectName": subject.name,
                                "subjectId": subject.id,
                                "weight": weight,
                                "mastery": scored["currentMastery"],
                                "currentMastery": scored["currentMastery"],
                                "predictedScore": scored["predictedScore"],
                                "delta": scored["delta"],
                                "confidence": scored["confidence"],
                                "confidenceLevel": scored["confidence"],
                                "attemptCount": scored["attemptCount"],
                                "drivers": scored["drivers"],
                                "status": _status_from_score(scored["predictedScore"]),
                            }
                        )
    rows.sort(key=lambda r: (r["predictedScore"], r["attemptCount"]), reverse=True)
    return rows


def _status_from_score(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 55:
        return "fair"
    if score >= 40:
        return "weak"
    return "critical"


def rollup_subject_readiness(
    topic_rows: list[dict],
    *,
    profile_readiness: int | None = None,
    exam_date: str | None = None,
) -> list[dict]:
    """Subject-level current/projected readiness from topic forecasts."""
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for row in topic_rows:
        key = row.get("subjectId") or row.get("subject") or "unknown"
        by_subject[str(key)].append(row)

    out: list[dict] = []
    for subject_id, topics in by_subject.items():
        if not topics:
            continue
        name = topics[0].get("subjectName") or topics[0].get("subject") or subject_id

        def _weighted(field: str) -> float:
            total_w = 0.0
            acc = 0.0
            for t in topics:
                w = float(t.get("weight") or 0)
                if w <= 0:
                    w = max(1, int(t.get("attemptCount") or 1))
                else:
                    # Prefer weight * attempt evidence so empty weights still count
                    w = w * max(1, int(t.get("attemptCount") or 1))
                acc += float(t.get(field, 0)) * w
                total_w += w
            return acc / total_w if total_w else 0.0

        current = round(_weighted("currentMastery"))
        projected = round(_weighted("predictedScore"))
        # Confidence: best of topics with most attempts, downgrade if mixed low
        confidences = [t.get("confidence") or t.get("confidenceLevel") or "low" for t in topics]
        if all(c == "high" for c in confidences):
            conf: Confidence = "high"
        elif any(c in ("high", "medium") for c in confidences):
            conf = "medium"
        else:
            conf = "low"

        out.append(
            {
                "subjectId": subject_id,
                "subjectName": name,
                "currentReadiness": current if topics else (profile_readiness or 0),
                "projectedReadiness": projected if topics else min(100, (profile_readiness or 0) + 0),
                "examDate": exam_date or "",
                "confidenceLevel": conf,
            }
        )

    out.sort(key=lambda r: r["projectedReadiness"], reverse=True)
    return out


def nearest_upcoming_exam_date(db: Session, institution_id: str, student_id: str) -> str | None:
    """Best-effort exam date from scheduled assessments; None if unavailable."""
    today = _now().date()
    assessments = (
        db.query(Assessment)
        .filter(Assessment.institution_id == institution_id)
        .all()
    )
    candidates: list[datetime] = []
    for a in assessments:
        dt = _parse_dt(a.scheduled_at)
        if not dt:
            continue
        if dt.date() >= today and a.status in ("scheduled", "active", "published", "open"):
            # Prefer assessments that include this student when assigned list is set
            assigned = []
            try:
                assigned = json.loads(a.assigned_student_ids or "[]")
            except json.JSONDecodeError:
                assigned = []
            if assigned and student_id not in assigned:
                continue
            candidates.append(dt)
    if not candidates:
        # Fall back to any future scheduled_at regardless of status
        for a in assessments:
            dt = _parse_dt(a.scheduled_at)
            if dt and dt.date() >= today:
                candidates.append(dt)
    if not candidates:
        return None
    nearest = min(candidates)
    return nearest.date().isoformat()


def get_student_topic_readiness(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile or not profile.user:
        return []
    return compute_topic_readiness(db, profile.user.institution_id, student_id)


def get_student_subject_readiness_predictions(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile or not profile.user:
        return []
    institution_id = profile.user.institution_id
    topics = compute_topic_readiness(db, institution_id, student_id, only_with_attempts=True)
    exam_date = nearest_upcoming_exam_date(db, institution_id, student_id) or ""
    rolled = rollup_subject_readiness(
        topics,
        profile_readiness=profile.readiness,
        exam_date=exam_date,
    )
    if rolled:
        return rolled[:6]
    # No topic attempts — fall back to profile readiness without fake +8 lift
    return [
        {
            "subjectId": "overall",
            "subjectName": "Overall",
            "currentReadiness": profile.readiness,
            "projectedReadiness": profile.readiness,
            "examDate": exam_date,
            "confidenceLevel": "low",
        }
    ]
