"""Role resolution and validation for multi-role accounts."""

from app.models.user import User
from app.services.user_roles import get_allowed_roles as _get_allowed_roles

# Legacy demo seed emails (BrightPath SQLite seed)
DEDICATED_EMAIL_ROLES: dict[str, str] = {
    "arjun@brightpath.edu": "student",
    "priya@brightpath.edu": "tutor",
    "rajesh@brightpath.edu": "admin",
}

MULTI_ROLE_EMAIL = "demo@prism.app"
LEGACY_DEMO_ROLES = ("student", "tutor", "admin")


def get_allowed_roles(user: User) -> list[str]:
    email = user.email.strip().lower()
    if email == MULTI_ROLE_EMAIL and not user.roles:
        return list(LEGACY_DEMO_ROLES)
    if email in DEDICATED_EMAIL_ROLES and not user.roles:
        return [DEDICATED_EMAIL_ROLES[email]]
    return _get_allowed_roles(user)


def validate_role_selection(user: User, role: str) -> None:
    allowed = get_allowed_roles(user)
    if role not in allowed:
        raise ValueError(f"Role '{role}' is not permitted for this account")
