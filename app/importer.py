"""CSV import. Idempotent (source_hash), never guesses a school, never edits teacher text."""
import csv
import io
import json
import sqlite3
from datetime import datetime

from . import normalise as N
from .school_match import match_school, norm


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _get_teacher(conn, name):
    if not name:
        return None
    conn.execute("INSERT OR IGNORE INTO teachers (name) VALUES (?)", (name,))
    return conn.execute("SELECT teacher_id FROM teachers WHERE name = ?", (name,)).fetchone()[0]


def _link_group(conn, school_id, rec):
    if rec["year_min"] is None:
        return None
    for g in conn.execute("SELECT * FROM school_club_groups WHERE school_id = ?", (school_id,)):
        if (g["year_min"], g["year_max"]) == (rec["year_min"], rec["year_max"]):
            return g["group_id"]
    return None


def insert_session(conn, rec, school_id, method, needs_review, batch_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (school_id, club_group_id, teacher_id, session_date, week_start, "
        "programme_week, session_number, activity, year_group_label, students_present, "
        "completion_status, completion_raw, incomplete_reason, technical_status, "
        "technical_description, submitted_at, school_name_raw, school_match_method, "
        "needs_school_review, source_hash, batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (school_id, _link_group(conn, school_id, rec), _get_teacher(conn, rec["teacher"]),
         rec["session_date"], rec["week_start"], rec["programme_week"], rec["session_number"],
         rec["activity"], rec["year_group_label"], rec["students_present"],
         rec["completion_status"], rec["completion_raw"], rec["incomplete_reason"],
         rec["technical_status"], rec["technical_description"], rec["submitted_at"],
         rec["school_text"], method, int(needs_review), rec["source_hash"], batch_id))
    sid = cur.lastrowid
    conn.execute(
        "INSERT INTO feedback (session_id, engagement, enjoyment, understanding, difficulty, pace, "
        "activity_quality, teacher_confidence, student_response_raw, teacher_sentiment, session_outcome) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, rec["engagement"], rec["enjoyment"], rec["understanding"], rec["difficulty"], rec["pace"],
         rec["activity_quality"], rec["teacher_confidence"], rec["student_response_raw"],
         rec["teacher_sentiment"], rec["session_outcome"]))
    conn.execute(
        "INSERT INTO comments (session_id, positive_comment, negative_comment, evidence, "
        "teacher_reflection, action_category, action_required, priority, action_status, sensitivity_flag) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, rec["positive_comment"], rec["negative_comment"], rec["evidence"],
         rec["teacher_reflection"], rec["action_category"], rec["action_required"], rec["priority"],
         rec["action_status"], rec["sensitivity_flag"]))
    for polarity, labels in (("positive", rec["positive_tags"]), ("negative", rec["negative_tags"])):
        for label in labels:
            conn.execute("INSERT OR IGNORE INTO themes (label, polarity) VALUES (?,?)", (label, polarity))
            tid = conn.execute("SELECT theme_id FROM themes WHERE label=? AND polarity=?",
                               (label, polarity)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO session_tags (session_id, theme_id, source) "
                         "VALUES (?,?, 'form')", (sid, tid))
    return sid


def _other_text(raw):
    skip = {"school", "submitted_at", "teacher"}
    return [v for k, v in raw.items() if k not in skip and isinstance(v, str)]


def import_csv_text(conn: sqlite3.Connection, text: str, filename: str = "upload.csv") -> dict:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        return {"total": 0, "imported": 0, "duplicates": 0, "pending": 0, "errors": ["File is empty."]}
    header_map = N.map_headers(rows[0])
    errors = []
    if "school" not in header_map.values():
        errors.append("No school column found; every row will need a school assigned by hand.")
    batch_id = conn.execute(
        "INSERT INTO import_batches (filename, imported_at) VALUES (?,?)",
        (filename, _now())).lastrowid
    stats = {"total": 0, "imported": 0, "duplicates": 0, "pending": 0, "errors": errors, "batch_id": batch_id}
    for line_no, row in enumerate(rows[1:], start=2):
        if not any((c or "").strip() for c in row):
            continue
        stats["total"] += 1
        raw = N.row_to_raw(header_map, row)
        rec = N.build_record(raw)
        if "error" in rec:
            errors.append(f"Row {line_no}: {rec['error']}")
            continue
        h = rec["source_hash"]
        if conn.execute("SELECT 1 FROM sessions WHERE source_hash=?", (h,)).fetchone() or \
           conn.execute("SELECT 1 FROM pending_feedback WHERE source_hash=?", (h,)).fetchone():
            stats["duplicates"] += 1
            continue
        m = match_school(conn, rec["school_text"], _other_text(raw))
        if m.school_id:
            insert_session(conn, rec, m.school_id, m.method, m.needs_review, batch_id)
            stats["imported"] += 1
        else:
            reason = ("Several schools match: " + ", ".join(m.candidates)) if m.method == "ambiguous" \
                else "No school recognised"
            conn.execute("INSERT INTO pending_feedback (batch_id, source_hash, school_text, reason, "
                         "raw_json, created_at) VALUES (?,?,?,?,?,?)",
                         (batch_id, h, rec["school_text"], reason, json.dumps(raw), _now()))
            stats["pending"] += 1
    conn.execute("UPDATE import_batches SET rows_total=?, rows_imported=?, rows_duplicate=?, rows_pending=? "
                 "WHERE batch_id=?", (stats["total"], stats["imported"], stats["duplicates"],
                                      stats["pending"], batch_id))
    conn.commit()
    return stats


def import_csv_file(conn, path) -> dict:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return import_csv_text(conn, fh.read(), filename=str(path).split("/")[-1])


def resolve_pending(conn, pending_id: int, school_id: str, learn_alias: bool = True) -> int:
    """Assign a school to a waiting row, import it, and remember the wording as an alias."""
    p = conn.execute("SELECT * FROM pending_feedback WHERE pending_id=? AND resolved=0",
                     (pending_id,)).fetchone()
    if not p:
        raise ValueError("Pending row not found")
    raw = json.loads(p["raw_json"])
    rec = N.build_record(raw)
    session_id = insert_session(conn, rec, school_id, "manual", False, p["batch_id"])
    conn.execute("UPDATE pending_feedback SET resolved=1 WHERE pending_id=?", (pending_id,))
    alias = norm(p["school_text"])
    if learn_alias and alias:
        conn.execute("INSERT OR IGNORE INTO school_aliases (alias, school_id, source) VALUES (?,?, 'learned')",
                     (alias, school_id))
        # other waiting rows with the same wording can now be matched automatically
        for other in conn.execute("SELECT pending_id, school_text FROM pending_feedback "
                                  "WHERE resolved=0 AND pending_id<>?", (pending_id,)).fetchall():
            if norm(other["school_text"]) == alias:
                resolve_pending(conn, other["pending_id"], school_id, learn_alias=False)
    conn.commit()
    return session_id


def create_school(conn, name: str, aliases=()) -> str:
    n = conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0]
    existing = {r[0] for r in conn.execute("SELECT school_id FROM schools")}
    i = n + 1
    while f"SCH{i:03d}" in existing:
        i += 1
    sid = f"SCH{i:03d}"
    conn.execute("INSERT INTO schools (school_id, name) VALUES (?,?)", (sid, name.strip()))
    for kw in {norm(name), *(norm(a) for a in aliases)}:
        if kw:
            conn.execute("INSERT OR IGNORE INTO school_aliases (alias, school_id, source) "
                         "VALUES (?,?, 'manual')", (kw, sid))
    conn.commit()
    return sid
