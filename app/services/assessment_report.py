"""Build and persist per-assessment student reports.

AI summaries are generated only when an assessment result is finalized or the
assessment is marked completed. Report reads never call Vertex — they return
stored copy (or rule-based placeholders if nothing is stored yet).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from statistics import mean

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentStudentReport, AssessmentSubmission
from app.models.user import StudentProfile
from app.services import analytics_recompute as recompute_svc
from app.services import vertex_summary as vertex_svc

logger = logging.getLogger(__name__)


def _parse_json_list(raw: str) -> list:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _rank_in_class(db: Session, assessment_id: str, student_id: str, accuracy: int) -> tuple[int | None, int]:
    submissions = (
        db.query(AssessmentSubmission)
        .filter(
            AssessmentSubmission.assessment_id == assessment_id,
            AssessmentSubmission.status == "attended",
            AssessmentSubmission.max_score > 0,
        )
        .all()
    )
    if not submissions:
        return None, 0
    ranked = sorted(
        submissions,
        key=lambda sub: (sub.score / sub.max_score if sub.max_score else 0),
        reverse=True,
    )
    total = len(ranked)
    for idx, sub in enumerate(ranked, start=1):
        if sub.student_id == student_id:
            return idx, total
    return None, total


def _rule_summary(
    student_name: str,
    assessment_title: str,
    subject: str,
    accuracy: int,
    class_avg: int | None,
    strong_topics: list[str],
    weak_topics: list[str],
) -> str:
    vs_class = ""
    if class_avg is not None:
        delta = accuracy - class_avg
        if delta > 0:
            vs_class = f" scored {delta} points above the class average of {class_avg}%"
        elif delta < 0:
            vs_class = f" scored {abs(delta)} points below the class average of {class_avg}%"
        else:
            vs_class = f" matched the class average of {class_avg}%"
    strength = f" Strong areas included {', '.join(strong_topics[:2])}." if strong_topics else ""
    focus = f" Focus next on {', '.join(weak_topics[:2])}." if weak_topics else ""
    return (
        f"{student_name} scored {accuracy}% on {assessment_title} ({subject}){vs_class}."
        f"{strength}{focus}"
    ).strip()


def _rule_summary_ta(
    student_name: str,
    assessment_title: str,
    subject: str,
    accuracy: int,
) -> str:
    return (
        f"{student_name} {assessment_title} ({subject}) தேர்வில் {accuracy}% மதிப்பெண் பெற்றுள்ளார்."
    )


def _student_message_en(assessment_title: str, accuracy: int) -> str:
    return f"You scored {accuracy}% on {assessment_title}."


def _student_message_ta(assessment_title: str, accuracy: int) -> str:
    return f"{assessment_title} தேர்வில் நீங்கள் {accuracy}% மதிப்பெண் பெற்றுள்ளீர்கள்."


def _ensure_tamil_fields(db: Session, report: AssessmentStudentReport) -> None:
    """Backfill Tamil copy for reports created before bilingual summaries."""
    updated = False
    profile = db.get(StudentProfile, report.student_id)
    student_name = profile.user.name if profile else ""
    if not (report.summary_ta or "").strip():
        report.summary_ta = _rule_summary_ta(
            student_name, report.assessment_title, report.subject, report.accuracy_pct
        )
        updated = True
    if not (report.student_message_ta or "").strip():
        report.student_message_ta = _student_message_ta(report.assessment_title, report.accuracy_pct)
        updated = True
    if not (report.student_message_en or "").strip():
        report.student_message_en = _student_message_en(report.assessment_title, report.accuracy_pct)
        updated = True
    if updated:
        db.commit()
        db.refresh(report)


def _exam_knowledge_summary(topics: list[dict], student_name: str, exam_title: str) -> str:
    if not topics:
        return (
            f"Topic-level scores for {exam_title} will appear once questions on this paper are tagged."
        )
    avg = round(mean(int(t["masteryPct"]) for t in topics))
    weak = sorted(topics, key=lambda t: int(t["masteryPct"]))[:3]
    strong = sorted(topics, key=lambda t: -int(t["masteryPct"]))[:3]
    who = student_name or "This student"
    parts = [
        f"On {exam_title}, {who}'s topic mastery averages {avg}% across {len(topics)} tagged topic"
        f"{'' if len(topics) == 1 else 's'}."
    ]
    if strong:
        names = ", ".join(f"{t['concept']} ({t['masteryPct']}%)" for t in strong)
        parts.append(f"Strongest on this paper: {names}.")
    if weak and (len(topics) > 1 or int(weak[0]["masteryPct"]) < 75):
        names = ", ".join(f"{t['concept']} ({t['masteryPct']}%)" for t in weak)
        parts.append(f"Needs attention on this paper: {names}.")
    return " ".join(parts)


def _attach_exam_knowledge(db: Session, payload: dict) -> dict:
    profile = db.get(StudentProfile, payload.get("studentId", ""))
    student_name = profile.user.name if profile else ""
    payload["studentName"] = student_name
    submission = None
    if payload.get("submissionId"):
        submission = db.get(AssessmentSubmission, payload["submissionId"])
    if submission is None and payload.get("assessmentId") and payload.get("studentId"):
        submission = (
            db.query(AssessmentSubmission)
            .filter(
                AssessmentSubmission.assessment_id == payload["assessmentId"],
                AssessmentSubmission.student_id == payload["studentId"],
                AssessmentSubmission.status == "attended",
            )
            .first()
        )
    topics = recompute_svc.submission_topic_breakdown(db, submission)
    payload["topicScores"] = topics
    payload["knowledgeSummary"] = _exam_knowledge_summary(
        topics, student_name, payload.get("assessmentTitle") or "this exam"
    )
    return payload


def _live_class_stats(
    db: Session, assessment_id: str, student_id: str, accuracy: int
) -> tuple[int | None, int | None, int]:
    """Fresh class average + rank from attended submissions (not frozen stored values)."""
    assessment = db.get(Assessment, assessment_id)
    live_avg = assessment.class_avg if assessment else None
    rank, total = _rank_in_class(db, assessment_id, student_id, accuracy)
    if live_avg is None and total > 0:
        submissions = (
            db.query(AssessmentSubmission)
            .filter(
                AssessmentSubmission.assessment_id == assessment_id,
                AssessmentSubmission.status == "attended",
                AssessmentSubmission.max_score > 0,
            )
            .all()
        )
        if submissions:
            live_avg = round(
                mean((s.score / s.max_score) * 100 for s in submissions if s.max_score)
            )
    return live_avg, rank, total


def _report_to_dict(report: AssessmentStudentReport, *, batch_name: str | None = None) -> dict:
    from app.services.subjects_list import normalize_subjects

    subject_scores = _parse_json_list(report.subject_scores)
    subjects = normalize_subjects(
        [row.get("subject") for row in subject_scores if isinstance(row, dict)],
        report.subject,
    )
    return {
        "id": report.id,
        "assessmentId": report.assessment_id,
        "studentId": report.student_id,
        "submissionId": report.submission_id,
        "assessmentTitle": report.assessment_title,
        "subject": report.subject,
        "subjects": subjects,
        "batchName": batch_name,
        "score": report.score,
        "maxScore": report.max_score,
        "accuracy": report.accuracy_pct,
        "classAvg": report.class_avg_pct,
        "rankInClass": report.rank_in_class,
        "totalInClass": report.total_in_class,
        "timeSpentMin": report.time_spent_min,
        "submittedAt": report.submitted_at,
        "subjectScores": subject_scores,
        "strongTopics": _parse_json_list(report.strong_topics),
        "weakTopics": _parse_json_list(report.weak_topics),
        "summary": report.summary,
        "summaryTa": report.summary_ta,
        "studentMessageEn": report.student_message_en,
        "studentMessageTa": report.student_message_ta,
        "summarySource": report.summary_source,
        "computedAt": report.computed_at,
        "reportType": "assessment",
    }


def _report_dict(db: Session, report: AssessmentStudentReport) -> dict:
    assessment = db.get(Assessment, report.assessment_id)
    batch_name = assessment.batch_name if assessment else None
    payload = _attach_exam_knowledge(db, _report_to_dict(report, batch_name=batch_name))
    live_avg, rank, total = _live_class_stats(
        db, report.assessment_id, report.student_id, report.accuracy_pct
    )
    if live_avg is not None:
        payload["classAvg"] = live_avg
    if rank is not None:
        payload["rankInClass"] = rank
    if total:
        payload["totalInClass"] = total
    return payload


def _report_to_student_summary(report: AssessmentStudentReport) -> dict:
    return {
        "assessmentId": report.assessment_id,
        "assessmentTitle": report.assessment_title,
        "subject": report.subject,
        "submittedAt": report.submitted_at,
        "accuracy": report.accuracy_pct,
        "studentMessageEn": report.student_message_en or _student_message_en(
            report.assessment_title, report.accuracy_pct
        ),
        "studentMessageTa": report.student_message_ta or _student_message_ta(
            report.assessment_title, report.accuracy_pct
        ),
        "cscReferralEn": "For a detailed report, please visit your CSC center.",
        "cscReferralTa": "விரிவான அறிக்கைக்கு CSC மையத்தை அணுகவும்.",
    }


def build_and_store_assessment_report(
    db: Session,
    assessment_id: str,
    student_id: str,
    *,
    commit: bool = True,
    force: bool = False,
    use_ai: bool = True,
) -> dict | None:
    """Generate (optionally via Vertex) and persist an assessment report.

    Call with use_ai=True only from assessment submit / mark-completed flows.
    """
    profile = db.get(StudentProfile, student_id)
    assessment = db.get(Assessment, assessment_id)
    if not profile or not assessment:
        return None

    submission = (
        db.query(AssessmentSubmission)
        .filter(
            AssessmentSubmission.assessment_id == assessment_id,
            AssessmentSubmission.student_id == student_id,
        )
        .first()
    )
    if not submission or submission.status != "attended":
        return None

    existing = (
        db.query(AssessmentStudentReport)
        .filter(
            AssessmentStudentReport.assessment_id == assessment_id,
            AssessmentStudentReport.student_id == student_id,
        )
        .first()
    )
    if existing and not force:
        _ensure_tamil_fields(db, existing)
        return _report_dict(db, existing)

    accuracy = round((submission.score / submission.max_score) * 100) if submission.max_score else 0
    strong_topics, weak_topics = recompute_svc.submission_topic_tags(
        db, assessment.institution_id, student_id, submission
    )
    rank, total = _rank_in_class(db, assessment_id, student_id, accuracy)

    from app.services.subjects_list import subjects_from_stored, subjects_label

    paper_subjects = subjects_from_stored(
        assessment.subject, getattr(assessment, "subjects", None)
    )
    subject_label = subjects_label(paper_subjects) or assessment.subject or "Subject"
    subject_scores = recompute_svc.submission_subject_scores(
        db,
        submission,
        fallback_subjects=paper_subjects,
        total_score=submission.score,
        total_max=submission.max_score,
    )
    rule_summary = _rule_summary(
        profile.user.name,
        assessment.title,
        subject_label,
        accuracy,
        assessment.class_avg,
        strong_topics,
        weak_topics,
    )
    rule_summary_ta = _rule_summary_ta(
        profile.user.name, assessment.title, subject_label, accuracy
    )
    context = {
        "studentName": profile.user.name,
        "assessmentTitle": assessment.title,
        "subject": subject_label,
        "subjects": paper_subjects,
        "board": assessment.board,
        "grade": assessment.grade,
        "score": submission.score,
        "maxScore": submission.max_score,
        "accuracy": accuracy,
        "classAvg": assessment.class_avg,
        "rankInClass": rank,
        "totalInClass": total,
        "timeSpentMin": submission.time_spent_min,
        "strongTopics": strong_topics,
        "weakTopics": weak_topics,
        "subjectScores": subject_scores,
    }

    summary = rule_summary
    summary_ta = rule_summary_ta
    summary_source = "rule-based"
    if use_ai:
        ai_summary, ai_summary_ta = vertex_svc.generate_pair_parallel(
            vertex_svc.generate_assessment_report_summary,
            vertex_svc.generate_assessment_report_summary_ta,
            context,
        )
        if ai_summary:
            summary = ai_summary
            summary_source = "vertex"
        if ai_summary_ta:
            summary_ta = ai_summary_ta

    student_msg_en = _student_message_en(assessment.title, accuracy)
    student_msg_ta = _student_message_ta(assessment.title, accuracy)
    computed_at = datetime.now().isoformat(timespec="minutes")

    if existing:
        report = existing
        report.submission_id = submission.id
        report.assessment_title = assessment.title
        report.subject = subject_label
        report.score = submission.score
        report.max_score = submission.max_score
        report.accuracy_pct = accuracy
        report.class_avg_pct = assessment.class_avg
        report.rank_in_class = rank
        report.total_in_class = total or None
        report.time_spent_min = submission.time_spent_min
        report.submitted_at = submission.submitted_at
        report.subject_scores = json.dumps(subject_scores)
        report.strong_topics = json.dumps(strong_topics)
        report.weak_topics = json.dumps(weak_topics)
        report.summary = summary
        report.summary_ta = summary_ta
        report.student_message_en = student_msg_en
        report.student_message_ta = student_msg_ta
        report.summary_source = summary_source
        report.computed_at = computed_at
    else:
        report = AssessmentStudentReport(
            id=f"asr-{uuid.uuid4().hex[:8]}",
            assessment_id=assessment_id,
            student_id=student_id,
            submission_id=submission.id,
            assessment_title=assessment.title,
            subject=subject_label,
            score=submission.score,
            max_score=submission.max_score,
            accuracy_pct=accuracy,
            class_avg_pct=assessment.class_avg,
            rank_in_class=rank,
            total_in_class=total or None,
            time_spent_min=submission.time_spent_min,
            submitted_at=submission.submitted_at,
            subject_scores=json.dumps(subject_scores),
            strong_topics=json.dumps(strong_topics),
            weak_topics=json.dumps(weak_topics),
            summary=summary,
            summary_ta=summary_ta,
            student_message_en=student_msg_en,
            student_message_ta=student_msg_ta,
            summary_source=summary_source,
            computed_at=computed_at,
        )
        db.add(report)

    if commit:
        db.commit()
        db.refresh(report)
    else:
        db.flush()
    return _report_dict(db, report)


def get_assessment_report(db: Session, assessment_id: str, student_id: str) -> dict | None:
    """Return the stored assessment report only — never calls Vertex."""
    report = (
        db.query(AssessmentStudentReport)
        .filter(
            AssessmentStudentReport.assessment_id == assessment_id,
            AssessmentStudentReport.student_id == student_id,
        )
        .first()
    )
    if report:
        _ensure_tamil_fields(db, report)
        return _report_dict(db, report)
    # No stored AI report yet — return rule-based snapshot without Vertex.
    return build_and_store_assessment_report(
        db, assessment_id, student_id, force=False, use_ai=False
    )


def get_assessment_report_summary(db: Session, assessment_id: str, student_id: str) -> dict | None:
    full = get_assessment_report(db, assessment_id, student_id)
    if not full:
        return None
    report = (
        db.query(AssessmentStudentReport)
        .filter(
            AssessmentStudentReport.assessment_id == assessment_id,
            AssessmentStudentReport.student_id == student_id,
        )
        .first()
    )
    if report:
        return _report_to_student_summary(report)
    return {
        "assessmentId": full["assessmentId"],
        "assessmentTitle": full["assessmentTitle"],
        "subject": full["subject"],
        "submittedAt": full["submittedAt"],
        "accuracy": full["accuracy"],
        "studentMessageEn": full.get("studentMessageEn")
        or _student_message_en(full["assessmentTitle"], full["accuracy"]),
        "studentMessageTa": full.get("studentMessageTa")
        or _student_message_ta(full["assessmentTitle"], full["accuracy"]),
        "cscReferralEn": "For a detailed report, please visit your CSC center.",
        "cscReferralTa": "விரிவான அறிக்கைக்கு CSC மையத்தை அணுகவும்.",
    }


def list_assessment_reports(
    db: Session,
    student_id: str,
    *,
    academic_year_id: str | None = None,
    enrollment_id: str | None = None,
) -> list[dict]:
    """List stored assessment reports. Never triggers Vertex on read."""
    stored = (
        db.query(AssessmentStudentReport)
        .filter(AssessmentStudentReport.student_id == student_id)
        .order_by(AssessmentStudentReport.submitted_at.desc())
        .all()
    )
    stored_by_assessment = {row.assessment_id: row for row in stored}

    submissions_q = (
        db.query(AssessmentSubmission)
        .join(Assessment, Assessment.id == AssessmentSubmission.assessment_id)
        .filter(
            AssessmentSubmission.student_id == student_id,
            AssessmentSubmission.status == "attended",
            Assessment.mode != "practice",
        )
    )
    if enrollment_id:
        submissions_q = submissions_q.filter(AssessmentSubmission.enrollment_id == enrollment_id)
    submissions = submissions_q.order_by(AssessmentSubmission.submitted_at.desc()).all()

    results: list[dict] = []
    seen: set[str] = set()
    for sub in submissions:
        if sub.assessment_id in seen:
            continue
        seen.add(sub.assessment_id)
        if academic_year_id:
            assessment = db.get(Assessment, sub.assessment_id)
            if not assessment or assessment.academic_year_id != academic_year_id:
                continue
        report = stored_by_assessment.get(sub.assessment_id)
        if report:
            _ensure_tamil_fields(db, report)
            results.append(_report_dict(db, report))
            continue
        # Persist rule-based copy so the list stays fast; AI fills in on next assessment update.
        built = build_and_store_assessment_report(
            db, sub.assessment_id, student_id, use_ai=False
        )
        if built:
            results.append(built)
    return results


def refresh_reports_for_assessment(db: Session, assessment_id: str) -> list[str]:
    """Force regenerate stored reports for every attended student on this assessment.

    Prefer report_jobs.run_after_assessment_completed / enqueue_report_job from API
    write paths; this remains for direct service callers and tests.
    """
    from app.services.report_jobs import run_after_assessment_completed

    result = run_after_assessment_completed(db, assessment_id)
    return list(result.get("refreshedStudents") or [])
