"""Turn raw Google Form rows into a clean internal record.

The form's column names are never used outside this module. Headers are matched
by pattern, and the two identical 'Additional notes or information' columns are
told apart by the question that precedes them.
"""
import hashlib
import re
from datetime import date, datetime, timedelta

# canonical field -> regex on the cleaned, lower-cased header. Order matters.
FIELD_PATTERNS = [
    ("teacher_confidence", r"confidence"),
    ("teacher_reflection", r"reflection"),
    ("submitted_at", r"^timestamp"),
    ("school", r"which school|^school"),
    ("session_label", r"session\s*/\s*week"),
    ("activity", r"^activity\s*/\s*topic"),
    ("students_present", r"number of students"),
    ("session_date", r"date of session"),
    ("year_group", r"year group"),
    ("completion", r"planned session completed"),
    ("incomplete_reason", r"fully completed|main reason"),
    ("technical", r"significant technical problems"),
    ("technical_description", r"if yes, briefly describe"),
    ("engagement", r"student engagement"),
    ("enjoyment", r"student enjoyment"),
    ("understanding", r"student understanding"),
    ("difficulty", r"activity difficulty"),
    ("pace", r"pace of the session"),
    ("activity_quality", r"quality of the activity"),
    ("worked_well", r"worked particularly well"),
    ("difficulties", r"significant difficulties"),
    ("student_response", r"how did students respond"),
    ("evidence", r"what evidence"),
    ("session_outcome", r"how did the session go"),
    ("action_category", r"change or be followed up"),
    ("priority", r"^priority"),
    ("action_required", r"what action is needed"),
    ("teacher", r"^teacher(\s*name)?$"),
]

RATING_FIELDS = ["engagement", "enjoyment", "understanding", "difficulty", "pace",
                 "activity_quality", "teacher_confidence"]
SENSITIVE_WORDS = re.compile(
    r"\b(intelligen\w*|iq|sen|senco|autis\w*|adhd|dyslex\w*|disabilit\w*|"
    r"special needs|slow learners?|stupid|dumb|low ability|weak students?)\b", re.I)


def clean(value):
    """Trim and collapse whitespace; empty -> None. Wording is otherwise untouched."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def map_headers(header: list[str]) -> dict[int, str]:
    """Return {column index: canonical field name} for a header row."""
    mapping: dict[int, str] = {}
    last_field = None
    for i, raw in enumerate(header):
        h = (clean(raw) or "").lower()
        if h.startswith("additional notes"):
            if last_field == "worked_well":
                mapping[i] = "positive_note"
            elif last_field == "difficulties":
                mapping[i] = "negative_note"
            continue
        for field, pattern in FIELD_PATTERNS:
            if re.search(pattern, h):
                mapping[i] = field
                last_field = field
                break
    return mapping


def row_to_raw(header_map: dict[int, str], row: list[str]) -> dict:
    raw = {}
    for i, field in header_map.items():
        if i < len(row):
            raw[field] = clean(row[i])
    return raw


# ---------------------------------------------------------------- parsers
def parse_date(text):
    text = clean(text)
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_timestamp(text):
    text = clean(text)
    if not text:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    d = parse_date(text)
    return datetime(d.year, d.month, d.day) if d else None


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_session_label(text):
    """'Session 2/ Week 1' -> (session_number, programme_week)."""
    m = re.search(r"session\s*(\d+)\s*/?\s*week\s*(\d+)", text or "", re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    nums = re.findall(r"\d+", text or "")
    if len(nums) == 2:
        return int(nums[0]), int(nums[1])
    return None, None


def parse_year_group(text):
    """'Year 1–2' -> (1, 2); 'Year 5+' or '5 and above' -> (5, None); 'Mixed' -> (None, None)."""
    t = clean(text) or ""
    nums = [int(n) for n in re.findall(r"\d+", t)]
    if len(nums) >= 2:
        return min(nums[:2]), max(nums[:2])
    if len(nums) == 1:
        if "+" in t or re.search(r"and (above|over|up)", t, re.I):
            return nums[0], None
        return nums[0], nums[0]
    return None, None


def parse_int(text):
    t = clean(text)
    if t is None:
        return None
    try:
        return int(float(t))
    except ValueError:
        m = re.search(r"\d+", t)
        return int(m.group()) if m else None


def parse_rating(text):
    n = parse_int(text)
    return n if n is not None and 1 <= n <= 5 else None


def completion_status(text):
    t = (clean(text) or "").lower()
    if not t:
        return "unknown"
    if t.startswith("yes") or "as planned" in t:
        return "complete"
    if "mostly" in t:
        return "mostly"
    if "partial" in t:
        return "partial"
    if t.startswith("no") or "not completed" in t:
        return "not_completed"
    return "unknown"


def technical_status(text):
    t = (clean(text) or "").lower()
    if not t or t.startswith("no"):
        return "none"
    if "minor" in t:
        return "minor"
    if "major" in t or "significant" in t or "serious" in t:
        return "major"
    return "yes"


def teacher_sentiment(text):
    """Collapse the teacher's five-point response to Positive / Mixed / Negative."""
    t = (clean(text) or "").lower()
    if not t:
        return None
    if "negative" in t:
        return "Negative"
    if "positive" in t:
        return "Positive"
    return "Mixed"          # 'Mixed' and 'Neutral' both land here


def split_tags(text):
    """Split a tick-box answer into labels; 'No significant difficulties' is not a tag."""
    if not text:
        return []
    labels = [clean(p) for p in text.split(",")]
    return [l for l in labels if l and not l.lower().startswith("no significant")]


def is_sensitive(*texts) -> int:
    return int(any(t and SENSITIVE_WORDS.search(t) for t in texts))


def make_hash(raw: dict) -> str:
    key = "|".join(str(raw.get(k) or "").lower() for k in
                   ("submitted_at", "school", "session_date", "year_group", "activity", "engagement"))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def build_record(raw: dict) -> dict:
    """Raw canonical strings -> typed internal record. Returns {'error': ...} if unusable."""
    session_date = parse_date(raw.get("session_date"))
    submitted = parse_timestamp(raw.get("submitted_at"))
    if session_date is None and submitted is not None:
        session_date = submitted.date()
    if session_date is None:
        return {"error": "no usable session date"}
    number, week = parse_session_label(raw.get("session_label"))
    ymin, ymax = parse_year_group(raw.get("year_group"))
    action_category = raw.get("action_category")
    action_text = raw.get("action_required")
    has_action = bool(action_text) or bool(
        action_category and "no action" not in action_category.lower())
    rec = {
        "school_text": raw.get("school"),
        "teacher": raw.get("teacher"),
        "session_date": session_date.isoformat(),
        "week_start": monday_of(session_date).isoformat(),
        "submitted_at": submitted.isoformat(sep=" ") if submitted else None,
        "session_number": number,
        "programme_week": week,
        "activity": raw.get("activity"),
        "year_group_label": raw.get("year_group"),
        "year_min": ymin, "year_max": ymax,
        "students_present": parse_int(raw.get("students_present")),
        "completion_raw": raw.get("completion"),
        "completion_status": completion_status(raw.get("completion")),
        "incomplete_reason": raw.get("incomplete_reason"),
        "technical_raw": raw.get("technical"),
        "technical_status": technical_status(raw.get("technical")),
        "technical_description": raw.get("technical_description"),
        "student_response_raw": raw.get("student_response"),
        "teacher_sentiment": teacher_sentiment(raw.get("student_response")),
        "session_outcome": raw.get("session_outcome"),
        "positive_comment": raw.get("positive_note"),
        "negative_comment": raw.get("negative_note"),
        "evidence": raw.get("evidence"),
        "teacher_reflection": raw.get("teacher_reflection"),
        "action_category": action_category,
        "action_required": action_text,
        "priority": parse_rating(raw.get("priority")),
        "action_status": "open" if has_action else "none",
        "positive_tags": split_tags(raw.get("worked_well")),
        "negative_tags": split_tags(raw.get("difficulties")),
        "sensitivity_flag": is_sensitive(raw.get("positive_note"), raw.get("negative_note"),
                                         raw.get("evidence"), raw.get("action_required")),
        "source_hash": make_hash(raw),
    }
    for f in RATING_FIELDS:
        rec[f] = parse_rating(raw.get(f))
    return rec
