"""Runs a model over the written feedback and stores the result in ai_analysis.

Each written field is analysed on its own (never one blended text), and the
teacher's original words are only ever read, never written.
"""
from datetime import datetime

from .. import config

ANALYSED_FIELDS = ("positive_comment", "negative_comment", "evidence", "action_required")
# The field that explains the teacher's overall response, used for the
# teacher-vs-AI comparison on the dashboard. The others are shown individually.
PRIMARY_FIELD = "evidence"


def run_analysis(conn, model, batch_size: int = 16) -> dict:
    version = model.version
    todo = []
    for field in ANALYSED_FIELDS:
        rows = conn.execute(
            f"SELECT c.session_id, c.{field} AS text FROM comments c "
            f"WHERE c.{field} IS NOT NULL AND c.{field} <> '' AND NOT EXISTS ("
            f"  SELECT 1 FROM ai_analysis a WHERE a.session_id = c.session_id "
            f"  AND a.source_field = ? AND a.model_version = ?)", (field, version)).fetchall()
        todo += [(r["session_id"], field, r["text"]) for r in rows]
    done = flagged = 0
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        for (session_id, field, _), res in zip(chunk, model.predict([t[2] for t in chunk])):
            review = int(res.confidence < config.LOW_CONFIDENCE)
            conn.execute(
                "INSERT OR IGNORE INTO ai_analysis (session_id, source_field, sentiment, sentiment_confidence, "
                "model_name, model_version, needs_review, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, field, res.label, res.confidence, model.name, version, review,
                 datetime.now().isoformat(timespec="seconds")))
            done += 1
            flagged += review
    conn.commit()
    return {"analysed": done, "flagged_for_review": flagged, "model_version": version}
