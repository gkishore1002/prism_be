"""Build and persist per-assessment student reports."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentStudentReport, AssessmentSubmission
from app.models.user import StudentProfile
from app.services import analytics_recompute as recompute_svc
from app.services import vertex_summary as vertex_svc


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


def _report_to_dict(report: AssessmentStudentReport) -> dict:
    return {
        "id": report.id,
        "assessmentId": report.assessment_id,
        "studentId": report.student_id,
        "submissionId": report.submission_id,
        "assessmentTitle": report.assessment_title,
        "subject": report.subject,
        "score": report.score,
        "maxScore": report.max_score,
        "accuracy": report.accuracy_pct,
        "classAvg": report.class_avg_pct,
        "rankInClass": report.rank_in_class,
        "totalInClass": report.total_in_class,
        "timeSpentMin": report.time_spent_min,
        "submittedAt": report.submitted_at,
        "subjectScores": _parse_json_list(report.subject_scores),
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
) -> dict | None:
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
    if existing:
        _ensure_tamil_fields(db, existing)
        return _report_to_dict(existing)

    accuracy = round((submission.score / submission.max_score) * 100) if submission.max_score else 0
    strong_topics, weak_topics = recompute_svc.submission_topic_tags(
        db, assessment.institution_id, student_id, submission
    )
    rank, total = _rank_in_class(db, assessment_id, student_id, accuracy)
    subject_scores = [
        {
            "subject": assessment.subject,
            "score": submission.score,
            "maxScore": submission.max_score,
            "accuracy": accuracy,
        }
    ]
    rule_summary = _rule_summary(
        profile.user.name,
        assessment.title,
        assessment.subject,
        accuracy,
        assessment.class_avg,
        strong_topics,
        weak_topics,
    )
    context = {
        "studentName": profile.user.name,
        "assessmentTitle": assessment.title,
        "subject": assessment.subject,
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
    ai_summary, ai_summary_ta = vertex_svc.generate_pair_parallel(
        vertex_svc.generate_assessment_report_summary,
        vertex_svc.generate_assessment_report_summary_ta,
        context,
    )
    summary = ai_summary or rule_summary
    summary_source = "vertex" if ai_summary else "rule-based"
    summary_ta = ai_summary_ta or _rule_summary_ta(
        profile.user.name, assessment.title, assessment.subject, accuracy
    )
    student_msg_en = _student_message_en(assessment.title, accuracy)
    student_msg_ta = _student_message_ta(assessment.title, accuracy)
    computed_at = datetime.now().isoformat(timespec="minutes")

    report = AssessmentStudentReport(
        id=f"asr-{uuid.uuid4().hex[:8]}",
        assessment_id=assessment_id,
        student_id=student_id,
        submission_id=submission.id,
        assessment_title=assessment.title,
        subject=assessment.subject,
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
    return _report_to_dict(report)


def get_assessment_report(db: Session, assessment_id: str, student_id: str) -> dict | None:
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
        return _report_to_dict(report)
    return build_and_store_assessment_report(db, assessment_id, student_id)


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


def list_assessment_reports(db: Session, student_id: str) -> list[dict]:
    stored = (
        db.query(AssessmentStudentReport)
        .filter(AssessmentStudentReport.student_id == student_id)
        .order_by(AssessmentStudentReport.submitted_at.desc())
        .all()
    )
    stored_by_assessment = {row.assessment_id: row for row in stored}

    submissions = (
        db.query(AssessmentSubmission)
        .filter(
            AssessmentSubmission.student_id == student_id,
            AssessmentSubmission.status == "attended",
        )
        .order_by(AssessmentSubmission.submitted_at.desc())
        .all()
    )

    results: list[dict] = []
    seen: set[str] = set()
    for sub in submissions:
        if sub.assessment_id in seen:
            continue
        seen.add(sub.assessment_id)
        report = stored_by_assessment.get(sub.assessment_id)
        if report:
            _ensure_tamil_fields(db, report)
            results.append(_report_to_dict(report))
            continue
        built = build_and_store_assessment_report(db, sub.assessment_id, student_id)
        if built:
            results.append(built)
    return results
