"""Tests for report job extraction mapped to batch / assessments / marks."""

from __future__ import annotations

from app.services import report_jobs as jobs


def test_extract_eligible_filters_unscored(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "student_score_flags",
        lambda _db, _inst, sid: {
            "studentId": sid,
            "batchIds": ["b1"] if sid == "s1" else [],
            "hasAssessments": sid == "s1",
            "hasMarks": sid == "s2",
            "attendedAssessmentIds": ["a1"] if sid == "s1" else [],
            "marksCount": 1 if sid == "s2" else 0,
        },
    )
    eligible = jobs.extract_eligible_students(
        None,  # type: ignore[arg-type]
        "inst-1",
        student_ids=["s1", "s2", "s3"],
    )
    assert [e["studentId"] for e in eligible] == ["s1", "s2"]


def test_extract_eligible_respects_batch_membership(monkeypatch):
    class _Q:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [("s1",), ("s2",)]

    class _Db:
        def query(self, *a, **k):
            return _Q()

    monkeypatch.setattr(
        jobs,
        "student_score_flags",
        lambda _db, _inst, sid: {
            "studentId": sid,
            "batchIds": ["batch-a"],
            "hasAssessments": sid == "s1",
            "hasMarks": False,
            "attendedAssessmentIds": ["a1"] if sid == "s1" else [],
            "marksCount": 0,
        },
    )
    eligible = jobs.extract_eligible_students(
        _Db(),  # type: ignore[arg-type]
        "inst-1",
        student_ids=["s1", "s2", "s9"],
        batch_id="batch-a",
    )
    # s9 not in batch; s2 in batch but no scores → only s1
    assert [e["studentId"] for e in eligible] == ["s1"]


def test_collect_batch_ids_merges_membership_and_assessment_name(monkeypatch):
    monkeypatch.setattr(jobs, "_batch_ids_for_student", lambda _db, sid: [f"b-{sid}"])
    monkeypatch.setattr(
        jobs, "_resolve_batch_id_by_name", lambda _db, _inst, name: "b-named" if name else None
    )
    ids = jobs._collect_batch_ids(
        None,  # type: ignore[arg-type]
        "inst-1",
        ["s1", "s2"],
        preferred_batch_id="b-pref",
        assessment_batch_name="Alpha",
    )
    assert ids == ["b-named", "b-pref", "b-s1", "b-s2"]
