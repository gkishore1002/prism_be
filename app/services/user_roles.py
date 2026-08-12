"""Multi-role account helpers — one user can hold admin, tutor, and student roles."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query

from app.models.user import User

ROLE_ORDER = ("admin", "tutor", "student")
ALL_APP_ROLES = frozenset(ROLE_ORDER)


def parse_roles(user: User) -> list[str]:
    if user.roles:
        parsed = [r.strip() for r in user.roles.split(",") if r.strip() in ALL_APP_ROLES]
        if parsed:
            # preserve order, dedupe
            seen: set[str] = set()
            ordered: list[str] = []
            for role in parsed:
                if role not in seen:
                    seen.add(role)
                    ordered.append(role)
            return ordered
    if user.role in ALL_APP_ROLES:
        return [user.role]
    return [user.role] if user.role else []


def primary_role(roles: list[str]) -> str:
    for role in ROLE_ORDER:
        if role in roles:
            return role
    return roles[0]


def has_role(user: User, role: str) -> bool:
    return role in parse_roles(user)


def add_role(user: User, role: str) -> list[str]:
    if role not in ALL_APP_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    roles = parse_roles(user)
    if role not in roles:
        roles.append(role)
    user.roles = ",".join(roles)
    user.role = primary_role(roles)
    return roles


def remove_role(user: User, role: str) -> list[str]:
    if role not in ALL_APP_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    roles = parse_roles(user)
    if role not in roles:
        return roles
    roles = [r for r in roles if r != role]
    if not roles:
        raise ValueError("Staff must keep at least one role")
    user.roles = ",".join(roles)
    user.role = primary_role(roles)
    return roles


def set_roles(user: User, roles: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for role in roles:
        if role not in ALL_APP_ROLES or role in seen:
            continue
        seen.add(role)
        normalized.append(role)
    if not normalized:
        raise ValueError("Staff must have at least one role")
    user.roles = ",".join(normalized)
    user.role = primary_role(normalized)
    return normalized


def get_allowed_roles(user: User) -> list[str]:
    """Roles this account may select at login."""
    return parse_roles(user)


def role_filter(role: str):
    """SQLAlchemy filter — users whose account includes a role."""
    return or_(
        User.role == role,
        User.roles == role,
        User.roles.like(f"{role},%"),
        User.roles.like(f"%,{role},%"),
        User.roles.like(f"%,{role}"),
    )


def filter_users_with_role(query: Query, role: str) -> Query:
    return query.filter(role_filter(role))


def is_admin_account(user: User) -> bool:
    return has_role(user, "admin")


def is_tutor_account(user: User) -> bool:
    return has_role(user, "tutor")
