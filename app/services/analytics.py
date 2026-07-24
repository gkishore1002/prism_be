"""Analytics computations derived from database records (no AI)."""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean

from sqlalchemy.orm import Session

from app.models.academic import Board, Grade, Question, Subject, Topic
from app.models.assessment import Assessment, AssessmentSubmission
from app.models.content import Batch, BatchStudent
from app.models.institution import Center, Institution
from app.models.user import StudentProfile, User
from app.services import analytics_recompute as recompute_svc
from app.utils import from_json_list


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


def _students_for_institution(db: Session, institution_id: str) -> list[StudentProfile]:
    return (
        db.query(StudentProfile)
        .join(User)
        .filter(User.institution_id == institution_id)
        .all()
    )


def get_institution_overview(db: Session, institution_id: str) -> dict:
    students = _students_for_institution(db, institution_id)
    tutors = db.query(User).filter(User.institution_id == institution_id, User.role == "tutor").count()
    boards: dict[str, int] = defaultdict(int)
    for s in students:
        boards[s.board] += 1
    health_scores = [s.health for s in students] or [0]
    readiness_scores = [s.readiness for s in students] or [0]
    improving = sum(1 for s in students if s.improving)
    return {
        "institution": _institution_dict(db, institution_id),
        "totalStudents": len(students),
        "tutorCount": tutors,
        "byBoard": [{"board": b, "count": c} for b, c in boards.items()],
        "avgImprovement": round(mean([s.readiness - 60 for s in students]) if students else 0),
        "parentNps": min(100, round(mean(readiness_scores) * 0.85)) if readiness_scores else 0,
        "retention": round(88 + (improving / max(len(students), 1)) * 10),
        "avgHealth": round(mean(health_scores)),
        "avgReadiness": round(mean(readiness_scores)),
    }


def _institution_dict(db: Session, institution_id: str) -> dict:
    inst = db.get(Institution, institution_id)
    if not inst:
        return {"id": institution_id, "name": "Institution", "type": "coaching", "boardIds": [], "studentCount": 0, "tutorCount": 0}
    students = _students_for_institution(db, institution_id)
    tutors = db.query(User).filter(User.institution_id == institution_id, User.role == "tutor").count()
    return {
        "id": inst.id,
        "name": inst.name,
        "type": inst.type,
        "boardIds": from_json_list(inst.board_ids),
        "studentCount": len(students),
        "tutorCount": tutors,
    }


def get_centers_analytics(db: Session, institution_id: str) -> list[dict]:
    from app.services.centers import student_counts_by_center

    centers = db.query(Center).filter(Center.institution_id == institution_id).all()
    students = _students_for_institution(db, institution_id)
    by_center: dict[str, list[StudentProfile]] = defaultdict(list)
    for s in students:
        by_center[s.center_id or "unknown"].append(s)
    live_counts = student_counts_by_center(db, institution_id)

    result = []
    for c in centers:
        cohort = by_center.get(c.id, [])
        avg = round(mean([s.health for s in cohort])) if cohort else 70
        student_count = live_counts.get(c.id, len(cohort))
        result.append({
            "id": c.id,
            "name": c.name,
            "city": c.city or "",
            "students": student_count,
            "studentCount": student_count,
            "batchCount": c.batch_count or 0,
            "avg": avg,
            "retention": min(98, 80 + avg // 5),
            "nps": min(90, 40 + avg // 2),
            "growth": max(5, avg // 5),
        })
    return result


def get_board_report(db: Session, institution_id: str) -> list[dict]:
    students = _students_for_institution(db, institution_id)
    subject_health = get_subject_health_distribution(db, institution_id)
    ranked_subjects = sorted(subject_health, key=lambda r: r["health"], reverse=True) if subject_health else []
    boards: dict[str, list[StudentProfile]] = defaultdict(list)
    for s in students:
        boards[s.board].append(s)
    rows = []
    for board, cohort in boards.items():
        avg = round(mean([s.health for s in cohort]))
        at_risk = sum(1 for s in cohort if s.health < 55 or not s.improving)
        if ranked_subjects:
            top_subject = ranked_subjects[0]["subject"]
            weak_subject = ranked_subjects[-1]["subject"]
        else:
            top_subject = "—"
            weak_subject = "—"
        rows.append({
            "board": board,
            "students": len(cohort),
            "avg": avg,
            "improvement": round(mean([s.readiness - 55 for s in cohort])),
            "atRisk": at_risk,
            "syllabus": min(95, avg + 8),
            "topSubject": top_subject,
            "weakSubject": weak_subject,
        })
    return rows


def get_teachers(db: Session, institution_id: str) -> list[dict]:
    tutors = db.query(User).filter(User.institution_id == institution_id, User.role == "tutor").all()
    students = _students_for_institution(db, institution_id)
    batches = db.query(Batch).filter(Batch.institution_id == institution_id).all()
    assessments = (
        db.query(Assessment)
        .filter(Assessment.institution_id == institution_id)
        .all()
    )
    result = []
    for t in tutors:
        tutor_assessments = [a for a in assessments if a.created_by_tutor_id == t.id]
        cohort_ids: set[str] = set()
        for assessment in tutor_assessments:
            cohort_ids.update(from_json_list(assessment.assigned_student_ids))
        if not cohort_ids:
            batch_names = {a.batch_name for a in tutor_assessments if a.batch_name}
            for batch in batches:
                if batch.name in batch_names:
                    cohort_ids.update(_batch_student_ids(db, batch.id))
        cohort = [s for s in students if s.id in cohort_ids]
        if not cohort and len(tutors) == 1:
            cohort = students
        avg_health = round(mean([s.health for s in cohort])) if cohort else 75
        primary_subject = tutor_assessments[0].subject if tutor_assessments else "General"
        result.append({
            "id": t.id,
            "name": t.name,
            "email": t.email,
            "subject": f"{primary_subject} · {cohort[0].board if cohort else 'Institution'}",
            "students": len(cohort),
            "improved": round(100 * sum(1 for s in cohort if s.improving) / len(cohort)) if cohort else 0,
            "growth": max(5, avg_health // 5),
            "readiness": round(mean([s.readiness for s in cohort])) if cohort else 70,
        })
    return result


def _batch_student_ids(db: Session, batch_id: str) -> list[str]:
    from app.models.content import BatchStudent

    return [r.student_id for r in db.query(BatchStudent).filter(BatchStudent.batch_id == batch_id).all()]


def get_hardest_topics(db: Session, institution_id: str, limit: int = 5) -> list[dict]:
    topics = _topic_mastery_rows(db, institution_id)
    weak = sorted(topics, key=lambda t: t["mastery"])[:limit]
    return [{"topic": t["topic"], "correct": t["mastery"]} for t in weak]


def _topic_mastery_rows(
    db: Session,
    institution_id: str,
    *,
    student_ids: set[str] | None = None,
    student_id: str | None = None,
) -> list[dict]:
    return recompute_svc.topic_mastery_rows(
        db,
        institution_id,
        student_ids=student_ids,
        student_id=student_id,
    )


def get_syllabus_completion(db: Session, institution_id: str) -> list[dict]:
    mastery_by_topic = {
        row["topic_id"]: row["mastery"]
        for row in recompute_svc.topic_mastery_rows(db, institution_id)
    }
    boards = (
        db.query(Board)
        .filter(Board.institution_id == institution_id)
        .order_by(Board.name)
        .all()
    )
    rows: list[dict] = []
    for board in boards:
        for grade in sorted(board.grades, key=lambda item: item.name):
            row: dict = {"board": board.name, "grade": grade.name}
            subject_count = 0
            for subject in sorted(grade.subjects, key=lambda item: item.name):
                subject_count += 1
                masteries: list[int] = []
                for chapter in subject.chapters:
                    for topic in chapter.topics:
                        masteries.append(mastery_by_topic.get(topic.id, 0))
                row[subject.name] = round(mean(masteries)) if masteries else 0
            if subject_count > 0:
                rows.append(row)
    return rows


def get_monthly_trend(db: Session, institution_id: str) -> list[dict]:
    return recompute_svc.institution_monthly_trend(db, institution_id)


def get_subject_health_distribution(db: Session, institution_id: str) -> list[dict]:
    rows = recompute_svc.subject_health_distribution(db, institution_id)
    if rows:
        return rows
    students = _students_for_institution(db, institution_id)
    avg = round(mean([s.health for s in students])) if students else 50
    return [{"subject": "Overall", "health": avg}]


def ensure_student_profile(db: Session, user: User) -> StudentProfile:
    """Create a minimal student profile when a student account has none yet."""
    from app.models.academic import Board, Grade

    board_name = "CBSE"
    grade_name = "Grade 8"
    if user.board_id:
        board = db.get(Board, user.board_id)
        if board:
            board_name = board.name
    if user.grade_id:
        grade = db.get(Grade, user.grade_id)
        if grade:
            grade_name = grade.name

    profile = StudentProfile(
        id=user.id,
        user_id=user.id,
        board=board_name,
        grade=grade_name,
        batch="Unassigned",
        center_id="",
        academic_year="2025-26",
        health=70,
        health_status="good",
        readiness=65,
        critical_gaps=1,
        improving=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_student_profile(db: Session, student_id: str) -> dict | None:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return None
    return {
        "id": profile.id,
        "name": profile.user.name,
        "board": profile.board,
        "grade": profile.grade.replace("Grade ", "") if profile.grade.startswith("Grade") else profile.grade,
        "batch": profile.batch,
        "centerId": profile.center_id,
        "academicYear": profile.academic_year,
        "healthScore": profile.health,
        "readiness": profile.readiness,
        "improvement": max(0, profile.readiness - 55),
        "streak": 5 + profile.critical_gaps,
        "status": profile.health_status,
    }


def get_student_health(db: Session, student_id: str) -> dict:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return {"overall": 0, "status": "weak", "trend": 0, "subjects": []}
    subjects = _subjects_for_student(db, profile)
    return {
        "overall": profile.health,
        "status": profile.health_status,
        "trend": 4 if profile.improving else -2,
        "subjects": subjects,
    }


def _subjects_for_student(db: Session, profile: StudentProfile) -> list[dict]:
    subjects_out = recompute_svc.subject_scores_for_student(
        db, profile.user.institution_id, profile.id
    )
    if subjects_out:
        trend = 3 if profile.improving else -1
        for row in subjects_out:
            row["trend"] = trend
        return subjects_out
    return [
        {
            "subjectId": "overall",
            "subjectName": "Overall",
            "health": profile.health,
            "status": profile.health_status,
            "trend": 2 if profile.improving else -1,
        }
    ]


def get_learning_gaps(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return []
    topics = _topic_mastery_rows(db, profile.user.institution_id, student_id=student_id)
    weak = sorted([t for t in topics if t["mastery"] > 0], key=lambda t: t["mastery"])[:3]
    if not weak:
        weak = sorted(topics, key=lambda t: t["mastery"])[:3]
    gaps = []
    for i, t in enumerate(weak):
        gaps.append({
            "id": f"gap-{i + 1}",
            "topicId": f"topic-{t['topic'].lower().replace(' ', '-')}",
            "topicName": t["topic"],
            "subjectName": t["subject"],
            "severity": "high" if i == 0 else "medium",
            "impactOnScore": max(4, 12 - i * 3),
            "rootCause": f"Needs focused practice on {t['topic']}",
            "recommendedAction": f"Practice 15 problem sets on {t['topic']}",
        })
    return gaps


def get_recovery_plan(db: Session, student_id: str) -> list[dict]:
    gaps = get_learning_gaps(db, student_id)
    steps = []
    for i, gap in enumerate(gaps):
        steps.append({
            "id": f"step-{i + 1}",
            "topicName": gap["topicName"],
            "subjectName": gap["subjectName"],
            "action": gap["recommendedAction"],
            "estimatedHours": 2 + i,
            "expectedGain": gap["impactOnScore"],
            "priority": i + 1,
            "completed": False,
        })
    return steps


def get_readiness_predictions(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return []
    subjects = _subjects_for_student(db, profile)
    return [
        {
            "subjectId": s["subjectId"],
            "subjectName": s["subjectName"],
            "currentReadiness": profile.readiness,
            "projectedReadiness": min(100, profile.readiness + 8),
            "examDate": "2026-09-15",
            "confidenceLevel": "high" if profile.improving else "medium",
        }
        for s in subjects[:3]
    ]


def get_improvement_trend(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return []
    events = recompute_svc.student_score_events(db, profile.user.institution_id, student_id)
    trend = recompute_svc.monthly_trend_from_events(events)
    if trend:
        return trend[-6:]
    return [{"month": "Current", "score": profile.health}]


def get_topic_breakdown(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return []
    topics = _topic_mastery_rows(db, profile.user.institution_id, student_id=student_id)
    ranked = sorted(topics, key=lambda t: t["mastery"], reverse=True)
    return [
        {
            "topic": t["topic"],
            "subject": t["subject"],
            "mastery": t["mastery"],
            "status": _health_status(t["mastery"]),
        }
        for t in ranked[:6]
    ]


def get_student_subjects(db: Session, student_id: str) -> list[dict]:
    health = get_student_health(db, student_id)
    return [
        {"name": s["subjectName"], "health": s["health"], "status": s["status"]}
        for s in health["subjects"]
    ]


def get_recent_assessments(db: Session, student_id: str) -> list[dict]:
    subs = (
        db.query(AssessmentSubmission)
        .filter(AssessmentSubmission.student_id == student_id)
        .order_by(AssessmentSubmission.submitted_at.desc())
        .limit(5)
        .all()
    )
    results = []
    for sub in subs:
        assessment = db.get(Assessment, sub.assessment_id)
        if not assessment:
            continue
        accuracy = round((sub.score / sub.max_score) * 100) if sub.max_score else 0
        strong_topics, weak_topics = recompute_svc.submission_topic_tags(
            db, assessment.institution_id, student_id, sub
        )
        results.append({
            "id": sub.id,
            "assessmentId": sub.assessment_id,
            "title": assessment.title,
            "subjectName": assessment.subject,
            "date": sub.submitted_at,
            "score": sub.score,
            "maxScore": sub.max_score,
            "accuracy": accuracy,
            "timeSpent": sub.time_spent_min,
            "weakTopics": weak_topics,
            "strongTopics": strong_topics,
            "insight": f"Scored {accuracy}% on {assessment.title}",
        })
    return results


def get_student_wise_report(db: Session, student_id: str) -> dict | None:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return None
    gaps = get_learning_gaps(db, student_id)
    topics = _topic_mastery_rows(db, profile.user.institution_id, student_id=student_id)
    strong = [t["topic"] for t in sorted(topics, key=lambda x: x["mastery"], reverse=True) if t["mastery"] >= 70][:2]
    weak = [g["topicName"] for g in gaps[:3]]
    recent = get_recent_assessments(db, student_id)
    accuracies = [r["accuracy"] for r in recent]
    avg_accuracy = round(mean(accuracies)) if accuracies else profile.health
    rule_insight = (
        f"{profile.user.name} is {'improving' if profile.improving else 'needs support'} "
        f"with {profile.critical_gaps} critical gaps."
    )
    report_context = {
        "studentName": profile.user.name,
        "board": profile.board,
        "grade": profile.grade,
        "batch": profile.batch,
        "health": profile.health,
        "readiness": profile.readiness,
        "improvement": max(0, profile.readiness - 55),
        "avgAccuracy": avg_accuracy,
        "status": profile.health_status,
        "criticalGaps": profile.critical_gaps,
        "strongTopics": strong,
        "weakTopics": weak,
        "recentTests": [
            {"title": r["title"], "date": r["date"], "accuracy": r["accuracy"], "subject": r["subjectName"]}
            for r in recent
        ],
    }
    return {
        "studentId": student_id,
        "health": profile.health,
        "readiness": profile.readiness,
        "improvement": max(0, profile.readiness - 55),
        "avgAccuracy": avg_accuracy,
        "status": profile.health_status,
        "criticalGaps": profile.critical_gaps,
        "strongTopics": strong,
        "weakTopics": weak,
        "recentTests": report_context["recentTests"],
        "insight": rule_insight,
        "reportType": "snapshot",
    }


def get_overall_performance_report(db: Session, student_id: str) -> dict | None:
    """All student metrics up to date with live AI summary (not stored in DB)."""
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return None

    health = get_student_health(db, student_id)
    gaps = get_learning_gaps(db, student_id)
    readiness = get_readiness_predictions(db, student_id)
    trend = get_improvement_trend(db, student_id)
    topics = get_topic_breakdown(db, student_id)
    monthly = get_monthly_reports(db, student_id)
    recovery = get_recovery_plan(db, student_id)
    subjects = get_student_subjects(db, student_id)
    recent = get_recent_assessments(db, student_id)
    wise = get_student_wise_report(db, student_id) or {}

    context = {
        "studentName": profile.user.name,
        "board": profile.board,
        "grade": profile.grade,
        "batch": profile.batch,
        "health": profile.health,
        "healthStatus": profile.health_status,
        "readiness": profile.readiness,
        "improving": profile.improving,
        "criticalGaps": profile.critical_gaps,
        "subjectHealth": subjects,
        "overallHealth": health,
        "learningGaps": gaps[:5],
        "readinessPredictions": readiness,
        "improvementTrend": trend,
        "topicBreakdown": topics[:8],
        "monthlyReports": monthly,
        "recoveryPlan": recovery,
        "recentAssessments": recent,
        "strongTopics": wise.get("strongTopics", []),
        "weakTopics": wise.get("weakTopics", []),
        "avgAccuracy": wise.get("avgAccuracy", profile.health),
    }

    from app.services import vertex_summary as vertex_svc

    ai_summary = vertex_svc.generate_student_report_summary(context)
    rule_insight = wise.get("insight") or (
        f"{profile.user.name} is {'improving' if profile.improving else 'needs support'} "
        f"with {profile.critical_gaps} critical gaps."
    )

    return {
        "studentId": student_id,
        "studentName": profile.user.name,
        "board": profile.board,
        "grade": profile.grade,
        "batch": profile.batch,
        "health": profile.health,
        "readiness": profile.readiness,
        "improvement": wise.get("improvement", max(0, profile.readiness - 55)),
        "avgAccuracy": wise.get("avgAccuracy", profile.health),
        "status": profile.health_status,
        "criticalGaps": profile.critical_gaps,
        "improving": profile.improving,
        "subjectHealth": subjects,
        "learningGaps": gaps,
        "readinessPredictions": readiness,
        "improvementTrend": trend,
        "topicBreakdown": topics,
        "monthlyReports": monthly,
        "recoveryPlan": recovery,
        "recentAssessments": recent,
        "strongTopics": wise.get("strongTopics", []),
        "weakTopics": wise.get("weakTopics", []),
        "summary": ai_summary or rule_insight,
        "summarySource": "vertex" if ai_summary else "rule-based",
        "reportType": "overall",
    }


def get_monthly_reports(db: Session, student_id: str) -> list[dict]:
    from app.services.analytics_recompute import _parse_event_date

    profile = db.get(StudentProfile, student_id)
    if not profile:
        return []
    events = recompute_svc.student_score_events(db, profile.user.institution_id, student_id)
    by_month: dict[str, list[int]] = defaultdict(list)
    month_labels: dict[str, str] = {}
    for event in events:
        dt = _parse_event_date(str(event["date"]))
        if not dt:
            continue
        key = dt.strftime("%Y-%m")
        month_labels[key] = dt.strftime("%B %Y")
        by_month[key].append(event["pct"])
    if not by_month:
        if profile.health:
            return [
                {
                    "period": "Current",
                    "health": profile.health,
                    "readiness": profile.readiness,
                    "improvement": max(0, profile.readiness - 50),
                }
            ]
        return []
    sorted_keys = sorted(by_month.keys())[-6:]
    reports: list[dict] = []
    prev_health: int | None = None
    for key in sorted_keys:
        scores = by_month[key]
        health = round(mean(scores))
        readiness = min(100, max(35, health + min(12, len(scores) * 2)))
        improvement = max(0, health - prev_health) if prev_health is not None else max(0, health - 50)
        reports.append(
            {
                "period": month_labels[key],
                "health": health,
                "readiness": readiness,
                "improvement": improvement,
            }
        )
        prev_health = health
    reports.reverse()
    return reports


def get_progress_alerts(db: Session, student_id: str) -> list[dict]:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        return []
    alerts = []
    if profile.health < 60:
        alerts.append({"type": "warning", "message": "Academic health below target — review weak topics.", "href": "/student/reports"})
    if profile.critical_gaps > 0:
        alerts.append({"type": "risk", "message": f"{profile.critical_gaps} critical gap(s) need attention.", "href": "/student/reports"})
    return alerts


def _student_ids_for_batch(
    db: Session, institution_id: str, *, batch_id: str | None = None, batch_name: str | None = None
) -> set[str] | None:
    if batch_id:
        batch = db.get(Batch, batch_id)
        if not batch or batch.institution_id != institution_id:
            return set()
        return set(_batch_student_ids(db, batch_id))
    if batch_name:
        batch = (
            db.query(Batch)
            .filter(Batch.institution_id == institution_id, Batch.name == batch_name)
            .first()
        )
        if not batch:
            return set()
        return set(_batch_student_ids(db, batch.id))
    return None


def get_tutor_topic_weakness(
    db: Session,
    institution_id: str,
    batch_name: str | None = None,
    *,
    batch_id: str | None = None,
) -> list[dict]:
    student_ids = _student_ids_for_batch(
        db, institution_id, batch_id=batch_id, batch_name=batch_name
    )
    topics = _topic_mastery_rows(db, institution_id, student_ids=student_ids)
    with_data = [t for t in topics if t["mastery"] > 0]
    weak = sorted(with_data or topics, key=lambda t: t["mastery"])[:5]
    result = []
    for i, t in enumerate(weak):
        result.append({
            "rank": i + 1,
            "topic": t["topic"],
            "avgMastery": t["mastery"],
            "suggestedNextClass": f"Level Up: {t['topic']} Mastery",
            "expectedGain": max(4, 10 - i * 2),
        })
    return result


def get_tutor_at_risk(
    db: Session,
    institution_id: str,
    *,
    batch_id: str | None = None,
    batch_name: str | None = None,
) -> list[dict]:
    students = _students_for_institution(db, institution_id)
    student_ids = _student_ids_for_batch(
        db, institution_id, batch_id=batch_id, batch_name=batch_name
    )
    if student_ids is not None:
        students = [s for s in students if s.id in student_ids]
    at_risk = [s for s in students if s.health < 60 or not s.improving or s.critical_gaps >= 2]
    result = []
    for s in sorted(at_risk, key=lambda x: x.health)[:10]:
        result.append({
            "name": s.user.name,
            "grade": int("".join(filter(str.isdigit, s.grade)) or 8),
            "board": s.board,
            "reason": f"Health {s.health}% · {s.critical_gaps} gaps · trend {'↓' if not s.improving else '↑'}",
            "risk": min(99, 100 - s.health + s.critical_gaps * 5),
        })
    return result


def get_tutor_batch_heatmap(
    db: Session,
    institution_id: str,
    *,
    batch_id: str | None = None,
    batch_name: str | None = None,
) -> list[dict]:
    student_ids = _student_ids_for_batch(
        db, institution_id, batch_id=batch_id, batch_name=batch_name
    )
    topics = _topic_mastery_rows(db, institution_id, student_ids=student_ids)
    return [{"topic": t["topic"], "mastery": t["mastery"]} for t in topics]


def get_class_insights(db: Session, institution_id: str) -> list[dict]:
    students = _students_for_institution(db, institution_id)
    batches = db.query(Batch).filter(Batch.institution_id == institution_id).all()
    result = []
    for b in batches:
        ids = set(_batch_student_ids(db, b.id))
        cohort = [s for s in students if s.id in ids]
        if not cohort:
            continue
        weakness = get_tutor_topic_weakness(db, institution_id, batch_id=b.id)
        avg_health = round(mean([s.health for s in cohort]))
        weak_topic = weakness[0]["topic"] if weakness else "core topics"
        affected = len([s for s in cohort if s.health < 60 or s.critical_gaps >= 2])
        severity = "high" if avg_health < 55 or affected >= max(2, len(cohort) // 3) else "medium" if avg_health < 70 else "low"
        result.append(
            {
                "id": f"ci-{b.id}",
                "title": f"{b.name}: strengthen {weak_topic}",
                "description": (
                    f"{b.board} · {b.grade} · {len(cohort)} students · "
                    f"class health {avg_health}%"
                ),
                "affectedStudents": affected,
                "topicName": weak_topic,
                "subjectName": b.subject or "General",
                "severity": severity,
                "suggestedIntervention": (
                    weakness[0]["suggestedNextClass"]
                    if weakness
                    else "Review recent assessments and marks"
                ),
            }
        )
    return result


def get_tutor_copilot_summary(
    db: Session,
    institution_id: str,
    batch_name: str | None = None,
    *,
    batch_id: str | None = None,
) -> dict:
    batches = db.query(Batch).filter(Batch.institution_id == institution_id).all()
    batch = None
    if batch_id:
        batch = next((b for b in batches if b.id == batch_id), None)
    elif batch_name:
        batch = next((b for b in batches if b.name == batch_name), None)
    if batch is None and batches:
        batch = batches[0]
    students = _students_for_institution(db, institution_id)
    if batch:
        ids = set(_batch_student_ids(db, batch.id))
        cohort = [s for s in students if s.id in ids]
    else:
        cohort = students
    weakness = get_tutor_topic_weakness(
        db, institution_id, batch_name=batch.name if batch else None, batch_id=batch.id if batch else None
    )
    top = weakness[0] if weakness else None
    topic_rows = _topic_mastery_rows(
        db,
        institution_id,
        student_ids=set(_batch_student_ids(db, batch.id)) if batch else None,
    )
    strong_topics = [
        t["topic"]
        for t in sorted(topic_rows, key=lambda x: x["mastery"], reverse=True)
        if t["mastery"] >= 70
    ][:2]
    return {
        "headline": top["suggestedNextClass"] if top else "Level Up: Geometry Mastery",
        "subject": batch.subject if batch and batch.subject else "Mathematics",
        "batchName": batch.name if batch else "—",
        "studentCount": len(cohort),
        "avgScore": batch.avg_score if batch and batch.avg_score else round(mean([s.health for s in cohort])) if cohort else 0,
        "strongTopics": strong_topics,
        "weakTopics": [w["topic"] for w in weakness[:3]],
        "expectedImprovement": top["expectedGain"] if top else 8,
    }


def _subject_name_matches(candidate: str, query: str) -> bool:
    c = candidate.strip().lower()
    q = query.strip().lower()
    if not c or not q:
        return False
    return c == q or q in c or c in q


def get_subject_topics(db: Session, institution_id: str, subject: str) -> list[dict]:
    rows = _topic_mastery_rows(db, institution_id)
    by_topic: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if not _subject_name_matches(row["subject"], subject):
            continue
        if row["mastery"] > 0:
            by_topic[row["topic"]].append(row["mastery"])
    return sorted(
        [{"name": topic, "mastery": round(mean(scores))} for topic, scores in by_topic.items()],
        key=lambda item: item["mastery"],
    )


def _student_subject_topics(
    db: Session, institution_id: str, student_id: str, subject: str
) -> list[dict]:
    rows = _topic_mastery_rows(db, institution_id, student_id=student_id)
    return [
        {"name": row["topic"], "mastery": row["mastery"]}
        for row in rows
        if _subject_name_matches(row["subject"], subject) and row["mastery"] > 0
    ]


def get_subject_students(db: Session, institution_id: str, subject: str) -> list[dict]:
    students = _students_for_institution(db, institution_id)
    result = []
    for s in students:
        subj_rows = recompute_svc.subject_scores_for_student(db, institution_id, s.id)
        match = next(
            (row for row in subj_rows if _subject_name_matches(row["subjectName"], subject)),
            None,
        )
        topic_rows = _student_subject_topics(db, institution_id, s.id, subject)
        if match:
            result.append(
                {
                    "id": s.id,
                    "name": s.user.name,
                    "grade": s.grade,
                    "health": match["health"],
                    "readiness": s.readiness,
                    "batch": s.batch,
                    "topics": topic_rows,
                }
            )
            continue
        events = recompute_svc.student_score_events(db, institution_id, s.id)
        if any(_subject_name_matches(event["subject"], subject) for event in events):
            result.append(
                {
                    "id": s.id,
                    "name": s.user.name,
                    "grade": s.grade,
                    "health": s.health,
                    "readiness": s.readiness,
                    "batch": s.batch,
                    "topics": topic_rows,
                }
            )
    return result


def get_tutor_name_map(db: Session, institution_id: str) -> dict[str, str]:
    tutors = db.query(User).filter(User.institution_id == institution_id, User.role == "tutor").all()
    return {t.id: t.name for t in tutors}


def get_student_dashboard(db: Session, student_id: str, institution_id: str) -> dict:
    """All student analytics in one payload."""
    health = get_student_health(db, student_id)
    return {
        "overview": get_institution_overview(db, institution_id),
        "tutorNames": get_tutor_name_map(db, institution_id),
        "profile": get_student_profile(db, student_id),
        "health": health,
        "gaps": get_learning_gaps(db, student_id),
        "recovery": get_recovery_plan(db, student_id),
        "readiness": get_readiness_predictions(db, student_id),
        "improvementTrend": get_improvement_trend(db, student_id),
        "topicBreakdown": get_topic_breakdown(db, student_id),
        "subjects": [{"name": s["subjectName"], "health": s["health"], "status": s["status"]} for s in health["subjects"]],
        "recentAssessments": get_recent_assessments(db, student_id),
        "report": get_student_wise_report(db, student_id),
        "monthlyReports": get_monthly_reports(db, student_id),
        "alerts": get_progress_alerts(db, student_id),
    }


def get_tutor_dashboard(db: Session, institution_id: str) -> dict:
    """All tutor analytics in one payload."""
    return {
        "overview": get_institution_overview(db, institution_id),
        "tutorNames": get_tutor_name_map(db, institution_id),
        "topicWeakness": get_tutor_topic_weakness(db, institution_id),
        "atRisk": get_tutor_at_risk(db, institution_id),
        "batchHeatmap": get_tutor_batch_heatmap(db, institution_id),
        "classInsights": get_class_insights(db, institution_id),
        "copilot": get_tutor_copilot_summary(db, institution_id),
        "studentMaster": get_student_master_profiles(db, institution_id),
    }


def get_admin_dashboard(db: Session, institution_id: str) -> dict:
    """All admin analytics in one payload."""
    return {
        "overview": get_institution_overview(db, institution_id),
        "tutorNames": get_tutor_name_map(db, institution_id),
        "centers": get_centers_analytics(db, institution_id),
        "boardReport": get_board_report(db, institution_id),
        "teachers": get_teachers(db, institution_id),
        "hardestTopics": get_hardest_topics(db, institution_id),
        "syllabusCompletion": get_syllabus_completion(db, institution_id),
        "monthlyTrend": get_monthly_trend(db, institution_id),
        "subjectHealth": get_subject_health_distribution(db, institution_id),
        "studentMaster": get_student_master_profiles(db, institution_id),
        "topicWeakness": get_tutor_topic_weakness(db, institution_id),
        "atRisk": get_tutor_at_risk(db, institution_id),
        "batchHeatmap": get_tutor_batch_heatmap(db, institution_id),
        "classInsights": get_class_insights(db, institution_id),
        "copilot": get_tutor_copilot_summary(db, institution_id),
    }


def get_student_master_profiles(db: Session, institution_id: str) -> list[dict]:
    students = _students_for_institution(db, institution_id)
    result = []
    for s in students:
        batch_ids = [
            row.batch_id
            for row in db.query(BatchStudent).filter(BatchStudent.student_id == s.id).all()
        ]
        result.append(
            {
                "id": s.id,
                "name": s.user.name,
                "board": s.board,
                "grade": s.grade,
                "batch": s.batch,
                "batchIds": batch_ids,
                "centerId": s.center_id,
                "academicYear": s.academic_year,
                "schoolName": s.school_name,
                "email": s.user.email,
                "status": s.status,
            }
        )
    return result
