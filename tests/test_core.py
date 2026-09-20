import io

import pytest
from fastapi.testclient import TestClient

from app import config, db, importer, queries as Q
from app.main import app
from app.normalise import parse_year_group, teacher_sentiment, map_headers
from app.school_match import match_school
from app.sentiment.base import SentimentModel, SentimentResult
from app.sentiment.service import run_analysis


@pytest.fixture(scope="module")
def conn():
    c = db.connect()
    db.init_db(c)
    importer.import_csv_file(c, config.SAMPLE_CSV)
    yield c
    c.close()


@pytest.fixture(scope="module")
def client(conn):
    with TestClient(app) as c:
        yield c


def test_all_sessions_have_school_and_import_is_idempotent(conn):
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE school_id IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 5
    again = importer.import_csv_file(conn, config.SAMPLE_CSV)
    assert again["imported"] == 0 and again["duplicates"] == 5


def test_duplicate_notes_columns_mapped_by_position():
    header = ["Timestamp", "What worked particularly well?", "Additional notes or information",
              "Were there any significant difficulties?", "Additional notes or information .1"]
    assert sorted(map_headers(header).values()) == sorted(
        ["submitted_at", "worked_well", "positive_note", "difficulties", "negative_note"])


def test_parsers():
    assert parse_year_group("Year 1–2") == (1, 2)
    assert parse_year_group("Year 5+") == (5, None)
    assert parse_year_group("Mixed year groups") == (None, None)
    assert teacher_sentiment("Mostly negative") == "Negative"
    assert teacher_sentiment("Very positive") == "Positive"
    assert teacher_sentiment("Mixed") == "Mixed"


def test_school_matching(conn):
    assert match_school(conn, "Brighton Prep School").school_id == "SCH002"
    assert match_school(conn, "hurlingham sch").school_id == "SCH003"
    assert match_school(conn, "St. Catherine's twickneham").school_id == "SCH007"
    m = match_school(conn, "", ["we visited Chelsea Academy today"])
    assert m.school_id == "SCH009" and m.needs_review
    assert match_school(conn, "Unknown Place").school_id is None


def test_club_groups_linked(conn):
    rows = conn.execute("SELECT club_group_id FROM sessions WHERE school_id='SCH002'").fetchall()
    assert all(r[0] for r in rows)


def test_original_text_preserved(conn):
    r = conn.execute("SELECT positive_comment FROM v_sessions WHERE positive_comment LIKE '%hyper motivated%'").fetchone()
    assert "hyper motivated" in r[0] and "very good listening ability" in r[0]


def test_kpis_and_sentiment(conn):
    rows = Q.fetch_sessions(conn)
    k = Q.kpis(rows, conn)
    assert k["sessions"] == 5 and k["schools_total"] == 9 and k["schools_reporting"] == 4
    d = Q.sentiment_dist(rows)
    assert d["Positive"]["count"] == 3 and d["Mixed"]["count"] == 1 and d["Negative"]["count"] == 1


def test_pages_render(client, conn):
    for url in ["/", "/week", "/week?start=2026-09-07", "/schools", "/schools/SCH002", "/schools/SCH002/edit",
                "/schools/SCH002/handover", "/schools/SCH001", "/sessions/1", "/admin/import", "/schools/SCH009"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:300])
    assert "Sylvie Withycombe" in client.get("/schools/SCH002").text
    assert "View This Week" in client.get("/").text
    assert client.get("/schools/NOPE").status_code == 404


def test_week_page_uses_session_date(client):
    assert "Chelsea Academy" in client.get("/week").text
    assert "No feedback recorded" in client.get("/week?start=2026-08-31").text


def test_search(client):
    r = client.get("/search?q=Show Hurlingham", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/schools/SCH003"
    assert "Negative feedback" in client.get("/search?q=negative feedback this week").text
    assert "Needs attention" in client.get("/search?q=Which schools need attention?").text


class FakeModel(SentimentModel):
    name = "fake"
    version = "fake-v1"

    def predict(self, texts):
        return [SentimentResult("Positive", 0.9 if "motivated" in t else 0.4) for t in texts]


def test_ai_layer_is_separate_and_versioned(conn):
    before = conn.execute("SELECT positive_comment FROM comments ORDER BY session_id").fetchall()
    res = run_analysis(conn, FakeModel())
    assert res["analysed"] > 0 and res["model_version"] == "fake-v1"
    assert [tuple(r) for r in before] == [tuple(r) for r in
        conn.execute("SELECT positive_comment FROM comments ORDER BY session_id")]
    assert conn.execute("SELECT COUNT(*) FROM ai_analysis WHERE model_version IS NULL").fetchone()[0] == 0
    assert run_analysis(conn, FakeModel())["analysed"] == 0      # no duplicates
    assert Q.ai_dist(conn, Q.fetch_sessions(conn))["n"] == 5


def test_pending_flow(client, conn):
    csv_text = ("Timestamp,Teacher ,Which School did you attend,  Date of session  ,Activity / topic  \n"
                "20/09/2026 10:00:00,Parth Sinha,Zebra Lane Primary,18/09/2026,Logic Lab\n")
    r = client.post("/admin/import", files={"file": ("x.csv", io.BytesIO(csv_text.encode()), "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303
    p = conn.execute("SELECT pending_id FROM pending_feedback WHERE resolved=0").fetchone()
    assert p
    r = client.post(f"/admin/pending/{p[0]}/new-school", data={"name": "Zebra Lane Primary"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE school_id='SCH010'").fetchone()[0] == 1


def test_edit_versions_guidelines(client, conn):
    data = {"name": "Brighton College Prep Kensington", "edited_by": "Test", "g_parking": "Street only.",
            "c_name_0": "Sylvie Withycombe", "c_phone_0": "+44 (0) 20 7591 4622", "c_name_2": "Alex DSL",
            "group_id": ["1", "2"], "g_year": ["Years 1–2", "Year 5 and above"], "g_start": ["15:15", "16:00"],
            "g_end": ["16:00", "17:00"], "g_onsite": ["15:00", "15:45"], "g_room": ["Building 13", "Building 13"],
            "g_students": ["14", "7"], "aliases": "brighton prep"}
    # carry over the other guidelines untouched
    for key, val in Q.current_guidelines(conn, "SCH002").items():
        data.setdefault(f"g_{key}", val)
    r = client.post("/schools/SCH002/edit", data=data, follow_redirects=False)
    assert r.status_code == 303
    versions = conn.execute("SELECT version, valid_to FROM school_guidelines WHERE school_id='SCH002' "
                            "AND section='parking' ORDER BY version").fetchall()
    assert [v[0] for v in versions] == [1, 2] and versions[0][1] is not None and versions[1][1] is None
    assert conn.execute("SELECT club_group_id FROM sessions WHERE session_id=1").fetchone()[0] == 1   # history kept
    assert "Safeguarding lead (DSL) not recorded" not in client.get("/schools/SCH002").text
