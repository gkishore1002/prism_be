"""Build Learning Genome cohort dataset from DB marks, submissions, and profiles."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.models.content import Batch, BatchStudent
from app.models.user import StudentProfile
from app.services.analytics import _batch_student_ids, _students_for_institution, _topic_mastery_rows
from app.services.marks import marks_for_students


SUBJECT_CODES = ("TAM", "ENG", "MAT", "SCI", "SOC")

CLUSTER_LABELS = (
    "High Performers",
    "Fast Improvers",
    "Steady Performers",
    "Inconsistent Learners",
    "Hidden Talent",
    "Needs Immediate Support",
)


def _subject_code(name: str) -> str:
    n = name.strip().lower()
    if "tamil" in n:
        return "TAM"
    if "english" in n:
        return "ENG"
    if "math" in n:
        return "MAT"
    if "sci" in n:
        return "SCI"
    if "social" in n:
        return "SOC"
    return "ENG"


def _parse_date_label(value: str) -> str:
    if not value:
        return "—"
    if "T" in value:
        value = value.split("T", 1)[0]
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d-%b")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).strftime("%d-%b")
        except ValueError:
            continue
    return value[:10]


def _cohort_profiles(db: Session, institution_id: str, batch_id: str | None) -> tuple[Batch | None, list[StudentProfile]]:
    batches = db.query(Batch).filter(Batch.institution_id == institution_id).all()
    if not batches:
        return None, []
    batch = next((b for b in batches if b.id == batch_id), None) if batch_id else None
    if batch is None:
        for b in batches:
            if _batch_student_ids(db, b.id):
                batch = b
                break
        if batch is None:
            batch = batches[0]
    ids = set(_batch_student_ids(db, batch.id))
    students = _students_for_institution(db, institution_id)
    cohort = [s for s in students if s.id in ids]
    return batch, cohort


def _score_events_for_cohort(
    db: Session,
    institution_id: str,
    batch: Batch,
    cohort: list[StudentProfile],
) -> list[dict]:
    student_ids = [s.id for s in cohort]
    events: list[dict] = []

    for row in marks_for_students(db, institution_id, student_ids, batch_id=batch.id):
        events.append(
            {
                "studentId": row.student_id,
                "date": row.conducted_on,
                "dateLabel": _parse_date_label(row.conducted_on),
                "subject": row.subject,
                "subjectCode": _subject_code(row.subject),
                "pct": row.percentage,
                "source": "marks",
                "title": row.assessment_title,
                "scored": float(row.scored_marks),
                "maxMarks": int(row.max_marks),
                "sessionId": row.session_id,
            }
        )

    subs = (
        db.query(AssessmentSubmission)
        .filter(AssessmentSubmission.student_id.in_(student_ids))
        .order_by(AssessmentSubmission.submitted_at.asc())
        .all()
    )
    for sub in subs:
        assessment = db.get(Assessment, sub.assessment_id)
        if not assessment or assessment.institution_id != institution_id:
            continue
        pct = round((sub.score / sub.max_score) * 100) if sub.max_score else 0
        events.append(
            {
                "studentId": sub.student_id,
                "date": sub.submitted_at or "",
                "dateLabel": _parse_date_label(sub.submitted_at or ""),
                "subject": assessment.subject,
                "subjectCode": _subject_code(assessment.subject),
                "pct": pct,
                "source": "assessment",
                "title": assessment.title,
                "assessmentTitle": assessment.title,
                "scored": float(sub.score),
                "maxMarks": int(sub.max_score),
                "assessmentId": assessment.id,
                "sessionId": assessment.id,
            }
        )

    events.sort(key=lambda e: e["date"])
    return events


def _student_assessment_attendance(
    db: Session, institution_id: str, student_id: str
) -> tuple[int, int]:
    from app.utils import from_json_list

    assessments = (
        db.query(Assessment)
        .filter(
            Assessment.institution_id == institution_id,
            Assessment.status.in_(("live", "completed")),
        )
        .all()
    )
    invited = 0
    submitted = 0
    for assessment in assessments:
        assigned = from_json_list(assessment.assigned_student_ids)
        if not assigned or student_id not in assigned:
            continue
        invited += 1
        sub = (
            db.query(AssessmentSubmission)
            .filter(
                AssessmentSubmission.assessment_id == assessment.id,
                AssessmentSubmission.student_id == student_id,
            )
            .first()
        )
        if sub:
            submitted += 1
    absent = max(0, invited - submitted)
    pct = round((submitted / invited) * 100) if invited else 100
    return pct, absent


def _pct_grade(pct: float) -> str:
    if pct >= 90:
        return "A+"
    if pct >= 80:
        return "A"
    if pct >= 70:
        return "B+"
    if pct >= 60:
        return "B"
    if pct >= 50:
        return "C"
    if pct >= 40:
        return "D"
    return "E"


def _exam_bundles(events: list[dict]) -> list[dict]:
    """Group score events into assessment/exam windows (title + date)."""
    bundles: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for ev in events:
        title = str(ev.get("title") or "Assessment")
        date_label = ev.get("dateLabel") or _parse_date_label(str(ev.get("date", "")))
        key = (title, date_label)
        if key not in bundles:
            bundles[key] = {
                "title": title,
                "date": date_label,
                "dateRaw": str(ev.get("date", "")),
                "assessmentId": ev.get("assessmentId"),
                "sessionId": ev.get("sessionId"),
                "subjects": [],
            }
            order.append(key)
        bundles[key]["subjects"].append(
            {
                "name": ev["subject"],
                "code": ev.get("subjectCode") or _subject_code(ev["subject"]),
                "pct": ev["pct"],
                "scored": ev.get("scored"),
                "maxMarks": ev.get("maxMarks"),
                "grade": _pct_grade(ev["pct"]),
            }
        )

    exams: list[dict] = []
    prev_overall: float | None = None
    for key in order:
        bundle = bundles[key]
        pcts = [s["pct"] for s in bundle["subjects"]]
        overall = round(mean(pcts), 1) if pcts else 0.0
        vs_prev = None if prev_overall is None else round(overall - prev_overall, 1)
        exams.append(
            {
                **bundle,
                "overall": overall,
                "subjectCount": len(bundle["subjects"]),
                "vsPrev": vs_prev,
            }
        )
        prev_overall = overall
    return exams


def _class_avg_for_subject(
    db: Session,
    institution_id: str,
    *,
    batch_id: str | None,
    subject: str,
    title: str,
    date_label: str,
) -> float | None:
    """Average % across batch peers for the same assessment window + subject."""
    if not batch_id:
        return None
    peer_ids = _batch_student_ids(db, batch_id)
    if not peer_ids:
        return None
    scores: list[int] = []
    for sid in peer_ids:
        from app.services.analytics_recompute import student_score_events

        for ev in student_score_events(db, institution_id, sid):
            ev_title = str(ev.get("title") or "Assessment")
            ev_date = _parse_date_label(str(ev.get("date", "")))
            if (
                ev_title == title
                and ev_date == date_label
                and ev["subject"].strip().lower() == subject.strip().lower()
            ):
                scores.append(int(ev["pct"]))
    if not scores:
        return None
    return round(mean(scores), 1)


def _enrich_latest_assessment(
    db: Session,
    institution_id: str,
    exams: list[dict],
    *,
    batch_id: str | None,
) -> dict | None:
    if not exams:
        return None
    latest = exams[-1]
    subjects = []
    for row in latest["subjects"]:
        class_avg = _class_avg_for_subject(
            db,
            institution_id,
            batch_id=batch_id,
            subject=row["name"],
            title=latest["title"],
            date_label=latest["date"],
        )
        vs_class = None if class_avg is None else round(row["pct"] - class_avg, 1)
        subjects.append(
            {
                **row,
                "classAvg": class_avg,
                "vsClass": vs_class,
            }
        )
    return {
        "title": latest["title"],
        "date": latest["date"],
        "assessmentId": latest.get("assessmentId"),
        "subjects": subjects,
        "overall": latest["overall"],
    }


def _build_student_profile(
    profile: StudentProfile,
    events: list[dict],
    rank: int,
    *,
    attendance_pct: int = 100,
    absent_count: int = 0,
) -> dict | None:
    if not events:
        return None

    subj_scores: dict[str, list[int]] = defaultdict(list)
    for ev in events:
        subj_scores[ev["subjectCode"]].append(ev["pct"])

    subj_avg: dict[str, int] = {}
    for code in SUBJECT_CODES:
        vals = subj_scores.get(code, [])
        if vals:
            subj_avg[code] = round(mean(vals))

    if not subj_avg:
        return None

    overall_vals = list(subj_avg.values())
    overall = round(mean(overall_vals))

    pct_series = [e["pct"] for e in events]
    consistency_sd = round(pstdev(pct_series), 1) if len(pct_series) >= 2 else 12.0
    consistency = "High" if consistency_sd < 10 else "Medium" if consistency_sd < 18 else "Low"

    if len(pct_series) >= 4:
        mid = len(pct_series) // 2
        first_half = mean(pct_series[:mid])
        second_half = mean(pct_series[mid:])
        velocity = round(second_half - first_half, 1)
    elif len(pct_series) >= 2:
        velocity = round(pct_series[-1] - pct_series[0], 1)
    else:
        velocity = 0.0

    if velocity >= 4:
        trend = "Improving"
    elif velocity <= -4:
        trend = "Declining"
    else:
        trend = "Stable"

    strongest = max(subj_avg.items(), key=lambda x: x[1])[0]
    weakest = min(subj_avg.items(), key=lambda x: x[1])[0]

    best_ev = max(events, key=lambda e: e["pct"])
    worst_ev = min(events, key=lambda e: e["pct"])
    best_day = {"date": best_ev["dateLabel"], "score": best_ev["pct"]}
    worst_day = {"date": worst_ev["dateLabel"], "score": worst_ev["pct"]}

    exam_shock = [
        e["dateLabel"]
        for e in events
        if e["pct"] < max(0, overall - 25)
    ][:3]

    daily_curve = [
        {
            "date": e["dateLabel"],
            "subject": e["subjectCode"],
            "score": e["pct"],
            "title": e.get("title") or "Assessment",
        }
        for e in events[-12:]
    ]

    exams = _exam_bundles(events)

    return {
        "overall": overall,
        "subjAvg": subj_avg,
        "strongest": strongest,
        "weakest": weakest,
        "bestDay": best_day,
        "worstDay": worst_day,
        "consistency": consistency,
        "consistencySd": consistency_sd,
        "trend": trend,
        "velocity": velocity,
        "predicted": min(100, max(0, round(overall + velocity))),
        "recovery": "Excellent" if trend == "Improving" else "Good" if overall >= 60 else "Needs Support",
        "examShock": exam_shock,
        "attendancePct": attendance_pct,
        "absentCount": absent_count,
        "attendanceImpact": (
            f"{absent_count} missed assessment(s)" if absent_count > 0 else None
        ),
        "balance": "Polarized" if consistency_sd >= 20 else "Moderate",
        "growthPotential": min(99, max(5, round(velocity * 3 + (100 - overall) * 0.4))),
        "confidence": overall,
        "dailyCurve": daily_curve,
        "examHistory": exams,
        "rank": rank,
        "studentId": profile.id,
        "scoreSources": sorted({e["source"] for e in events}),
    }


def _assign_clusters(students_by_name: dict[str, dict]) -> dict[str, list[str]]:
    clusters: dict[str, list[str]] = {label: [] for label in CLUSTER_LABELS}
    for name, s in students_by_name.items():
        overall = s["overall"]
        velocity = s["velocity"]
        sd = s["consistencySd"]
        growth = s["growthPotential"]
        if overall >= 85:
            clusters["High Performers"].append(name)
        elif velocity >= 8:
            clusters["Fast Improvers"].append(name)
        elif sd >= 18:
            clusters["Inconsistent Learners"].append(name)
        elif overall < 55 or s.get("recovery") == "Needs Support":
            clusters["Needs Immediate Support"].append(name)
        elif growth >= 50 and overall < 75:
            clusters["Hidden Talent"].append(name)
        else:
            clusters["Steady Performers"].append(name)
    return {k: v for k, v in clusters.items() if v}


def _concepts_from_score_events(events: list[dict]) -> list[dict]:
    by_subject: dict[str, list[int]] = defaultdict(list)
    for ev in events:
        by_subject[ev["subject"]].append(ev["pct"])
    rows = [
        {"concept": f"Class average · {subject}", "subject": subject, "masteryPct": round(mean(pcts))}
        for subject, pcts in by_subject.items()
    ]
    rows.sort(key=lambda r: r["masteryPct"])
    return [r for r in rows if r["masteryPct"] < 55][:8]


def _concepts_not_mastered(db: Session, institution_id: str, batch: Batch | None) -> list[dict]:
    topics = _topic_mastery_rows(db, institution_id)
    if batch:
        topics = [t for t in topics if t["board"] == batch.board and t["grade"] == batch.grade]
    weak = [t for t in topics if t["mastery"] < 55]
    weak.sort(key=lambda t: t["mastery"])
    return [
        {"concept": t["topic"], "subject": t["subject"], "masteryPct": t["mastery"]}
        for t in weak[:8]
    ]


def get_cohort_report(db: Session, institution_id: str, batch_id: str | None = None) -> dict:
    batch, cohort = _cohort_profiles(db, institution_id, batch_id)
    if not batch or not cohort:
        return {
            "batchId": batch_id,
            "batchName": batch.name if batch else None,
            "meta": {
                "classAvg": 0,
                "dates": [],
                "subjSeq": list(SUBJECT_CODES),
                "totalStudents": 0,
                "windowLabel": "No data yet",
                "subjectsLabel": "—",
                "totalMarks": 0,
                "subjectsCount": 0,
            },
            "clusters": {},
            "students": {},
            "conceptsNotMastered": _concepts_not_mastered(db, institution_id, batch),
            "dataSource": "empty",
        }

    events = _score_events_for_cohort(db, institution_id, batch, cohort)
    by_student: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        by_student[ev["studentId"]].append(ev)

    profiles_map = {s.id: s for s in cohort}
    built: list[tuple[str, dict]] = []
    for sid, profile in profiles_map.items():
        name = profile.user.name
        att_pct, absent = _student_assessment_attendance(db, institution_id, sid)
        prof = _build_student_profile(
            profile,
            by_student.get(sid, []),
            rank=0,
            attendance_pct=att_pct,
            absent_count=absent,
        )
        if prof:
            built.append((name, prof))

    built.sort(key=lambda item: item[1]["overall"], reverse=True)
    students: dict[str, dict] = {}
    for idx, (name, prof) in enumerate(built, start=1):
        prof["rank"] = idx
        students[name] = prof

    class_avg = round(mean(p["overall"] for p in students.values())) if students else 0
    total_marks = sum(e.get("pct", 0) for e in events)
    subjects = sorted({e["subject"] for e in events})
    dates = sorted({_parse_date_label(e["date"]) for e in events if e["date"]})
    subj_seq = [_subject_code(s) for s in subjects[:10]] or list(SUBJECT_CODES)

    window_label = "No scores yet"
    if dates:
        window_label = f"{dates[0]} – {dates[-1]}"

    assessment_count = sum(1 for e in events if e["source"] == "assessment")
    marks_count = sum(1 for e in events if e["source"] == "marks")

    concepts = _concepts_from_score_events(events) if events else []
    if not concepts:
        concepts = _concepts_not_mastered(db, institution_id, batch)

    return {
        "batchId": batch.id,
        "batchName": batch.name,
        "meta": {
            "classAvg": class_avg,
            "dates": dates[:10],
            "subjSeq": subj_seq,
            "totalStudents": len(students),
            "batchStudentCount": len(cohort),
            "scoredStudentCount": len(students),
            "assessmentResultCount": assessment_count,
            "savedMarksCount": marks_count,
            "windowLabel": window_label,
            "subjectsLabel": " · ".join(subjects[:6]) if subjects else "—",
            "totalMarks": total_marks,
            "subjectsCount": len(subjects),
        },
        "clusters": _assign_clusters(students) if students else {},
        "students": students,
        "conceptsNotMastered": concepts,
        "dataSource": "live" if events else "empty",
    }


def get_student_genome(db: Session, institution_id: str, student_id: str) -> dict | None:
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != institution_id:
        return None

    membership = (
        db.query(BatchStudent)
        .filter(BatchStudent.student_id == student_id)
        .first()
    )
    batch = db.get(Batch, membership.batch_id) if membership else None

    from app.services.analytics_recompute import student_score_events

    raw_events = student_score_events(db, institution_id, student_id)
    events = [
        {
            "studentId": student_id,
            "date": event["date"],
            "dateLabel": _parse_date_label(str(event["date"])),
            "subject": event["subject"],
            "subjectCode": _subject_code(event["subject"]),
            "pct": event["pct"],
            "source": event["source"],
            "title": event.get("title") or "Assessment",
            "scored": event.get("scored"),
            "maxMarks": event.get("maxMarks"),
            "assessmentId": event.get("assessmentId"),
            "sessionId": event.get("sessionId"),
        }
        for event in raw_events
    ]

    att_pct, absent = _student_assessment_attendance(db, institution_id, student_id)
    genome = _build_student_profile(
        profile,
        events,
        rank=1,
        attendance_pct=att_pct,
        absent_count=absent,
    )
    if not genome:
        return {
            "name": profile.user.name,
            "profile": None,
            "totalStudents": len(_batch_student_ids(db, batch.id)) if batch else 1,
            "batchLabel": f"{profile.batch} · {profile.board} · {profile.grade}" if profile.batch else None,
            "source": "empty",
            "message": "No assessment results or saved marks yet for this student.",
        }

    total = len(_batch_student_ids(db, batch.id)) if batch else 1
    rank = 1
    if batch:
        cohort_report = get_cohort_report(db, institution_id, batch.id)
        rank = cohort_report.get("students", {}).get(profile.user.name, {}).get("rank", 1)
        genome["rank"] = rank
        total = max(cohort_report.get("meta", {}).get("batchStudentCount", total), 1)

    genome["latestAssessment"] = _enrich_latest_assessment(
        db,
        institution_id,
        genome.get("examHistory") or [],
        batch_id=batch.id if batch else None,
    )

    from app.services import vertex_summary as vertex_svc

    narrative_context = {
        "studentName": profile.user.name,
        "batchLabel": f"{profile.batch} · {profile.board} · {profile.grade}" if profile.batch else None,
        "totalStudents": max(total, 1),
        "profile": genome,
    }
    ai_narrative, ai_narrative_ta = vertex_svc.generate_pair_parallel(
        vertex_svc.generate_student_genome_narrative,
        vertex_svc.generate_student_genome_narrative_ta,
        narrative_context,
    )
    rule_narrative_ta = (
        f"{profile.user.name} அவர்களின் ஒட்டுமொத்த மதிப்பெண் {genome.get('overall', 0)}% ஆகும். "
        f"வகுப்பில் #{rank} இடம். விரிவான பகுப்பாய்வுக்கு CSC மையத்தை அணுகவும்."
    )

    return {
        "name": profile.user.name,
        "profile": genome,
        "totalStudents": max(total, 1),
        "batchLabel": f"{profile.batch} · {profile.board} · {profile.grade}" if profile.batch else None,
        "source": "live",
        "narrative": ai_narrative,
        "narrativeTa": ai_narrative_ta or rule_narrative_ta,
        "narrativeSource": "vertex" if ai_narrative else "rule-based",
    }
