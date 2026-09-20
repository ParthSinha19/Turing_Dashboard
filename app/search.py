"""Global search. Rule-based: a school lookup by keyword plus a few intent words, translated
into the same deterministic queries the dashboards use. No text is generated and nothing is guessed;
an LLM summary layer can later sit on top of these results."""
import difflib
from datetime import timedelta

from . import config, queries as Q
from .school_match import find_school_in_query, norm
from .normalise import monday_of

STOP = {"show", "me", "the", "of", "from", "at", "for", "in", "a", "an", "is", "are", "has", "have",
        "how", "been", "going", "doing", "ai", "club", "feedback", "latest", "and", "school", "what"}


def _has(nq, *needles):
    return any(n in nq for n in needles)


def run_search(conn, query: str) -> dict:
    nq = norm(query)
    if not nq:
        return {"redirect": "/"}
    today = config.today()
    sid = find_school_in_query(conn, query)
    school = Q.get_school(conn, sid) if sid else None

    week_start = since = None
    period = None
    if "this week" in nq:
        week_start, period = monday_of(today).isoformat(), "this week"
    elif "last week" in nq:
        week_start, period = (monday_of(today) - timedelta(days=7)).isoformat(), "last week"
    elif _has(nq, "last month", "past month", "this month"):
        since, period = (today - timedelta(days=30)).isoformat(), "last 30 days"
    elif _has(nq, "recently", "recent problems"):
        since, period = (today - timedelta(days=30)).isoformat(), "last 30 days"

    negative = _has(nq, "negative", "poor", "poorly", "bad")
    positive = _has(nq, "positive", "positively")
    attention = _has(nq, "attention", "concern", "struggling") or (
        _has(nq, "which schools") and _has(nq, "problem", "issue"))
    problems = _has(nq, "common problem", "common issue", "most common", "biggest problem", "recurring")
    activities = "activit" in nq
    latest = _has(nq, "latest", "recent", "newest")
    overall = _has(nq, "overall")
    metric = next((m for m in ("engagement", "enjoyment", "understanding") if m in nq), None)
    trending = bool(metric and _has(nq, "chang", "trend", "over"))
    changing = not trending and _has(nq, "chang", "improv", "follow up", "action")

    interp = []
    if school:
        interp.append(f"School: {school['name']}")
    if period:
        interp.append(f"Period: {period}")
    for flag, text in ((negative, "Negative responses"), (positive, "Positive responses"),
                       (attention, "Needs attention"), (problems, "Most common problems"),
                       (changing, "Open actions"), (activities, "By activity")):
        if flag:
            interp.append(text)
    if trending:
        interp.append(f"Trend: {metric}")

    intents = any((negative, positive, attention, problems, changing, activities, trending, latest,
                   period, overall))
    if overall and not school:
        return {"redirect": "/"}
    if school and not intents:
        return {"redirect": f"/schools/{sid}"}

    scope = Q.fetch_sessions(conn, school_id=sid, week_start=week_start, since=since)
    blocks = []
    if problems:
        t = Q.theme_counts(conn, school_id=sid, since=since)
        blocks.append({"type": "themes", "title": "Most common problems", "items": t["negative"][:8],
                       "sessions": t["sessions"]})
    if changing:
        acts = sorted([r for r in scope if r["action_status"] == "open"],
                      key=lambda r: (-(r["priority"] or 0), r["session_date"]))
        blocks.append({"type": "sessions", "title": "Open actions, highest priority first",
                       "rows": acts[:20], "total": len(acts)})
    if activities:
        pool = [r for r in scope if not negative or r["teacher_sentiment"] == "Negative"]
        blocks.append({"type": "activities", "title": "Activities by positive response",
                       "items": Q.activity_ranking(pool)})
    if trending:
        blocks.append({"type": "trend", "title": f"Average {metric} by week", "metric": metric,
                       "points": Q.weekly_trend(scope)})
    wants_list = attention or negative or latest or (positive and not activities) or not blocks
    if wants_list:
        rows, title = scope, "Latest feedback" if latest else "Feedback"
        if attention:
            rows, title = [r for r in scope if r["needs_attention"]], "Sessions needing attention"
        elif negative:
            rows, title = [r for r in scope if r["teacher_sentiment"] == "Negative"], "Negative feedback"
        elif positive:
            rows, title = [r for r in scope if r["teacher_sentiment"] == "Positive"], "Positive feedback"
        blocks.append({"type": "sessions", "title": title, "rows": rows[:5 if latest else 30],
                       "total": len(rows)})

    if not blocks or (not school and not intents):
        names = {s["name"]: s["school_id"] for s in Q.list_schools(conn)}
        close = difflib.get_close_matches(query, list(names), n=3, cutoff=0.4)
        return {"interpretation": interp, "blocks": [], "school": None,
                "suggestions": [{"name": n, "id": names[n]} for n in close]}
    return {"interpretation": interp, "blocks": blocks, "school": school, "suggestions": []}
