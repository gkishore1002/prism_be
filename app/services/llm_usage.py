"""Record and query daily LLM token usage. Failures never break Vertex calls."""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.llm_usage import LlmUsageDaily
from app.services.tenant_context import close_tenant_db

logger = logging.getLogger(__name__)

SERVICE_BOOK_OUTLINE = "book_outline"
SERVICE_TOPIC_MAP = "topic_map"
SERVICE_MCQ_GENERATION = "mcq_generation"
SERVICE_REPORT_SUMMARY = "report_summary"  # legacy / generic
SERVICE_ASSESSMENT_REPORT = "assessment_report"
SERVICE_STUDENT_REPORT = "student_report"
SERVICE_STUDENT_GENOME = "student_genome"
SERVICE_OTHER = "other"

SERVICE_LABELS = {
    SERVICE_BOOK_OUTLINE: "Book outline",
    SERVICE_TOPIC_MAP: "Topic mapping",
    SERVICE_MCQ_GENERATION: "AI MCQ generation",
    SERVICE_REPORT_SUMMARY: "Report summaries",
    SERVICE_ASSESSMENT_REPORT: "Assessment reports",
    SERVICE_STUDENT_REPORT: "Student overall reports",
    SERVICE_STUDENT_GENOME: "Learning Genome narratives",
    SERVICE_OTHER: "Other",
}

_current_institution_id: ContextVar[str | None] = ContextVar(
    "llm_usage_institution_id", default=None
)


def set_current_institution_id(institution_id: str | None) -> None:
    value = (institution_id or "").strip() or None
    _current_institution_id.set(value)


def get_current_institution_id() -> str | None:
    return _current_institution_id.get()


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_tokens_from_text(text: str) -> int:
    """Rough fallback when the provider does not return usage metadata (~4 chars/token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def extract_usage_from_response(response: Any) -> tuple[int, int, int] | None:
    """Parse Gemini/Vertex usage_metadata if present."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None and isinstance(response, dict):
        meta = response.get("usage_metadata") or response.get("usageMetadata")
    if meta is None:
        return None

    def _get(obj: Any, *names: str) -> int:
        for name in names:
            if isinstance(obj, dict):
                val = obj.get(name)
            else:
                val = getattr(obj, name, None)
            if val is not None:
                try:
                    return max(0, int(val))
                except (TypeError, ValueError):
                    continue
        return 0

    prompt = _get(meta, "prompt_token_count", "promptTokenCount", "input_tokens")
    completion = _get(
        meta,
        "candidates_token_count",
        "candidatesTokenCount",
        "output_tokens",
        "completion_token_count",
    )
    total = _get(meta, "total_token_count", "totalTokenCount")
    if total <= 0:
        total = prompt + completion
    if prompt <= 0 and completion <= 0 and total <= 0:
        return None
    if prompt <= 0 and total > 0:
        prompt = max(0, total - completion)
    if completion <= 0 and total > 0:
        completion = max(0, total - prompt)
    return prompt, completion, total or (prompt + completion)


def record_usage(
    *,
    institution_id: str | None = None,
    service: str = SERVICE_OTHER,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
) -> None:
    """Upsert today's usage row. Never raises to callers."""
    try:
        inst = (institution_id or get_current_institution_id() or "").strip()
        if not inst:
            return
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens if total_tokens is not None else prompt + completion))
        if total <= 0 and prompt <= 0 and completion <= 0:
            return
        svc = (service or SERVICE_OTHER).strip()[:64] or SERVICE_OTHER
        model_name = (model or settings.vertex_model or "unknown").strip()[:128] or "unknown"
        day = _today()

        db = SessionLocal()
        try:
            row = (
                db.execute(
                    select(LlmUsageDaily).where(
                        LlmUsageDaily.institution_id == inst,
                        LlmUsageDaily.usage_date == day,
                        LlmUsageDaily.service == svc,
                        LlmUsageDaily.model == model_name,
                    )
                )
                .scalars()
                .first()
            )
            if row:
                row.prompt_tokens = int(row.prompt_tokens or 0) + prompt
                row.completion_tokens = int(row.completion_tokens or 0) + completion
                row.total_tokens = int(row.total_tokens or 0) + total
                row.call_count = int(row.call_count or 0) + 1
                row.updated_at = _now_iso()
            else:
                db.add(
                    LlmUsageDaily(
                        id=f"lu-{uuid.uuid4().hex[:12]}",
                        institution_id=inst,
                        usage_date=day,
                        service=svc,
                        model=model_name,
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        total_tokens=total,
                        call_count=1,
                        updated_at=_now_iso(),
                    )
                )
            db.commit()
        finally:
            close_tenant_db(db)
    except Exception:  # noqa: BLE001
        logger.warning("llm_usage_record_failed", exc_info=True)


def record_from_response(
    response: Any,
    *,
    service: str,
    prompt_text: str = "",
    completion_text: str = "",
    institution_id: str | None = None,
    model: str | None = None,
) -> None:
    parsed = extract_usage_from_response(response)
    if parsed:
        prompt, completion, total = parsed
    else:
        prompt = estimate_tokens_from_text(prompt_text)
        completion = estimate_tokens_from_text(completion_text)
        total = prompt + completion
    record_usage(
        institution_id=institution_id,
        service=service,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def _resolve_range(start: date | None, end: date | None) -> tuple[date, date]:
    today = date.today()
    end_d = end or today
    start_d = start or (end_d - timedelta(days=29))
    if start_d > end_d:
        raise ValueError("start must be <= end")
    return start_d, end_d


def institution_usage(
    db: Session,
    institution_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    start_d, end_d = _resolve_range(start, end)
    start_s, end_s = start_d.isoformat(), end_d.isoformat()
    rows = (
        db.query(LlmUsageDaily)
        .filter(
            LlmUsageDaily.institution_id == institution_id,
            LlmUsageDaily.usage_date >= start_s,
            LlmUsageDaily.usage_date <= end_s,
        )
        .all()
    )

    totals = {
        "promptTokens": 0,
        "completionTokens": 0,
        "totalTokens": 0,
        "callCount": 0,
    }
    by_service: dict[str, dict] = {}
    by_day: dict[str, dict] = {}

    for row in rows:
        p, c, t, n = (
            int(row.prompt_tokens or 0),
            int(row.completion_tokens or 0),
            int(row.total_tokens or 0),
            int(row.call_count or 0),
        )
        totals["promptTokens"] += p
        totals["completionTokens"] += c
        totals["totalTokens"] += t
        totals["callCount"] += n

        svc = row.service or SERVICE_OTHER
        bucket = by_service.setdefault(
            svc,
            {
                "service": svc,
                "promptTokens": 0,
                "completionTokens": 0,
                "totalTokens": 0,
                "callCount": 0,
            },
        )
        bucket["promptTokens"] += p
        bucket["completionTokens"] += c
        bucket["totalTokens"] += t
        bucket["callCount"] += n

        day = row.usage_date
        day_bucket = by_day.setdefault(
            day,
            {
                "date": day,
                "promptTokens": 0,
                "completionTokens": 0,
                "totalTokens": 0,
                "callCount": 0,
            },
        )
        day_bucket["promptTokens"] += p
        day_bucket["completionTokens"] += c
        day_bucket["totalTokens"] += t
        day_bucket["callCount"] += n

    return {
        "institutionId": institution_id,
        "range": {"start": start_s, "end": end_s},
        "totals": totals,
        "byService": sorted(by_service.values(), key=lambda x: x["totalTokens"], reverse=True),
        "byDay": sorted(by_day.values(), key=lambda x: x["date"]),
    }
