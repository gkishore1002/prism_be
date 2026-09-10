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


def subjects_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    right_keys = {s.casefold() for s in right}
    return any(s.casefold() in right_keys for s in left)
