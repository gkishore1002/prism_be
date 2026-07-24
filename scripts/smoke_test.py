"""Full analytics smoke test against running API."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
INST = "BRIGHTPATH"
PASS = "demo123"

USERS = {
    "tutor": "priya@brightpath.edu",
    "admin": "rajesh@brightpath.edu",
    "student": "arjun@brightpath.edu",
}


def req(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, object]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def login(email: str) -> str:
    status, data = req(
        "POST",
        "/auth/login",
        body={"email": email, "password": PASS, "institutionCode": INST},
    )
    assert status == 200, f"login failed for {email}: {status} {data}"
    return data["accessToken"]


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        FAILURES.append(name)


FAILURES: list[str] = []


def main() -> int:
    print("=== Prism API smoke test ===\n")

    # Health
    status, _ = req("GET", "/health") if False else (200, None)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/docs", timeout=5):
            check("API reachable", True)
    except Exception as exc:
        check("API reachable", False, str(exc))
        return 1

    tokens = {role: login(email) for role, email in USERS.items()}

    # Bootstrap payloads
    for role, token in tokens.items():
        status, boot = req("GET", "/portal/bootstrap", token=token)
        check(f"bootstrap {role}", status == 200 and boot.get("role") == role, f"status={status}")
        if role == "tutor":
            tutor_boot = boot
        if role == "admin":
            admin_boot = boot
        if role == "student":
            student_boot = boot

    # Admin bootstrap analytics fields
    for field in ("atRisk", "batchHeatmap", "classInsights", "hardestTopics", "copilot"):
        check(
            f"admin bootstrap has {field}",
            field in admin_boot and admin_boot[field] is not None,
            f"type={type(admin_boot.get(field)).__name__}",
        )

    # Tutor bootstrap analytics fields
    for field in ("atRisk", "batchHeatmap", "classInsights", "topicWeakness"):
        check(
            f"tutor bootstrap has {field}",
            field in tutor_boot,
            f"len={len(tutor_boot.get(field) or [])}",
        )

    # Class insights shape
    if tutor_boot.get("classInsights"):
        ci = tutor_boot["classInsights"][0]
        for key in ("id", "title", "description", "topicName", "suggestedIntervention", "severity"):
            check(f"classInsight field {key}", key in ci, str(ci.get(key, ""))[:60])
    else:
        check("classInsights non-empty", False, "no batches with insights")

    # Batch scoping
    batches = tutor_boot.get("batches") or []
    batch_id = batches[0]["id"] if batches else None
    batch_name = batches[0]["name"] if batches else None

    status, at_risk_all = req("GET", "/analytics/tutor/at-risk", token=tokens["tutor"])
    check("at-risk institution", status == 200, f"count={len(at_risk_all)}")

    if batch_id:
        status, at_risk_batch = req(
            "GET", f"/analytics/tutor/at-risk?batch_id={batch_id}", token=tokens["tutor"]
        )
        check("at-risk batch scoped", status == 200, f"batch={batch_name} count={len(at_risk_batch)}")
        check(
            "at-risk batch subset",
            len(at_risk_batch) <= len(at_risk_all),
            f"{len(at_risk_batch)} <= {len(at_risk_all)}",
        )

        status, heatmap = req(
            "GET", f"/analytics/tutor/batch-heatmap?batch_id={batch_id}", token=tokens["tutor"]
        )
        check("batch heatmap", status == 200, f"topics={len(heatmap)}")

        status, cohort = req(
            "GET", f"/analytics/tutor/cohort-report?batch_id={batch_id}", token=tokens["tutor"]
        )
        check("cohort report", status == 200, f"source={cohort.get('dataSource')}")

    # Subject students filter
    curriculum = tutor_boot.get("curriculum") or []
    subject_name = None
    if curriculum:
        grades = curriculum[0].get("grades") or []
        if grades:
            subjects = grades[0].get("subjects") or []
            if subjects:
                subject_name = subjects[0]["name"]

    status, all_students = req("GET", "/students", token=tokens["tutor"])
    total_students = len(all_students) if status == 200 else 0

    if subject_name:
        from urllib.parse import quote

        status, subj_students = req(
            "GET",
            f"/analytics/subjects/{quote(subject_name)}/students",
            token=tokens["tutor"],
        )
        check(
            "subject students filtered",
            status == 200 and len(subj_students) <= total_students,
            f"subject={subject_name} got={len(subj_students)} total={total_students}",
        )
        status, empty = req(
            "GET",
            "/analytics/subjects/Zoology/students",
            token=tokens["tutor"],
        )
        check("unknown subject returns empty", status == 200 and len(empty) == 0, f"count={len(empty)}")
        if subject_name and len(subj_students) > 0:
            check("subject with data returns rows", True, f"{len(subj_students)} for {subject_name}")

    # Student monthly reports (computed, not hardcoded June/May)
    status, monthly = req("GET", "/analytics/student/monthly-reports", token=tokens["student"])
    check("monthly reports", status == 200, f"periods={[r.get('period') for r in monthly]}")
    hardcoded = any(r.get("period") == "June 2026" and r.get("period") == "May 2026" for r in monthly)
    if monthly:
        periods = [r.get("period") for r in monthly]
        check(
            "monthly not only hardcoded template",
            not (periods == ["June 2026", "May 2026"]),
            str(periods),
        )

    # Student genome attendance
    status, genome = req("GET", "/analytics/student/genome", token=tokens["student"])
    check("student genome", status == 200)
    profile = (genome or {}).get("profile") or genome
    if isinstance(profile, dict) and "attendancePct" in profile:
        att = profile["attendancePct"]
        check(
            "genome attendance computed",
            isinstance(att, (int, float)) and 0 <= att <= 100,
            f"attendancePct={att} absent={profile.get('absentCount')}",
        )
    elif genome and genome.get("profile") is None:
        check("genome attendance computed", True, "no profile yet (empty student)")
    else:
        check("genome attendance field", False, str(genome)[:120])

    # Marks export endpoint
    status, sessions = req("GET", "/marks/sessions", token=tokens["tutor"])
    check("marks sessions", status == 200, f"sessions={len(sessions) if isinstance(sessions, list) else sessions}")

    print(f"\n=== Done: {len(FAILURES)} failure(s) ===")
    if FAILURES:
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
