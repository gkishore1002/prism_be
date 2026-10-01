"""Normalize multi-subject lists for batches, papers, and assessments."""

from __future__ import annotations

from app.utils import from_json_list, to_json_list


def normalize_subjects(*values: str | list[str] | None) -> list[str]:
    """Dedupe subject names while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        items: list[str]
        if value is None:
            continue
        if isinstance(value, list):
            items = value
        else:
            # Support legacy "Physics · Chemistry" labels if present.
            raw = value.strip()
            if not raw:
                continue
            if " · " in raw:
                items = [part.strip() for part in raw.split(" · ")]
            elif " | " in raw:
                items = [part.strip() for part in raw.split(" | ")]
            else:
                items = [raw]
        for item in items:
            name = (item or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
    return out


def subjects_json(subjects: list[str]) -> str:
    return to_json_list(normalize_subjects(subjects))


def subjects_from_stored(subject: str | None, subjects_raw: str | None) -> list[str]:
    stored = from_json_list(subjects_raw) if subjects_raw else []
    return normalize_subjects(stored, subject)


def primary_subject(subjects: list[str], fallback: str = "") -> str:
    return subjects[0] if subjects else fallback


def subjects_label(subjects: list[str], *, empty: str = "") -> str:
    """Human-readable multi-subject label (matches FE formatSubjects)."""
    return " · ".join(subjects) if subjects else empty


# Common aliases so marks/curriculum/assessment names resolve to the same subject.
_SUBJECT_ALIASES: dict[str, str] = {
    "math": "Mathematics",
    "maths": "Mathematics",
    "mathematics": "Mathematics",
    "eng": "English",
    "english": "English",
    "tam": "Tamil",
    "tamil": "Tamil",
    "sci": "Science",
    "science": "Science",
    "phy": "Physics",
    "physics": "Physics",
    "chem": "Chemistry",
    "chemistry": "Chemistry",
    "bio": "Biology",
    "biology": "Biology",
    "soc": "Social Science",
    "social": "Social Science",
    "social science": "Social Science",
    "social studies": "Social Science",
    "sst": "Social Science",
    "evs": "Environmental Science",
    "cs": "Computer Science",
    "computer": "Computer Science",
    "computer science": "Computer Science",
    "it": "Information Technology",
}


def canonicalize_subject_name(name: str | None) -> str:
    """Normalize free-text subject labels for consistent report keys."""
    raw = (name or "").strip()
    if not raw:
        return ""
    key = raw.casefold()
    if key in _SUBJECT_ALIASES:
        return _SUBJECT_ALIASES[key]
    # Word-aware soft match — avoid "sci" matching inside "social science".
    tokens = {part.strip() for part in key.replace("/", " ").replace("-", " ").split() if part.strip()}
    for alias, canonical in _SUBJECT_ALIASES.items():
        if " " in alias:
            if alias in key:
                return canonical
            continue
        if alias in tokens:
            return canonical
    return raw


def subjects_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    right_keys = {canonicalize_subject_name(s).casefold() for s in right}
    return any(canonicalize_subject_name(s).casefold() in right_keys for s in left)


def subject_names_match(candidate: str | None, query: str | None) -> bool:
    """True when two subject labels refer to the same subject (aliases included)."""
    c = canonicalize_subject_name(candidate).casefold()
    q = canonicalize_subject_name(query).casefold()
    if not c or not q:
        return False
    if c == q:
        return True
    c_tokens = set(c.replace("/", " ").replace("-", " ").split())
    q_tokens = set(q.replace("/", " ").replace("-", " ").split())
    if not c_tokens or not q_tokens:
        return False
    # Exact token-set equality (order-insensitive).
    if c_tokens == q_tokens:
        return True
    # "Mathematics Advanced" contains "Mathematics" as full token set subset,
    # but do not treat "Science" as matching "Social Science".
    if c_tokens < q_tokens or q_tokens < c_tokens:
        shorter = c if len(c_tokens) < len(q_tokens) else q
        longer = q if shorter is c else c
        if "social" in longer.split() and shorter in {"science", "sci"}:
            return False
        return True
    return False
