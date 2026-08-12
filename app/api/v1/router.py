from fastapi import APIRouter

from app.api.v1 import admins, analytics, assessments, auth, csc, curriculum, exports, imports, institutions, marks, notifications, platform, portal, questions, search, setup, staff, tutor_dashboard

api_router = APIRouter()
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(platform.router)
api_router.include_router(admins.router)
api_router.include_router(staff.router)
api_router.include_router(portal.router)
api_router.include_router(search.router)
api_router.include_router(institutions.router)
api_router.include_router(curriculum.router)
api_router.include_router(csc.router)
api_router.include_router(questions.router)
api_router.include_router(assessments.router)
api_router.include_router(notifications.router)
api_router.include_router(tutor_dashboard.router)
api_router.include_router(marks.router)
api_router.include_router(analytics.router)
api_router.include_router(exports.router)
api_router.include_router(imports.router)
