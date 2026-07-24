"""Vertex AI (Gemini) summaries for student reports."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, str]] = {}
_client = None


def _cache_get(key: str) -> str | None:
    if settings.vertex_summary_cache_ttl <= 0:
        return None
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.monotonic() > expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: str) -> None:
    if settings.vertex_summary_cache_ttl <= 0:
        return
    _CACHE[key] = (time.monotonic() + settings.vertex_summary_cache_ttl, value)


def _cache_key(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{kind}:{digest}"


def _resolve_project() -> str:
    project = settings.google_cloud_project.strip()
    if project:
        return project
    import os

    gcloud = "gcloud.cmd" if os.name == "nt" else "gcloud"
    try:
        result = subprocess.run(
            [gcloud, "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=False,
            shell=os.name == "nt",
        )
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def _resolve_credentials():
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    from google.oauth2.credentials import Credentials
    import os

    try:
        credentials, _ = google.auth.default()
        return credentials
    except DefaultCredentialsError:
        pass

    gcloud = "gcloud.cmd" if os.name == "nt" else "gcloud"
    try:
        result = subprocess.run(
            [gcloud, "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=False,
            shell=os.name == "nt",
        )
    except FileNotFoundError:
        return None

    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        return None
    return Credentials(token=token)


def _get_client():
    global _client
    if _client is not None:
        return _client

    from google import genai

    project = _resolve_project()
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not configured")

    credentials = _resolve_credentials()
    if credentials is None:
        raise RuntimeError("Google Cloud credentials not available")

    _client = genai.Client(
        vertexai=True,
        project=project,
        location=settings.google_cloud_location,
        credentials=credentials,
    )
    return _client


def _generate(prompt: str, *, cache_key: str) -> str | None:
    if not settings.vertex_enabled:
        return None

    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        from google.genai import types

        client = _get_client()
        response = client.models.generate_content(
            model=settings.vertex_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=512,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return None
        _cache_set(cache_key, text)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vertex summary generation failed: %s", exc)
        return None


def generate_assessment_report_summary(context: dict[str, Any]) -> str | None:
    """Assessment-wise summary — persisted in DB when first generated."""
    key = _cache_key("assessment_report", context)
    prompt = f"""You are an academic advisor writing a short report for one completed assessment.

Assessment result data (JSON):
{json.dumps(context, indent=2, default=str)}

Write 2–3 sentences covering: score on this assessment, performance vs class average if available, one strength, and one focus area from weak topics. Be specific to THIS assessment only — do not discuss overall career or unrelated subjects. Plain text only."""
    return _generate(prompt, cache_key=key)


def generate_student_report_summary(context: dict[str, Any]) -> str | None:
    """2–3 sentence executive summary for StudentWiseReport."""
    key = _cache_key("student_report", context)
    prompt = f"""You are an academic advisor writing a concise executive summary for a student's progress report.

Student data (JSON):
{json.dumps(context, indent=2, default=str)}

Write 2–3 sentences in plain English for parents and tutors. Cover overall performance across all subjects and assessments to date, trends, and top priorities. Be encouraging but honest. Do not use bullet points or markdown."""
    return _generate(prompt, cache_key=key)


def generate_student_genome_narrative(context: dict[str, Any]) -> str | None:
    """Multi-paragraph Learning Genome narrative for tutor/admin/student reports."""
    key = _cache_key("student_genome", context)
    prompt = f"""You are an educational data analyst writing a Learning Genome narrative for a student.

Student genome metrics (JSON):
{json.dumps(context, indent=2, default=str)}

Write 3–4 short paragraphs covering: overall standing and rank, subject strengths and weaknesses, trend and consistency, recovery/resilience, and projected next performance. Use warm, professional language suitable for tutors and parents. Plain text only — no bullet points or markdown."""
    return _generate(prompt, cache_key=key)
