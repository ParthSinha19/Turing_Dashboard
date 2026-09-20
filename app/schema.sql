-- Turing Education AI Club dashboard: relational schema (SQLite)
-- Principles: school_id links everything; teacher text is never overwritten;
-- AI output lives in its own table with model_version; guidelines are versioned.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- schools
CREATE TABLE IF NOT EXISTS schools (
    school_id    TEXT PRIMARY KEY,               -- SCH001, SCH002 ...
    name         TEXT NOT NULL UNIQUE,           -- official name
    short_name   TEXT,
    logo_path    TEXT,                           -- relative to /static
    address      TEXT,
    postcode     TEXT,
    phone        TEXT,
    club_days    TEXT,
    term_start   TEXT,
    term_end     TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    reviewed_by  TEXT,
    reviewed_at  TEXT,                           -- ISO date the info sheet was last reviewed
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Keywords used to identify a school from free text. Stored normalised
-- (lower case, punctuation removed). weight is reserved for future tie-breaking.
CREATE TABLE IF NOT EXISTS school_aliases (
    alias      TEXT NOT NULL,
    school_id  TEXT NOT NULL REFERENCES schools(school_id) ON DELETE CASCADE,
    source     TEXT NOT NULL DEFAULT 'seed',     -- seed | manual | learned
    PRIMARY KEY (alias, school_id)
);

CREATE TABLE IF NOT EXISTS school_club_groups (
    group_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id         TEXT NOT NULL REFERENCES schools(school_id) ON DELETE CASCADE,
    year_group_label  TEXT NOT NULL,
    year_min          INTEGER,
    year_max          INTEGER,                   -- NULL = open ended ("5 and above")
    start_time        TEXT,
    end_time          TEXT,
    on_site_by        TEXT,
    room              TEXT,
    typical_students  INTEGER,
    sort_order        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS school_contacts (
    contact_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id   TEXT NOT NULL REFERENCES schools(school_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    name        TEXT,
    phone       TEXT,
    is_turing   INTEGER NOT NULL DEFAULT 0,      -- 1 = Turing-side contact, not school staff
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Versioned guidelines: one current row per (school, section) where valid_to IS NULL.
CREATE TABLE IF NOT EXISTS school_guidelines (
    guideline_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id     TEXT NOT NULL REFERENCES schools(school_id) ON DELETE CASCADE,
    section       TEXT NOT NULL,
    content       TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    edited_by     TEXT
);
CREATE INDEX IF NOT EXISTS ix_guidelines_current ON school_guidelines (school_id, section, valid_to);

CREATE TABLE IF NOT EXISTS school_change_log (
    change_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id   TEXT NOT NULL REFERENCES schools(school_id) ON DELETE CASCADE,
    changed_at  TEXT NOT NULL,
    changed_by  TEXT,
    summary     TEXT NOT NULL
);

-- ---------------------------------------------------------------- sessions
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT,
    imported_at    TEXT NOT NULL,
    rows_total     INTEGER NOT NULL DEFAULT 0,
    rows_imported  INTEGER NOT NULL DEFAULT 0,
    rows_duplicate INTEGER NOT NULL DEFAULT 0,
    rows_pending   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id             TEXT NOT NULL REFERENCES schools(school_id),
    club_group_id         INTEGER REFERENCES school_club_groups(group_id) ON DELETE SET NULL,
    teacher_id            INTEGER REFERENCES teachers(teacher_id),
    session_date          TEXT NOT NULL,          -- ISO date
    week_start            TEXT NOT NULL,          -- ISO date of that Monday (calendar week)
    programme_week        INTEGER,
    session_number        INTEGER,
    activity              TEXT,
    year_group_label      TEXT,
    students_present      INTEGER,
    completion_status     TEXT,                   -- complete | mostly | partial | not_completed | unknown
    completion_raw        TEXT,
    incomplete_reason     TEXT,
    technical_status      TEXT NOT NULL DEFAULT 'none',   -- none | minor | major | yes
    technical_description TEXT,
    submitted_at          TEXT,
    school_name_raw       TEXT,                   -- exactly what the teacher typed
    school_match_method   TEXT,                   -- exact | keyword | text | manual
    needs_school_review   INTEGER NOT NULL DEFAULT 0,
    source_hash           TEXT NOT NULL UNIQUE,   -- idempotent import key
    batch_id              INTEGER REFERENCES import_batches(batch_id)
);
CREATE INDEX IF NOT EXISTS ix_sessions_school ON sessions (school_id, session_date);
CREATE INDEX IF NOT EXISTS ix_sessions_week ON sessions (week_start);

CREATE TABLE IF NOT EXISTS feedback (
    session_id           INTEGER PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    engagement           INTEGER,
    enjoyment            INTEGER,
    understanding        INTEGER,
    difficulty           INTEGER,                 -- 1 too easy, 3 about right, 5 too hard (assumed)
    pace                 INTEGER,
    activity_quality     INTEGER,
    teacher_confidence   INTEGER,                 -- not on the current form; nullable
    student_response_raw TEXT,                    -- e.g. "Mostly positive"
    teacher_sentiment    TEXT,                    -- Positive | Mixed | Negative
    session_outcome      TEXT                     -- e.g. "Very well"
);

CREATE TABLE IF NOT EXISTS comments (
    session_id        INTEGER PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    positive_comment  TEXT,
    negative_comment  TEXT,
    evidence          TEXT,
    teacher_reflection TEXT,                      -- not on the current form; nullable
    action_category   TEXT,
    action_required   TEXT,
    priority          INTEGER,
    action_status     TEXT NOT NULL DEFAULT 'none',  -- none | open | done
    action_owner      TEXT,
    action_closed_at  TEXT,
    sensitivity_flag  INTEGER NOT NULL DEFAULT 0
);

-- Themes come from the form's tick-box answers and are created as they appear.
CREATE TABLE IF NOT EXISTS themes (
    theme_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    label     TEXT NOT NULL,
    polarity  TEXT NOT NULL CHECK (polarity IN ('positive', 'negative')),
    UNIQUE (label, polarity)
);
CREATE TABLE IF NOT EXISTS session_tags (
    session_id  INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    theme_id    INTEGER NOT NULL REFERENCES themes(theme_id),
    source      TEXT NOT NULL DEFAULT 'form',     -- form | model | manual
    PRIMARY KEY (session_id, theme_id)
);

-- AI-derived output, kept apart from the teacher's words. One row per text field.
CREATE TABLE IF NOT EXISTS ai_analysis (
    analysis_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    source_field      TEXT NOT NULL,              -- positive_comment | negative_comment | evidence | action_required
    sentiment         TEXT NOT NULL,              -- Positive | Negative | Mixed | Neutral
    sentiment_confidence REAL,
    themes            TEXT,                       -- JSON list (Phase 3)
    issues            TEXT,                       -- JSON list (Phase 3)
    ai_summary        TEXT,                       -- (Phase 3)
    model_name        TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    needs_review      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    UNIQUE (session_id, source_field, model_version)
);

-- Feedback rows whose school could not be identified. They wait here until
-- a person assigns a school; nothing is imported without a school_id.
CREATE TABLE IF NOT EXISTS pending_feedback (
    pending_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     INTEGER REFERENCES import_batches(batch_id),
    source_hash  TEXT NOT NULL UNIQUE,
    school_text  TEXT,
    reason       TEXT,
    raw_json     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0
);

DROP VIEW IF EXISTS v_sessions;
CREATE VIEW v_sessions AS
SELECT s.session_id, s.school_id, sc.name AS school_name, sc.short_name, sc.logo_path,
       s.club_group_id, t.name AS teacher, s.session_date, s.week_start,
       s.programme_week, s.session_number, s.activity, s.year_group_label,
       s.students_present, s.completion_status, s.completion_raw, s.incomplete_reason,
       s.technical_status, s.technical_description, s.submitted_at,
       s.school_name_raw, s.school_match_method, s.needs_school_review,
       f.engagement, f.enjoyment, f.understanding, f.difficulty, f.pace,
       f.activity_quality, f.teacher_confidence, f.student_response_raw,
       f.teacher_sentiment, f.session_outcome,
       c.positive_comment, c.negative_comment, c.evidence, c.teacher_reflection,
       c.action_category, c.action_required, c.priority, c.action_status,
       c.action_owner, c.action_closed_at, c.sensitivity_flag
FROM sessions s
JOIN schools sc ON sc.school_id = s.school_id
LEFT JOIN teachers t ON t.teacher_id = s.teacher_id
LEFT JOIN feedback f ON f.session_id = s.session_id
LEFT JOIN comments c ON c.session_id = s.session_id;
