"""Unit tests for rule-based topic predictive readiness (pure formula)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.topic_readiness import (
    TopicAttempt,
    difficulty_factor,
    recency_decay,
    rollup_subject_readiness,
    score_attempts,
)


def _at(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def test_empty_attempts_low_confidence():
    result = score_attempts([])
    assert result["currentMastery"] == 0
    assert result["predictedScore"] == 0
    assert result["confidence"] == "low"
    assert result["attemptCount"] == 0
    assert "no attempts yet" in result["drivers"]


def test_perfect_recent_high_confidence():
    attempts = [
        TopicAttempt(True, 1, "medium", _at(d))
        for d in (25, 20, 15, 10, 8, 6, 4, 2)
    ]
    result = score_attempts(attempts)
    assert result["currentMastery"] == 100
    assert result["predictedScore"] >= 90
    assert result["confidence"] == "high"
    assert result["attemptCount"] == 8


def test_improving_velocity_raises_prediction_above_mastery():
    # Early failures, recent successes
    attempts = [
        TopicAttempt(False, 1, "medium", _at(40)),
        TopicAttempt(False, 1, "medium", _at(35)),
        TopicAttempt(False, 1, "medium", _at(30)),
        TopicAttempt(True, 1, "medium", _at(10)),
        TopicAttempt(True, 1, "medium", _at(5)),
        TopicAttempt(True, 1, "medium", _at(1)),
    ]
    result = score_attempts(attempts)
    assert result["currentMastery"] == 50
    assert result["velocity"] > 0
    assert result["predictedScore"] > result["currentMastery"]
    assert "improving" in result["drivers"]
    assert result["confidence"] == "medium"


def test_stale_attempts_apply_decay_and_driver():
    attempts = [
        TopicAttempt(True, 1, "medium", _at(120)),
        TopicAttempt(True, 1, "medium", _at(110)),
        TopicAttempt(True, 1, "medium", _at(100)),
    ]
    result = score_attempts(attempts)
    assert result["currentMastery"] == 100
    assert result["predictedScore"] < 100
    assert "stale" in result["drivers"]
    assert result["confidence"] == "medium"


def test_hard_correct_weighs_more_than_easy():
    as_of = datetime.now(timezone.utc)
    hard_only = [
        TopicAttempt(True, 1, "hard", as_of - timedelta(days=1)),
        TopicAttempt(False, 1, "easy", as_of - timedelta(days=2)),
    ]
    easy_only = [
        TopicAttempt(False, 1, "hard", as_of - timedelta(days=1)),
        TopicAttempt(True, 1, "easy", as_of - timedelta(days=2)),
    ]
    hard_result = score_attempts(hard_only, as_of=as_of)
    easy_result = score_attempts(easy_only, as_of=as_of)
    # Same 50% raw mastery; hard-correct evidence should score higher
    assert hard_result["currentMastery"] == easy_result["currentMastery"] == 50
    assert hard_result["evidence"] > easy_result["evidence"]


def test_difficulty_factors():
    assert difficulty_factor("easy") == 0.8
    assert difficulty_factor("medium") == 1.0
    assert difficulty_factor("hard") == 1.25
    assert difficulty_factor("unknown") == 1.0


def test_recency_decay_half_life():
    assert recency_decay(0) == 1.0
    assert abs(recency_decay(45) - 0.5) < 1e-9


def test_limited_evidence_low_confidence():
    attempts = [TopicAttempt(True, 1, "medium", _at(2))]
    result = score_attempts(attempts)
    assert result["confidence"] == "low"
    assert "limited evidence" in result["drivers"]


def test_subject_rollup_weighted_by_attempts():
    topics = [
        {
            "subjectId": "math",
            "subjectName": "Mathematics",
            "currentMastery": 40,
            "predictedScore": 50,
            "weight": 0.5,
            "attemptCount": 10,
            "confidence": "high",
        },
        {
            "subjectId": "math",
            "subjectName": "Mathematics",
            "currentMastery": 80,
            "predictedScore": 90,
            "weight": 0.5,
            "attemptCount": 2,
            "confidence": "medium",
        },
        {
            "subjectId": "sci",
            "subjectName": "Science",
            "currentMastery": 70,
            "predictedScore": 72,
            "weight": 1.0,
            "attemptCount": 4,
            "confidence": "medium",
        },
    ]
    rolled = rollup_subject_readiness(topics, exam_date="2026-09-01")
    assert len(rolled) == 2
    math = next(r for r in rolled if r["subjectId"] == "math")
    # Heavier weight on topic with more attempts → closer to 50 than 90
    assert math["projectedReadiness"] < 75
    assert math["examDate"] == "2026-09-01"
    assert math["confidenceLevel"] in ("high", "medium")
