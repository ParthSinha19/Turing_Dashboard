"""All dashboard numbers come from here: plain, deterministic database lookups."""
import json
from datetime import date, timedelta
from statistics import mean

from . import config
from .normalise import monday_of

RATINGS = [("engagement", "Engagement"), ("enjoyment", "Enjoyment"),
           ("understanding", "Understanding"), ("activity_quality", "Activity quality")]
SENTIMENTS = ["Positive", "Mixed", "Negative"]

STANDARD_ROLES = ["Main club contact", "SLT on site", "Safeguarding (DSL)", "First aid",
                  "Office / reception", "Site manager"]

# key, label, group, restricted, multiline
GUIDELINE_FIELDS = [
    ("concern", "Concern or disclosure", "Safeguarding and safety", False, False),
    ("fire", "Fire alarm", "Safeguarding and safety", False, False),
    ("child_missing", "Child missing", "Safeguarding and safety", False, False),
    ("entrance", "Entrance", "Arrival and check-in", False, False),
    ("sign_in", "Sign in", "Arrival and check-in", False, False),
    ("door_codes", "Door codes", "Arrival and check-in", True, False),
    ("parking", "Parking", "Arrival and check-in", False, False),
    ("sign_out", "Sign out", "Arrival and check-in", False, False),
    ("arrival_children", "Children get to you by", "Drop-off, register and collection", False, False),
    ("register", "Register", "Drop-off, register and collection", False, False),
    ("collection_point", "Collection point", "Drop-off, register and collection", False, False),
    ("late_collection", "Late collection", "Drop-off, register and collection", False, False),
    ("wifi_network", "Wifi network", "IT set-up and Turing kit", False, False),
    ("wifi_password", "Wifi password", "IT set-up and Turing kit", True, False),
    ("laptops", "Laptops / PCs", "IT set-up and Turing kit", False, False),
    ("logins", "Logins", "IT set-up and Turing kit", False, False),
    ("screen", "Screen / board", "IT set-up and Turing kit", False, False),
    ("kit", "Turing kit on site", "IT set-up and Turing kit", False, False),
    ("blocked_sites", "Blocked sites", "IT set-up and Turing kit", False, False),
    ("top_do", "Things to do here (one per line)", "Notes for cover teachers", False, True),
    ("watch_out", "Things to watch out for (one per line)", "Notes for cover teachers", False, True),
    ("commentary", "Other teacher commentary", "Notes for cover teachers", False, True),
]
GUIDELINE_KEYS = {k for k, *_ in GUIDELINE_FIELDS}
RESTRICTED_KEYS = {k for k, _, _, restricted, _ in GUIDELINE_FIELDS if restricted}
GUIDELINE_GROUPS = list(dict.fromkeys(g for _, _, g, _, _ in GUIDELINE_FIELDS))


# ---------------------------------------------------------------- helpers
def avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(mean(vals), 1) if vals else None


def attention_reasons(r) -> list[str]:
    reasons = []
    if r["teacher_sentiment"] == "Negative":
        reasons.append("Negative response")
    elif r["teacher_sentiment"] == "Mixed":
        reasons.append("Mixed response")
    if r["action_status"] == "open" and (r["priority"] or 0) >= config.HIGH_PRIORITY:
        reasons.append("High-priority action open")
    if r["completion_status"] in ("partial", "not_completed"):
        reasons.append("Session not completed")
    if r["technical_status"] not in (None, "none"):
        reasons.append("Technical issue")
    return reasons


def _enrich(r: dict) -> dict:
    r["reasons"] = attention_reasons(r)
    r["needs_attention"] = bool(r["reasons"])
    r["is_high_priority"] = (r["priority"] or 0) >= config.HIGH_PRIORITY
    return r


def fetch_sessions(conn, school_id=None, week_start=None, since=None) -> list[dict]:
    sql, args = "SELECT * FROM v_sessions WHERE 1=1", []
    if school_id:
        sql += " AND school_id = ?"; args.append(school_id)
    if week_start:
        sql += " AND week_start = ?"; args.append(week_start)
    if since:
        sql += " AND session_date >= ?"; args.append(since)
    sql += " ORDER BY session_date DESC, submitted_at DESC, session_id DESC"
    return [_enrich(dict(r)) for r in conn.execute(sql, args)]


def sentiment_dist(rows) -> dict:
    rated = [r for r in rows if r["teacher_sentiment"]]
    n = len(rated)
    out = {"n": n}
    for s in SENTIMENTS:
        c = sum(1 for r in rated if r["teacher_sentiment"] == s)
        out[s] = {"count": c, "pct": round(100 * c / n) if n else 0}
    return out


def kpis(rows, conn=None) -> dict:
    k = {
        "sessions": len(rows),
        "schools_reporting": len({r["school_id"] for r in rows}),
        "students": sum(r["students_present"] or 0 for r in rows),
        "attention": sum(1 for r in rows if r["needs_attention"]),
        "open_actions": sum(1 for r in rows if r["action_status"] == "open"),
        "technical": sum(1 for r in rows if r["technical_status"] not in (None, "none")),
    }
    for key, _ in RATINGS:
        k[key] = avg(rows, key)
    if conn is not None:
        k["schools_total"] = conn.execute("SELECT COUNT(*) FROM schools WHERE active=1").fetchone()[0]
    return k


def trend(rows) -> list[dict]:
    """One point per session, oldest first (weekly club, so sessions ~ weeks)."""
    out = []
    for r in sorted(rows, key=lambda r: (r["session_date"], r["session_id"])):
        out.append({"label": f"{date.fromisoformat(r['session_date']):%d %b}",
                    "detail": f"{r['year_group_label'] or ''}".strip(),
                    "engagement": r["engagement"], "enjoyment": r["enjoyment"],
                    "understanding": r["understanding"]})
    return out


def weekly_trend(rows) -> list[dict]:
    """Average ratings per calendar week, for the all-schools view."""
    weeks = {}
    for r in rows:
        weeks.setdefault(r["week_start"], []).append(r)
    return [{"label": f"w/c {date.fromisoformat(w):%d %b}", "n": len(rs),
             "engagement": avg(rs, "engagement"), "enjoyment": avg(rs, "enjoyment"),
             "understanding": avg(rs, "understanding")} for w, rs in sorted(weeks.items())]


def theme_counts(conn, school_id=None, since=None) -> dict:
    """Positive themes and recurring issues derived from the tick-boxes teachers ticked."""
    sql = ("SELECT th.label, th.polarity, COUNT(DISTINCT s.session_id) AS n "
           "FROM session_tags st JOIN themes th USING (theme_id) JOIN sessions s USING (session_id) WHERE 1=1")
    args = []
    if school_id:
        sql += " AND s.school_id = ?"; args.append(school_id)
    if since:
        sql += " AND s.session_date >= ?"; args.append(since)
    sql += " GROUP BY th.theme_id ORDER BY n DESC, th.label"
    tot_sql = "SELECT COUNT(*) FROM sessions WHERE 1=1" + (" AND school_id = ?" if school_id else "") \
        + (" AND session_date >= ?" if since else "")
    total = conn.execute(tot_sql, args).fetchone()[0] or 0
    out = {"positive": [], "negative": [], "sessions": total}
    for r in conn.execute(sql, args):
        out[r["polarity"]].append({"label": r["label"], "n": r["n"],
                                   "pct": round(100 * r["n"] / total) if total else 0})
    return out


def session_tags(conn, session_id) -> dict:
    out = {"positive": [], "negative": []}
    for r in conn.execute("SELECT th.label, th.polarity FROM session_tags st JOIN themes th USING (theme_id) "
                          "WHERE st.session_id = ? ORDER BY th.label", (session_id,)):
        out[r["polarity"]].append(r["label"])
    return out


# ---------------------------------------------------------------- AI layer (optional)
def latest_ai_by_field(conn, session_id) -> list[dict]:
    rows = conn.execute("SELECT * FROM ai_analysis WHERE session_id = ? ORDER BY analysis_id DESC",
                        (session_id,)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["source_field"] not in seen:
            seen.add(r["source_field"])
            d = dict(r)
            d["themes"] = json.loads(d["themes"]) if d["themes"] else []
            out.append(d)
    return sorted(out, key=lambda d: d["source_field"])


def ai_dist(conn, rows):
    """AI sentiment of the 'evidence' field, or None if no model has been run."""
    from .sentiment.service import PRIMARY_FIELD
    ids = {r["session_id"] for r in rows}
    latest = {}
    for a in conn.execute("SELECT session_id, sentiment, needs_review, model_version FROM ai_analysis "
                          "WHERE source_field = ? ORDER BY analysis_id", (PRIMARY_FIELD,)):
        if a["session_id"] in ids:
            latest[a["session_id"]] = a
    if not latest:
        return None
    n = len(latest)
    out = {"n": n, "review": sum(1 for a in latest.values() if a["needs_review"]),
           "version": next(reversed(latest.values()))["model_version"]}
    for s in ["Positive", "Mixed", "Negative", "Neutral"]:
        c = sum(1 for a in latest.values() if a["sentiment"] == s)
        out[s] = {"count": c, "pct": round(100 * c / n)}
    return out


# ---------------------------------------------------------------- schools
def get_school(conn, school_id):
    r = conn.execute("SELECT * FROM schools WHERE school_id = ?", (school_id,)).fetchone()
    return dict(r) if r else None


def list_schools(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM schools WHERE active = 1 ORDER BY name")]


def club_groups(conn, school_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM school_club_groups WHERE school_id = ? ORDER BY sort_order, group_id", (school_id,))]


def contacts(conn, school_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM school_contacts WHERE school_id = ? ORDER BY sort_order, contact_id", (school_id,))]


def current_guidelines(conn, school_id) -> dict:
    return {r["section"]: r["content"] for r in conn.execute(
        "SELECT section, content FROM school_guidelines WHERE school_id = ? AND valid_to IS NULL", (school_id,))}


def guideline_versions(conn, school_id) -> int:
    return conn.execute("SELECT COALESCE(MAX(version),0) FROM school_guidelines WHERE school_id = ?",
                        (school_id,)).fetchone()[0]


def change_log(conn, school_id, limit=10):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM school_change_log WHERE school_id = ? ORDER BY change_id DESC LIMIT ?",
        (school_id, limit))]


def teachers_for_school(conn, school_id):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT t.name FROM sessions s JOIN teachers t USING (teacher_id) WHERE s.school_id = ?",
        (school_id,))]


def review_status(school) -> dict:
    d = school.get("reviewed_at")
    if not d:
        return {"state": "never", "label": "Never reviewed", "days": None}
    days = (config.today() - date.fromisoformat(d)).days
    if days > config.REVIEW_STALE_DAYS:
        return {"state": "stale", "label": f"Last reviewed {days} days ago", "days": days}
    when = "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"
    return {"state": "ok", "label": "Reviewed " + when, "days": days}


def completeness(conn, school) -> dict:
    """What is still missing from a school's info sheet. Blank safeguarding contacts are called out."""
    sid = school["school_id"]
    groups, cons, g = club_groups(conn, sid), contacts(conn, sid), current_guidelines(conn, sid)
    named = {c["role"] for c in cons if c["name"]}
    checks = [("School address", bool(school["address"])), ("School phone", bool(school["phone"])),
              ("Club days", bool(school["club_days"])), ("Term dates", bool(school["term_start"] and school["term_end"])),
              ("Club groups and rooms", bool(groups)), ("School logo", bool(school["logo_path"]))]
    checks += [(f"Contact: {role}", role in named) for role in STANDARD_ROLES]
    for key in ("concern", "fire", "entrance", "collection_point", "top_do", "watch_out", "commentary"):
        label = next(l for k, l, *_ in GUIDELINE_FIELDS if k == key)
        checks.append((label.replace(" (one per line)", ""), bool(g.get(key))))
    done = sum(1 for _, ok in checks if ok)
    warnings = []
    if "Safeguarding (DSL)" not in named:
        warnings.append("Safeguarding lead (DSL) not recorded")
    for gr in groups:
        if gr["on_site_by"] and gr["start_time"] and gr["on_site_by"] >= gr["start_time"]:
            warnings.append(f"{gr['year_group_label']}: 'teacher on site by' is not before the club start")
    return {"checks": [{"label": l, "ok": ok} for l, ok in checks], "done": done, "total": len(checks),
            "pct": round(100 * done / len(checks)), "warnings": warnings}


def school_summaries(conn, rows) -> list[dict]:
    by_school = {}
    for r in rows:
        by_school.setdefault(r["school_id"], []).append(r)
    out = []
    for s in list_schools(conn):
        rs = by_school.get(s["school_id"], [])
        out.append({**s, "n": len(rs), "last": rs[0]["session_date"] if rs else None,
                    "engagement": avg(rs, "engagement"), "enjoyment": avg(rs, "enjoyment"),
                    "understanding": avg(rs, "understanding"),
                    "attention": sum(1 for r in rs if r["needs_attention"]),
                    "sentiment": sentiment_dist(rs), "review": review_status(s)})
    return out


def pending_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM pending_feedback WHERE resolved = 0").fetchone()[0]


# ---------------------------------------------------------------- weeks
def week_bounds(start: date) -> tuple[date, date]:
    m = monday_of(start)
    return m, m + timedelta(days=6)


def activity_ranking(rows, min_sessions=1):
    acts = {}
    for r in rows:
        if r["activity"] and r["teacher_sentiment"]:
            acts.setdefault(r["activity"], []).append(r)
    out = []
    for name, rs in acts.items():
        if len(rs) >= min_sessions:
            pos = sum(1 for r in rs if r["teacher_sentiment"] == "Positive")
            out.append({"activity": name, "n": len(rs), "pct_positive": round(100 * pos / len(rs)),
                        "enjoyment": avg(rs, "enjoyment"), "engagement": avg(rs, "engagement")})
    return sorted(out, key=lambda a: (-a["pct_positive"], -a["n"]))
