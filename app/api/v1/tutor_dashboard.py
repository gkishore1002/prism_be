import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.tutor_settings import TutorDashboardSetting
from app.models.user import User
from app.schemas import TutorDashboardSettingsOut, TutorDashboardSettingsUpdate

router = APIRouter(prefix="/tutor", tags=["tutor"], route_class=CamelCaseAPIRoute)

DEFAULT_SETTINGS = {
    "pageTitle": "Tutor Copilot",
    "pageSubtitle": "Your command center — batch health, assessments, and what to teach next.",
    "pageEyebrow": "CBSE · Grade 8",
    "heroContent": {
        "eyebrow": "AI Tutor Summary",
        "strongLabel": "Strong",
        "needsFocusLabel": "Needs focus",
        "expectedImprovementLabel": "Expected improvement",
        "ctaText": "Schedule follow-up test",
        "ctaLink": "/tutor/assessments",
    },
    "heroSummaryOverride": None,
}


def _to_out(row: TutorDashboardSetting) -> TutorDashboardSettingsOut:
    data = json.loads(row.payload)
    return TutorDashboardSettingsOut(
        page_title=data.get("pageTitle", DEFAULT_SETTINGS["pageTitle"]),
        page_subtitle=data.get("pageSubtitle", DEFAULT_SETTINGS["pageSubtitle"]),
        page_eyebrow=data.get("pageEyebrow", DEFAULT_SETTINGS["pageEyebrow"]),
        hero_content=data.get("heroContent", DEFAULT_SETTINGS["heroContent"]),
        hero_summary_override=data.get("heroSummaryOverride"),
        updated_at=row.updated_at,
    )


@router.get("/dashboard", response_model=TutorDashboardSettingsOut)
def get_dashboard_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor")),
) -> TutorDashboardSettingsOut:
    row = db.get(TutorDashboardSetting, user.id)
    if not row:
        now = datetime.now(timezone.utc).isoformat()
        return TutorDashboardSettingsOut(
            page_title=DEFAULT_SETTINGS["pageTitle"],
            page_subtitle=DEFAULT_SETTINGS["pageSubtitle"],
            page_eyebrow=DEFAULT_SETTINGS["pageEyebrow"],
            hero_content=DEFAULT_SETTINGS["heroContent"],
            hero_summary_override=None,
            updated_at=now,
        )
    return _to_out(row)


@router.put("/dashboard", response_model=TutorDashboardSettingsOut)
def save_dashboard_settings(
    body: TutorDashboardSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor")),
) -> TutorDashboardSettingsOut:
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        {
            "pageTitle": body.page_title,
            "pageSubtitle": body.page_subtitle,
            "pageEyebrow": body.page_eyebrow,
            "heroContent": body.hero_content,
            "heroSummaryOverride": body.hero_summary_override,
        }
    )
    row = db.get(TutorDashboardSetting, user.id)
    if row:
        row.payload = payload
        row.updated_at = now
    else:
        row = TutorDashboardSetting(
            user_id=user.id,
            institution_id=user.institution_id,
            payload=payload,
            updated_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/dashboard", status_code=status.HTTP_204_NO_CONTENT)
def reset_dashboard_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor")),
) -> None:
    row = db.get(TutorDashboardSetting, user.id)
    if row:
        db.delete(row)
        db.commit()
