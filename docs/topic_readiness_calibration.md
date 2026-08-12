# Topic predictive readiness — calibration notes

## What v1 predicts

For each student × topic, Prism forecasts a **likely exam % if assessed soon**
(`predictedScore`), distinct from historical mastery (`currentMastery`).

Formula (see `app/services/topic_readiness.py`):

1. Weighted evidence: marks × difficulty × recency decay (half-life 45 days)
2. Velocity: recent vs prior attempt means (clamped ±20)
3. Staleness penalty if last attempt > 45 days ago (up to 8 pts)
4. Confidence bands from attempt count + recency

Subject readiness rolls up as a weighted mean of topic predictions
(topic weight × attempt count).

## How to calibrate (phase 2)

Hold out the next assessment that covers a topic and compare:

| Metric | Definition |
|--------|------------|
| Error | `predictedScore − actualTopicPct` on next exam for that topic |
| MAE | Mean absolute error across student×topic |
| Bias | Mean signed error (positive = over-optimistic) |

Suggested SQL / notebook steps:

1. Snapshot `predictedScore` the day before a scheduled assessment.
2. After submit, compute topic % from that submission’s answers only
   (same correctness mapping as `_collect_student_attempts`).
3. Join on `(student_id, topic_id)` and compute MAE / bias by confidence band.
4. Tune in order: half-life → velocity coefficient (0.5) → decay penalty →
   difficulty factors. Prefer changes that reduce MAE without raising bias
   on `low` confidence rows (those should stay conservative).

Do **not** train ML until you have enough labeled outcomes
(hundreds of student×topic next-exam pairs). Until then, keep the rule engine
and adjust constants from calibration reports.

## API surfaces

- `GET /analytics/student/topic-readiness` — full topic forecasts
- `GET /analytics/student/topic-breakdown` — same fields (top topics)
- `GET /analytics/student/readiness` — subject rollup from topics
