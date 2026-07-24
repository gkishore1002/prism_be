"""Role resolution and validation for multi-role accounts."""

from app.models.user import User

DEDICATED_EMAIL_ROLES: dict[str, str] = {
    "arjun@brightpath.edu": "student",
    "priya@brightpath.edu": "tutor",
    "rajesh@brightpath.edu": "admin",
}

MULTI_ROLE_EMAIL = "demo@prism.app"
ALL_ROLES = ("student", "tutor", "admin")


def get_allowed_roles(user: User) -> list[str]:
    email = user.email.strip().lower()
    if email == MULTI_ROLE_EMAIL:
        if user.roles:
            parsed = [r.strip() for r in user.roles.split(",") if r.strip()]
            return parsed if parsed else list(ALL_ROLES)
        return list(ALL_ROLES)
    if email in DEDICATED_EMAIL_ROLES:
        return [DEDICATED_EMAIL_ROLES[email]]
    if user.roles:
        parsed = [r.strip() for r in user.roles.split(",") if r.strip()]
        if parsed:
            return parsed
    return [user.role]


def validate_role_selection(user: User, role: str) -> None:
    allowed = get_allowed_roles(user)
    if role not in allowed:
        raise ValueError(f"Role '{role}' is not permitted for this account")
