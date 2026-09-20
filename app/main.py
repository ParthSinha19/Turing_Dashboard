import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from starlette.datastructures import UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, importer, queries as Q
from .normalise import monday_of
from .search import run_search
from .sentiment import get_model
from .sentiment.service import run_analysis

ALLOWED_LOGO = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_LOGO_BYTES = 2_000_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    yield


app = FastAPI(title="Turing AI Club dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(config.TEMPLATE_DIR))


# ---------------------------------------------------------------- template helpers
def _to_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def f_date(v, fmt="%a %d %b %Y"):
    d = _to_date(v)
    return d.strftime(fmt).replace(" 0", " ") if d else "—"


def f_lines(v):
    return [ln.strip() for ln in (v or "").splitlines() if ln.strip()]


templates.env.filters["date"] = f_date
templates.env.filters["lines"] = f_lines
templates.env.globals.update(RATINGS=Q.RATINGS, MODEL_ON=config.SENTIMENT_MODEL != "none", FEEDBACK_FORM_URL=config.FEEDBACK_FORM_URL, HIGH_PRIORITY=config.HIGH_PRIORITY,
                             LOW_CONFIDENCE=config.LOW_CONFIDENCE)


def render(request: Request, name: str, conn, status_code: int = 200, **ctx):
    ctx.setdefault("active", "")
    ctx.setdefault("q", "")
    ctx["pending"] = Q.pending_count(conn)
    ctx["msg"] = request.query_params.get("msg")
    ctx["today"] = config.today()
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def back(url: str, msg: str | None = None):
    if msg:
        url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
    return RedirectResponse(url, status_code=303)


def _school_or_404(conn, sid):
    school = Q.get_school(conn, sid)
    if not school:
        raise_404()
    return school


def raise_404():
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Not found")


# ---------------------------------------------------------------- overview
@app.get("/", response_class=HTMLResponse)
def overview(request: Request, conn=Depends(db.get_conn)):
    rows = Q.fetch_sessions(conn)
    this_week = monday_of(config.today()).isoformat()
    return render(
        request, "overview.html", conn, active="overview",
        k=Q.kpis(rows, conn), teacher=Q.sentiment_dist(rows), ai=Q.ai_dist(conn, rows),
        attention=[r for r in rows if r["needs_attention"]][:6],
        schools=Q.school_summaries(conn, rows), wtrend=Q.weekly_trend(rows),
        week_count=sum(1 for r in rows if r["week_start"] == this_week),
        themes=Q.theme_counts(conn), model_on=config.SENTIMENT_MODEL != "none")


# ---------------------------------------------------------------- this week
@app.get("/week", response_class=HTMLResponse)
def week(request: Request, start: str | None = None, conn=Depends(db.get_conn)):
    current = monday_of(config.today())
    monday = monday_of(_to_date(start) or current)
    rows = Q.fetch_sessions(conn, week_start=monday.isoformat())
    prev_w, next_w = monday - timedelta(days=7), monday + timedelta(days=7)
    return render(
        request, "week.html", conn, active="week", rows=rows, monday=monday,
        sunday=monday + timedelta(days=6), is_current=monday == current,
        prev_url=f"/week?start={prev_w.isoformat()}",
        next_url=f"/week?start={next_w.isoformat()}" if next_w <= current else None,
        current_url="/week", k=Q.kpis(rows), dist=Q.sentiment_dist(rows))


# ---------------------------------------------------------------- schools
@app.get("/schools", response_class=HTMLResponse)
def schools(request: Request, conn=Depends(db.get_conn)):
    rows = Q.fetch_sessions(conn)
    return render(request, "schools.html", conn, active="schools", schools=Q.school_summaries(conn, rows))


@app.post("/schools/new")
async def school_new(request: Request, conn=Depends(db.get_conn)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return back("/schools", "Enter the school's official name.")
    aliases = [a for a in (form.get("aliases") or "").split(",") if a.strip()]
    try:
        sid = importer.create_school(conn, name, aliases)
    except Exception:
        return back("/schools", f"'{name}' already exists.")
    return back(f"/schools/{sid}/edit", "School added. Fill in its info sheet.")


@app.get("/schools/{sid}", response_class=HTMLResponse)
def school_page(request: Request, sid: str, conn=Depends(db.get_conn)):
    school = _school_or_404(conn, sid)
    rows = Q.fetch_sessions(conn, school_id=sid)
    guidelines = Q.current_guidelines(conn, sid)
    grouped = []
    for group in Q.GUIDELINE_GROUPS:
        items = [(label, guidelines[key], multi) for key, label, g, restricted, multi in Q.GUIDELINE_FIELDS
                 if g == group and not restricted and guidelines.get(key)]
        if items:
            grouped.append({"title": group, "fields": items})
    cons = Q.contacts(conn, sid)
    return render(
        request, "school.html", conn, active="schools", school=school, groups=Q.club_groups(conn, sid),
        school_contacts=[c for c in cons if not c["is_turing"] and c["name"]],
        turing_contacts=[c for c in cons if c["is_turing"] and c["name"]],
        guideline_groups=grouped, comp=Q.completeness(conn, school), review=Q.review_status(school),
        rows=rows, k=Q.kpis(rows), dist=Q.sentiment_dist(rows), ai=Q.ai_dist(conn, rows),
        trend=Q.trend(rows), themes=Q.theme_counts(conn, school_id=sid),
        open_actions=[r for r in rows if r["action_status"] == "open"],
        teachers=Q.teachers_for_school(conn, sid))


@app.get("/schools/{sid}/handover", response_class=HTMLResponse)
def handover(request: Request, sid: str, restricted: int = 0, conn=Depends(db.get_conn)):
    school = _school_or_404(conn, sid)
    guidelines = Q.current_guidelines(conn, sid)
    grouped = []
    for group in Q.GUIDELINE_GROUPS:
        items = [(label, guidelines[key], multi) for key, label, g, is_restricted, multi in Q.GUIDELINE_FIELDS
                 if g == group and guidelines.get(key) and (restricted or not is_restricted)]
        if items:
            grouped.append({"title": group, "fields": items})
    return render(request, "handover.html", conn, active="schools", school=school,
                  groups=Q.club_groups(conn, sid), contacts=[c for c in Q.contacts(conn, sid) if c["name"]],
                  guideline_groups=grouped, restricted=restricted, comp=Q.completeness(conn, school),
                  review=Q.review_status(school))


@app.get("/schools/{sid}/edit", response_class=HTMLResponse)
def school_edit(request: Request, sid: str, conn=Depends(db.get_conn)):
    school = _school_or_404(conn, sid)
    cons = Q.contacts(conn, sid)
    by_role = {c["role"]: c for c in cons}
    extras = [c for c in cons if c["role"] not in Q.STANDARD_ROLES]
    aliases = [r[0] for r in conn.execute("SELECT alias FROM school_aliases WHERE school_id=? ORDER BY alias",
                                          (sid,))]
    return render(request, "school_edit.html", conn, active="schools", school=school,
                  groups=Q.club_groups(conn, sid), by_role=by_role, roles=Q.STANDARD_ROLES, extras=extras,
                  guidelines=Q.current_guidelines(conn, sid), fields=Q.GUIDELINE_FIELDS,
                  aliases=aliases, log=Q.change_log(conn, sid), editor=request.cookies.get("editor", ""),
                  version=Q.guideline_versions(conn, sid))


@app.post("/schools/{sid}/edit")
async def school_save(request: Request, sid: str, conn=Depends(db.get_conn)):
    school = _school_or_404(conn, sid)
    form = await request.form()
    editor = (form.get("edited_by") or "").strip()
    name = (form.get("name") or "").strip()
    edit_url = f"/schools/{sid}/edit"
    if not editor or not name:
        return back(edit_url, "Enter the school name and your name before saving.")
    today = config.today().isoformat()
    changed = []

    # school details
    fields = ["name", "short_name", "address", "postcode", "phone", "club_days", "term_start", "term_end"]
    new = {f: (form.get(f) or "").strip() or None for f in fields}
    new["name"] = name
    for f in fields:
        if (school[f] or None) != new[f]:
            changed.append(f.replace("_", " ").capitalize())
    try:
        conn.execute(f"UPDATE schools SET {', '.join(f + '=?' for f in fields)}, reviewed_by=?, reviewed_at=? "
                     "WHERE school_id=?", (*[new[f] for f in fields], editor, today, sid))
    except Exception:
        return back(edit_url, f"Another school is already called '{name}'.")

    # aliases (keywords used to recognise this school in feedback)
    from .school_match import norm
    wanted = {norm(a) for a in (form.get("aliases") or "").split(",") if norm(a)} | {norm(name)}
    have = {r[0] for r in conn.execute("SELECT alias FROM school_aliases WHERE school_id=?", (sid,))}
    if wanted != have:
        conn.execute("DELETE FROM school_aliases WHERE school_id=?", (sid,))
        for a in wanted:
            conn.execute("INSERT OR IGNORE INTO school_aliases (alias, school_id, source) VALUES (?,?, 'manual')",
                         (a, sid))
        changed.append("Search keywords")

    # club groups: update by id, insert new, delete removed (sessions keep their own year-group text)
    ids, labels = form.getlist("group_id"), form.getlist("g_year")
    keep, before = set(), [(g["group_id"], g["year_group_label"], g["start_time"], g["end_time"], g["on_site_by"],
                            g["room"], g["typical_students"]) for g in Q.club_groups(conn, sid)]
    from .normalise import parse_int, parse_year_group
    for i, label in enumerate(labels):
        label = label.strip()
        if not label:
            continue
        ymin, ymax = parse_year_group(label)
        vals = (label, ymin, ymax, form.getlist("g_start")[i].strip() or None, form.getlist("g_end")[i].strip() or None,
                form.getlist("g_onsite")[i].strip() or None, form.getlist("g_room")[i].strip() or None,
                parse_int(form.getlist("g_students")[i]), i)
        if ids[i]:
            conn.execute("UPDATE school_club_groups SET year_group_label=?, year_min=?, year_max=?, start_time=?, "
                         "end_time=?, on_site_by=?, room=?, typical_students=?, sort_order=? WHERE group_id=? "
                         "AND school_id=?", (*vals, int(ids[i]), sid))
            keep.add(int(ids[i]))
        else:
            gid = conn.execute("INSERT INTO school_club_groups (school_id, year_group_label, year_min, year_max, "
                               "start_time, end_time, on_site_by, room, typical_students, sort_order) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, *vals)).lastrowid
            keep.add(gid)
    for (gid, *_rest) in before:
        if gid not in keep:
            conn.execute("DELETE FROM school_club_groups WHERE group_id=?", (gid,))
    after = [(g["group_id"], g["year_group_label"], g["start_time"], g["end_time"], g["on_site_by"], g["room"],
              g["typical_students"]) for g in Q.club_groups(conn, sid)]
    if [b[1:] for b in before] != [a[1:] for a in after]:
        changed.append("Club groups")

    # contacts
    old_contacts = [(c["role"], c["name"] or "", c["phone"] or "", c["is_turing"]) for c in Q.contacts(conn, sid)]
    conn.execute("DELETE FROM school_contacts WHERE school_id=?", (sid,))
    order, new_contacts = 0, []
    for i, role in enumerate(Q.STANDARD_ROLES):
        n_, p_ = (form.get(f"c_name_{i}") or "").strip(), (form.get(f"c_phone_{i}") or "").strip()
        new_contacts.append((role, n_, p_, 0))
        conn.execute("INSERT INTO school_contacts (school_id, role, name, phone, is_turing, sort_order) "
                     "VALUES (?,?,?,?,?,?)", (sid, role, n_ or None, p_ or None, 0, order))
        order += 1
    for role, n_, p_, kind in zip(form.getlist("x_role"), form.getlist("x_name"), form.getlist("x_phone"),
                                  form.getlist("x_kind")):
        if role.strip() and (n_.strip() or p_.strip()):
            turing = int(kind == "turing")
            new_contacts.append((role.strip(), n_.strip(), p_.strip(), turing))
            conn.execute("INSERT INTO school_contacts (school_id, role, name, phone, is_turing, sort_order) "
                         "VALUES (?,?,?,?,?,?)", (sid, role.strip(), n_.strip() or None, p_.strip() or None,
                                                  turing, order))
            order += 1
    norm_old = sorted(c for c in old_contacts if c[1] or c[2])
    if norm_old != sorted(c for c in new_contacts if c[1] or c[2]):
        changed.append("Contacts")

    # guidelines: new version per changed section, old versions kept
    current = Q.current_guidelines(conn, sid)
    for key, label, *_ in Q.GUIDELINE_FIELDS:
        text = (form.get(f"g_{key}") or "").replace("\r\n", "\n").strip()
        if text == (current.get(key) or ""):
            continue
        version = conn.execute("SELECT COALESCE(MAX(version),0) FROM school_guidelines WHERE school_id=? "
                               "AND section=?", (sid, key)).fetchone()[0]
        conn.execute("UPDATE school_guidelines SET valid_to=? WHERE school_id=? AND section=? AND valid_to IS NULL",
                     (today, sid, key))
        if text:
            conn.execute("INSERT INTO school_guidelines (school_id, section, content, version, valid_from, edited_by) "
                         "VALUES (?,?,?,?,?,?)", (sid, key, text, version + 1, today, editor))
        changed.append(label.replace(" (one per line)", ""))

    # logo
    upload = form.get("logo")
    if isinstance(upload, UploadFile) and upload.filename:
        ext = "." + upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        data = await upload.read()
        if ext not in ALLOWED_LOGO or len(data) > MAX_LOGO_BYTES:
            conn.commit()
            return back(edit_url, "Saved, but the logo was not uploaded. Use a PNG, JPG, WebP or GIF under 2 MB.")
        config.LOGO_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{sid}-{int(time.time())}{ext}"
        (config.LOGO_DIR / fname).write_bytes(data)
        for old in config.LOGO_DIR.glob(f"{sid}-*"):
            if old.name != fname:
                old.unlink(missing_ok=True)
        conn.execute("UPDATE schools SET logo_path=? WHERE school_id=?", (f"logos/{fname}", sid))
        changed.append("Logo")
    elif form.get("remove_logo"):
        for old in config.LOGO_DIR.glob(f"{sid}-*"):
            old.unlink(missing_ok=True)
        conn.execute("UPDATE schools SET logo_path=NULL WHERE school_id=?", (sid,))
        changed.append("Logo removed")

    conn.execute("INSERT INTO school_change_log (school_id, changed_at, changed_by, summary) VALUES (?,?,?,?)",
                 (sid, datetime.now().isoformat(timespec="minutes"), editor,
                  ("Updated: " + ", ".join(changed)) if changed else "Reviewed, no changes"))
    conn.commit()
    resp = back(f"/schools/{sid}", "Changes saved." if changed else "Marked as reviewed. Nothing changed.")
    resp.set_cookie("editor", editor, max_age=60 * 60 * 24 * 365)
    return resp


@app.post("/schools/{sid}/reviewed")
async def school_reviewed(request: Request, sid: str, conn=Depends(db.get_conn)):
    _school_or_404(conn, sid)
    form = await request.form()
    editor = (form.get("edited_by") or request.cookies.get("editor") or "").strip()
    if not editor:
        return back(f"/schools/{sid}/edit", "Enter your name, then save to mark the sheet as reviewed.")
    conn.execute("UPDATE schools SET reviewed_by=?, reviewed_at=? WHERE school_id=?",
                 (editor, config.today().isoformat(), sid))
    conn.execute("INSERT INTO school_change_log (school_id, changed_at, changed_by, summary) VALUES (?,?,?,?)",
                 (sid, datetime.now().isoformat(timespec="minutes"), editor, "Reviewed, no changes"))
    conn.commit()
    return back(f"/schools/{sid}", "Marked as reviewed.")


# ---------------------------------------------------------------- sessions
@app.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_page(request: Request, session_id: int, conn=Depends(db.get_conn)):
    row = conn.execute("SELECT * FROM v_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        raise_404()
    r = Q._enrich(dict(row))
    group = conn.execute("SELECT * FROM school_club_groups WHERE group_id = ?", (r["club_group_id"],)).fetchone() \
        if r["club_group_id"] else None
    return render(request, "session.html", conn, active="schools", r=r, tags=Q.session_tags(conn, session_id),
                  ai=Q.latest_ai_by_field(conn, session_id), group=dict(group) if group else None,
                  editor=request.cookies.get("editor", ""))


@app.post("/sessions/{session_id}/action")
async def session_action(request: Request, session_id: int, conn=Depends(db.get_conn)):
    """Only the follow-up tracking can be changed here. The teacher's words are never edited."""
    form = await request.form()
    status = form.get("action_status")
    if status not in ("open", "done"):
        return back(f"/sessions/{session_id}", "Choose open or done.")
    closed = config.today().isoformat() if status == "done" else None
    conn.execute("UPDATE comments SET action_status=?, action_owner=?, action_closed_at=? WHERE session_id=?",
                 (status, (form.get("action_owner") or "").strip() or None, closed, session_id))
    conn.commit()
    return back(f"/sessions/{session_id}", "Action updated.")


# ---------------------------------------------------------------- search
@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", conn=Depends(db.get_conn)):
    result = run_search(conn, q)
    if result.get("redirect"):
        return RedirectResponse(result["redirect"], status_code=303)
    return render(request, "search.html", conn, q=q, result=result)


# ---------------------------------------------------------------- import and admin
@app.get("/admin/import", response_class=HTMLResponse)
def import_page(request: Request, conn=Depends(db.get_conn)):
    pend = [dict(r) for r in conn.execute("SELECT * FROM pending_feedback WHERE resolved=0 ORDER BY pending_id")]
    import json
    for p in pend:
        raw = json.loads(p["raw_json"])
        p["summary"] = {k: raw.get(k) for k in ("session_date", "activity", "year_group", "teacher")}
    batches = [dict(r) for r in conn.execute("SELECT * FROM import_batches ORDER BY batch_id DESC LIMIT 8")]
    review = conn.execute("SELECT COUNT(*) FROM sessions WHERE needs_school_review=1").fetchone()[0]
    return render(request, "import.html", conn, active="import", pend=pend, batches=batches,
                  schools=Q.list_schools(conn), model_spec=config.SENTIMENT_MODEL, review_count=review)


@app.post("/admin/import")
async def import_upload(request: Request, conn=Depends(db.get_conn)):
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return back("/admin/import", "Choose a CSV file first.")
    text = (await upload.read()).decode("utf-8-sig", errors="replace")
    s = importer.import_csv_text(conn, text, upload.filename)
    msg = (f"{s['imported']} imported, {s['duplicates']} already present, {s['pending']} need a school.")
    if s["errors"]:
        msg += " " + " ".join(s["errors"][:3])
    return back("/admin/import", msg)


@app.post("/admin/pending/{pending_id}/resolve")
async def pending_resolve(request: Request, pending_id: int, conn=Depends(db.get_conn)):
    form = await request.form()
    school_id = form.get("school_id")
    if not school_id or not Q.get_school(conn, school_id):
        return back("/admin/import", "Choose a school.")
    importer.resolve_pending(conn, pending_id, school_id)
    return back("/admin/import", "Feedback assigned. The wording is now a search keyword for that school.")


@app.post("/admin/pending/{pending_id}/new-school")
async def pending_new_school(request: Request, pending_id: int, conn=Depends(db.get_conn)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return back("/admin/import", "Enter the new school's name.")
    try:
        sid = importer.create_school(conn, name)
    except Exception:
        return back("/admin/import", f"'{name}' already exists. Pick it from the list instead.")
    importer.resolve_pending(conn, pending_id, sid)
    return back(f"/schools/{sid}/edit", f"{name} added and its feedback imported. Fill in its info sheet.")


@app.post("/admin/sentiment/run")
def sentiment_run(conn=Depends(db.get_conn)):
    try:
        model = get_model()
    except Exception as exc:
        return back("/admin/import", f"Sentiment model unavailable: {exc}")
    if model is None:
        return back("/admin/import", "No sentiment model configured. Set SENTIMENT_MODEL=hf and restart. "
                                     "The dashboard keeps using the teacher's response meanwhile.")
    res = run_analysis(conn, model)
    return back("/admin/import", f"AI analysis added for {res['analysed']} text fields "
                                 f"({res['flagged_for_review']} flagged for review). Model: {res['model_version']}.")
