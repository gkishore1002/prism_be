from app.services.syllabus_books import subjects_match
from app.services.vertex_summary import _heuristic_topic_map, parse_llm_json
import app.services.vertex_summary as vertex_summary


def test_parse_llm_json_strips_fences():
    raw = """```json
{"chapters":[{"title":"Algebra","topics":["Linear Equations"]}]}
```"""
    data = parse_llm_json(raw)
    assert data["chapters"][0]["title"] == "Algebra"


def test_heuristic_topic_map_matches_chapter_keywords():
    outline = {
        "chapters": [
            {"title": "Algebra", "topics": ["Linear Equations", "Polynomials"]},
            {"title": "Geometry", "topics": ["Triangles", "Circles"]},
        ]
    }
    questions = [
        {"row": 2, "chapter": "Algebra", "text": "Solve the linear equation 2x + 3 = 7", "topic": ""},
        {"row": 3, "chapter": "Geometry", "text": "Find the area of a circle", "topic": ""},
    ]
    mapped = _heuristic_topic_map(outline, questions)
    by_row = {item["row"]: item["topic"] for item in mapped}
    assert by_row[2] == "Linear Equations"
    assert by_row[3] == "Circles"


def test_get_client_raises_when_credentials_missing(monkeypatch):
    monkeypatch.setattr(vertex_summary, "_api_key", lambda: "")
    monkeypatch.setattr(vertex_summary, "_resolve_project", lambda: "demo-project")
    monkeypatch.setattr(vertex_summary, "_resolve_credentials", lambda: None)
    vertex_summary._client = None
    try:
        import pytest

        with pytest.raises(RuntimeError, match="credentials"):
            vertex_summary._get_client()
    finally:
        vertex_summary._client = None


def test_knowledge_summary_lists_strong_and_weak_topics():
    from app.services.cohort_report import _knowledge_summary

    text = _knowledge_summary(
        [
            {"concept": "Loops", "subject": "Python", "masteryPct": 82},
            {"concept": "Recursion", "subject": "Python", "masteryPct": 41},
        ]
    )
    assert "averages 62%" in text
    assert "Loops" in text
    assert "Recursion" in text


def test_subjects_match_ignores_case_and_partial_names():
    assert subjects_match("Python Programming", "python programming")
    assert subjects_match("Python", "Python Programming")
    assert subjects_match("Python Programming", "Python")
    assert not subjects_match("Computer Science", "Python Programming")
    assert not subjects_match("Art", "Earth Science")

