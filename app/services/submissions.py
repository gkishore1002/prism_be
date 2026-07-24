"""Assessment submission side-effects."""

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.models.user import StudentProfile
from app.services.analytics_recompute import recompute_student_profile


def update_student_profile_after_submission(
    db: Session,
    profile: StudentProfile,
    score: int,
    max_score: int,
    submitted_at: str,
) -> None:
    recompute_student_profile(db, profile.id)


def existing_submission(
    db: Session, assessment_id: str, student_id: str
) -> AssessmentSubmission | None:
    return (
        db.query(AssessmentSubmission)
        .filter(
            AssessmentSubmission.assessment_id == assessment_id,
            AssessmentSubmission.student_id == student_id,
        )
        .first()
    )


def update_assessment_class_avg(db: Session, assessment_id: str) -> None:
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        return

    submissions = (
        db.query(AssessmentSubmission)
        .filter(AssessmentSubmission.assessment_id == assessment_id)
        .all()
    )
    percentages = [
        round((sub.score / sub.max_score) * 100)
        for sub in submissions
        if sub.max_score > 0
    ]
    assessment.class_avg = round(sum(percentages) / len(percentages)) if percentages else None
    db.add(assessment)
