"""Vertex AI (Gemini) summaries for student reports."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable

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


_CREDENTIALS_HINT = (
    "Google Cloud credentials not available. "
    "In Docker, mount ADC from `gcloud auth application-default login` "
    "(HOST_GCP_CREDENTIALS) or set GEMINI_API_KEY."
)


def _api_key() -> str:
    import os

    return (
        (settings.google_api_key or "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def _credential_file_candidates() -> list[str]:
    import os

    return [
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip(),
        "/gcp/adc.json",
        os.path.expanduser("~/.config/gcloud/application_default_credentials.json"),
    ]


def _resolve_credentials():
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    from google.oauth2.credentials import Credentials
    import os

    for path in _credential_file_candidates():
        if not path or not os.path.isfile(path):
            continue
        try:
            credentials, _ = google.auth.load_credentials_from_file(path)
            return credentials
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load Google credentials from %s: %s", path, exc)

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

    api_key = _api_key()
    if api_key:
        _client = genai.Client(api_key=api_key)
        return _client

    project = _resolve_project()
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not configured")

    credentials = _resolve_credentials()
    if credentials is None:
        raise RuntimeError(_CREDENTIALS_HINT)

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

    def _call_vertex() -> str | None:
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

    timeout = max(1, settings.vertex_request_timeout_seconds)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_call_vertex).result(timeout=timeout)
    except FuturesTimeoutError:
        logger.warning("Vertex summary timed out after %ss (cache_key=%s)", timeout, cache_key)
        return None
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vertex summary generation failed: %s", exc)
        return None


def generate_pair_parallel(
    en_fn: Callable[[dict[str, Any]], str | None],
    ta_fn: Callable[[dict[str, Any]], str | None],
    context: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Run English and Tamil Vertex generators in parallel; never raise on timeout."""
    if not settings.vertex_enabled:
        return None, None

    timeout = max(1, settings.vertex_request_timeout_seconds) + 4

    with ThreadPoolExecutor(max_workers=2) as pool:
        en_future = pool.submit(en_fn, context)
        ta_future = pool.submit(ta_fn, context)
        en: str | None = None
        ta: str | None = None
        try:
            en = en_future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("Vertex English summary timed out after %ss", timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vertex English summary failed: %s", exc)
        try:
            ta = ta_future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("Vertex Tamil summary timed out after %ss", timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vertex Tamil summary failed: %s", exc)
    return en, ta


def generate_assessment_report_summary(context: dict[str, Any]) -> str | None:
    """Assessment-wise summary — persisted in DB when first generated."""
    key = _cache_key("assessment_report", context)
    prompt = f"""You are an academic advisor writing a short report for one completed assessment.

Assessment result data (JSON):
{json.dumps(context, indent=2, default=str)}

Write 2–3 sentences covering: score on this assessment, performance vs class average if available, one strength, and one focus area from weak topics. Be specific to THIS assessment only — do not discuss overall career or unrelated subjects. Plain text only."""
    return _generate(prompt, cache_key=key)


def generate_assessment_report_summary_ta(context: dict[str, Any]) -> str | None:
    """Tamil assessment summary — persisted alongside English."""
    key = _cache_key("assessment_report_ta", context)
    prompt = f"""You are an academic advisor writing a short report in Tamil (Tamil script only) for one completed assessment.

Assessment result data (JSON):
{json.dumps(context, indent=2, default=str)}

Write 2–3 sentences in Tamil covering: score on this assessment, performance vs class average if available, one strength, and one focus area. Plain Tamil text only — no English, no markdown."""
    return _generate(prompt, cache_key=key)


def generate_student_report_summary_ta(context: dict[str, Any]) -> str | None:
    """Tamil executive summary for overall performance report."""
    key = _cache_key("student_report_ta", context)
    prompt = f"""You are an academic advisor writing a concise executive summary in Tamil (Tamil script only) for a student's progress report.

Student data (JSON):
{json.dumps(context, indent=2, default=str)}

Write 2–3 sentences in Tamil for parents and tutors. Cover overall performance, trends, and top priorities. Plain Tamil only — no English, no bullet points, no markdown."""
    return _generate(prompt, cache_key=key)


def generate_student_genome_narrative_ta(context: dict[str, Any]) -> str | None:
    """Tamil Learning Genome narrative."""
    key = _cache_key("student_genome_ta", context)
    prompt = f"""You are an educational data analyst writing a Learning Genome narrative in Tamil (Tamil script only) for a student.

Student genome metrics (JSON):
{json.dumps(context, indent=2, default=str)}

Write 3–4 short paragraphs in Tamil covering: overall standing, subject strengths and weaknesses, trend, and projected performance. Plain Tamil only — no English, no bullet points, no markdown."""
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


def parse_llm_json(text: str) -> Any:
    """Parse JSON from a model response, including fenced markdown."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _generate_flexible(
    contents: Any,
    *,
    timeout: int,
    max_output_tokens: int,
    temperature: float,
    json_mode: bool,
) -> str | None:
    if not settings.vertex_enabled:
        return None

    def _call_vertex() -> str | None:
        from google.genai import types

        client = _get_client()
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
        }
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        response = client.models.generate_content(
            model=settings.vertex_model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = (response.text or "").strip()
        return text or None

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_call_vertex).result(timeout=timeout)
    except FuturesTimeoutError:
        logger.warning("Vertex JSON generation timed out after %ss", timeout)
        return None
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vertex JSON generation failed: %s", exc)
        return None


def extract_book_outline(
    *,
    subject_name: str,
    pdf_bytes: bytes | None = None,
    pdf_text: str = "",
) -> dict[str, Any]:
    """Return {chapters: [{title, topics: [str]}]} from a textbook via Vertex."""
    subject_hint = f' for the subject "{subject_name}"' if subject_name else ""
    instructions = (
        f"You are building a syllabus outline{subject_hint} from a textbook.\n"
        "Identify the book's CHAPTERS in reading order, and for each chapter its main "
        "TOPICS (the sections / sub-headings a teacher would track through the term).\n"
        "Rules:\n"
        "- Use the book's ACTUAL structure / table of contents. Do NOT invent content.\n"
        "- Include EVERY chapter in the book, from the first to the last.\n"
        "- Concise chapter and topic titles: no leading numbers, no page numbers.\n"
        "- Typically 3-12 topics per chapter; skip front-matter, preface, index, glossary "
        "and answer keys.\n"
        "- Return STRICT JSON only, in exactly this shape:\n"
        '{"chapters":[{"title":"Chapter title","topics":["Topic one","Topic two"]}]}'
    )
    timeout = max(30, settings.vertex_book_timeout_seconds)
    response_text: str | None = None
    if pdf_bytes:
        try:
            from google.genai import types

            contents = [
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                instructions,
            ]
            response_text = _generate_flexible(
                contents,
                timeout=timeout,
                max_output_tokens=8192,
                temperature=0.1,
                json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("book_outline_multimodal_failed_fallback_text: %s", exc)
            response_text = None

    if not response_text:
        if not (pdf_text or "").strip():
            raise RuntimeError("No book content available for outline extraction")
        clipped = pdf_text[:3_000_000]
        prompt = f"{instructions}\n\n<BOOK TEXT>\n{clipped}\n</BOOK TEXT>"
        response_text = _generate_flexible(
            prompt,
            timeout=timeout,
            max_output_tokens=8192,
            temperature=0.1,
            json_mode=True,
        )
    if not response_text:
        raise RuntimeError("Vertex AI did not return a book outline")

    result = parse_llm_json(response_text)
    if isinstance(result, list):
        result = {"chapters": result}
    if not isinstance(result, dict) or not isinstance(result.get("chapters"), list):
        raise RuntimeError("Book outline was not valid JSON")

    chapters: list[dict[str, Any]] = []
    for ch in result["chapters"]:
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip()
        if not title:
            continue
        topics: list[str] = []
        for t in ch.get("topics") or []:
            tt = (t.get("title") if isinstance(t, dict) else t) or ""
            tt = str(tt).strip()
            if tt:
                topics.append(tt)
        chapters.append({"title": title, "topics": topics})
    if not chapters:
        raise RuntimeError("No chapters could be extracted from the document")
    return {"chapters": chapters}


def map_question_topics(
    outline: dict[str, Any],
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map questions to syllabus topics. Returns [{row, topic, chapter}]."""
    if not questions:
        return []
    prompt = f"""You assign exam questions to syllabus topics.

SYLLABUS OUTLINE (JSON):
{json.dumps(outline, indent=2, default=str)}

QUESTIONS (JSON):
{json.dumps(questions, indent=2, default=str)}

Rules:
- For each question, pick the closest chapter in the outline (use the given chapter name as a hint).
- Then pick ONE topic from that chapter's topics list.
- Only use topic titles that appear in the outline. Do not invent topics.
- If the question already has a topic that matches the outline, keep it.
- Return STRICT JSON only:
{{"mappings":[{{"row":1,"chapter":"Chapter title","topic":"Topic title"}}]}}
"""
    timeout = max(20, settings.vertex_topic_map_timeout_seconds)
    response_text = _generate_flexible(
        prompt,
        timeout=timeout,
        max_output_tokens=4096,
        temperature=0.1,
        json_mode=True,
    )
    if not response_text:
        return _heuristic_topic_map(outline, questions)
    try:
        result = parse_llm_json(response_text)
    except json.JSONDecodeError:
        return _heuristic_topic_map(outline, questions)
    raw = result.get("mappings") if isinstance(result, dict) else result
    if not isinstance(raw, list):
        return _heuristic_topic_map(outline, questions)
    mappings: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = item.get("row")
        topic = str(item.get("topic") or "").strip()
        chapter = str(item.get("chapter") or "").strip()
        if row is None or not topic:
            continue
        mappings.append({"row": int(row), "topic": topic, "chapter": chapter})
    return mappings or _heuristic_topic_map(outline, questions)


def _heuristic_topic_map(
    outline: dict[str, Any], questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fallback when Vertex is unavailable: match chapter, then keyword-overlap topic."""
    chapters = outline.get("chapters") or []
    mappings: list[dict[str, Any]] = []
    for q in questions:
        q_chapter = str(q.get("chapter") or "").strip().lower()
        q_text = str(q.get("text") or "").lower()
        matched = None
        for ch in chapters:
            title = str(ch.get("title") or "")
            if q_chapter and q_chapter in title.lower():
                matched = ch
                break
        if matched is None and chapters:
            matched = chapters[0]
        if not matched:
            continue
        topics = [str(t).strip() for t in (matched.get("topics") or []) if str(t).strip()]
        chosen = topics[0] if topics else str(matched.get("title") or "").strip()
        best_score = -1
        for topic in topics:
            score = sum(1 for word in topic.lower().split() if len(word) > 3 and word in q_text)
            if score > best_score:
                best_score = score
                chosen = topic
        existing = str(q.get("topic") or "").strip()
        mappings.append(
            {
                "row": int(q.get("row") or 0),
                "topic": existing or chosen,
                "chapter": str(matched.get("title") or q.get("chapter") or ""),
            }
        )
    return mappings

