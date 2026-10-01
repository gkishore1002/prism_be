"""Subject name wiring for reports / genome / marks matching."""

from app.services.cohort_report import _subject_code
from app.services.subjects_list import (
    canonicalize_subject_name,
    subject_names_match,
    subjects_from_stored,
    subjects_label,
)


def test_canonicalize_common_aliases():
    assert canonicalize_subject_name("maths") == "Mathematics"
    assert canonicalize_subject_name("Math") == "Mathematics"
    assert canonicalize_subject_name("social studies") == "Social Science"
    assert canonicalize_subject_name("Physics") == "Physics"


def test_subject_code_mapping():
    assert _subject_code("Mathematics") == "MAT"
    assert _subject_code("Maths") == "MAT"
    assert _subject_code("Physics") == "SCI"
    assert _subject_code("Chemistry") == "SCI"
    assert _subject_code("Social Science") == "SOC"
    assert _subject_code("Social Studies") == "SOC"
    assert _subject_code("Hindi") == "OTH"


def test_subject_names_match_avoids_social_vs_science():
    assert subject_names_match("Mathematics", "Maths")
    assert subject_names_match("Physics", "physics")
    assert not subject_names_match("Social Science", "Science")


def test_subjects_label_multi():
    subjects = subjects_from_stored("Physics", '["Physics","Chemistry"]')
    assert subjects == ["Physics", "Chemistry"]
    assert subjects_label(subjects) == "Physics · Chemistry"
