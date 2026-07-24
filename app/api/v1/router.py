from fastapi import APIRouter

from app.api.v1 import analytics, assessments, auth, curriculum, institutions, marks, notifications, portal, questions, search, tutor_dashboard

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(portal.router)
api_router.include_router(search.router)
api_router.include_router(institutions.router)
api_router.include_router(curriculum.router)
api_router.include_router(questions.router)
api_router.include_router(assessments.router)
api_router.include_router(notifications.router)
api_router.include_router(tutor_dashboard.router)
api_router.include_router(marks.router)
api_router.include_router(analytics.router)
