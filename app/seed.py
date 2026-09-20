"""Initial school records. Names are the official names supplied by Turing Education.
Add further schools from the dashboard (Schools > Add school) as their feedback arrives."""
from . import config
from .school_match import norm

# (official name, short name, keywords used to recognise the school in feedback)
SCHOOLS = [
    ("Holy Trinity Sunningdale", "Holy Trinity",
     ["holy trinity sunningdale", "holy trinity", "sunningdale"]),
    ("Brighton College Prep Kensington", "Brighton Prep",
     ["brighton college prep kensington", "brighton college prep", "brighton prep school",
      "brighton prep", "brighton college", "brighton"]),
    ("Hurlingham School", "Hurlingham",
     ["hurlingham school", "hurlingham", "hurlingam school", "hurlingam"]),
    ("Donhead Primary", "Donhead", ["donhead primary", "donhead"]),
    ("Beechwood Park School", "Beechwood Park",
     ["beechwood park school", "beechwood park", "beechwood"]),
    ("Norfolk House School", "Norfolk House",
     ["norfolk house school", "norfolk house", "norfolk"]),
    ("St Catherine's Twickenham", "St Catherine's",
     ["st catherines twickenham", "st catherines school", "st catherines", "st catherine",
      "saint catherines", "catherines", "twickenham", "twickneham"]),
    ("ICS Primary School", "ICS Primary", ["ics primary school", "ics primary", "ics"]),
    ("Chelsea Academy", "Chelsea Academy", ["chelsea academy", "chelsea"]),
]

BRIGHTON = {
    "address": "10-13 Prince's Gardens, London SW7 1ND",
    "postcode": "SW7 1ND",
    "club_days": "Monday",
    "reviewed_by": "Parth Sinha",
    "reviewed_at": "2026-09-19",
    "groups": [
        ("Years 1–2", 1, 2, "15:15", "16:00", "15:00", "Student Classroom, Building 13", 14),
        ("Year 5 and above", 5, None, "16:00", "17:00", "16:00",
         "Student Classroom, Building 13 (same classroom)", 7),
    ],
    "contacts": [
        ("Main club contact", "Sylvie Withycombe", "+44 (0) 20 7591 4622", 0),
        ("Turing teacher", "Parth Sinha", "", 1),
        ("Turing: incident emails", "Aakash", "", 1),
    ],
    "guidelines": {
        "concern": "Nearest teacher available, or reception.",
        "fire": "Stairs right next to the classroom.",
        "entrance": "Building 10 for reception, Building 13 for classes.",
        "sign_in": "Reception.",
        "door_codes": "Not required.",
        "parking": "Not available.",
        "sign_out": "Reception.",
        "arrival_children": "Children arrive by themselves.",
        "register": "Taken in the classroom; hand it back at reception.",
        "collection_point": "Exit of Building 13, downstairs from the classrooms.",
        "late_collection": "Not really a problem.",
        "child_missing": ("Notify the nearest intercom (in the cabin next door). If unavailable, "
                          "tell any teacher in sight."),
        "wifi_network": "None given.",
        "wifi_password": "None given.",
        "laptops": "In a classroom on the ground floor of Building 13.",
        "logins": "All students can sign in with their own IDs.",
        "screen": "Not yet sorted.",
        "kit": "None yet.",
        "blocked_sites": "All sites available for now (whatever we need).",
        "top_do": (
            "Reception is in Building 10. After a few visits, direct entry to Building 13 is also possible.\n"
            "Ice packs are available in the classroom for injuries. Tell reception immediately and email "
            "Aakash to keep Turing in the loop.\n"
            "One teacher goes downstairs to send the students off; the other stays for the next group. "
            "All students arrive by themselves."),
        "watch_out": (
            "The school lacks devices for the whole class. Until that is sorted, Years 1–2 have to team up.\n"
            "Enter for reception through Building 10."),
        "commentary": (
            "Chill classes with bright kids.\n"
            "Years 1–2 might need you to keep control. Other sessions should be smooth sailing.\n"
            "The iPad/laptop problem should be sorted eventually."),
    },
}


def seed_schools(conn) -> None:
    if conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0]:
        return
    for i, (name, short, keywords) in enumerate(SCHOOLS, start=1):
        sid = f"SCH{i:03d}"
        conn.execute("INSERT INTO schools (school_id, name, short_name) VALUES (?,?,?)",
                     (sid, name, short))
        for kw in {norm(name), *(norm(k) for k in keywords)}:
            conn.execute("INSERT OR IGNORE INTO school_aliases (alias, school_id, source) "
                         "VALUES (?,?, 'seed')", (kw, sid))
    _seed_brighton(conn)


def _seed_brighton(conn) -> None:
    sid = conn.execute("SELECT school_id FROM schools WHERE name = ?",
                       ("Brighton College Prep Kensington",)).fetchone()[0]
    b = BRIGHTON
    conn.execute("UPDATE schools SET address=?, postcode=?, club_days=?, reviewed_by=?, reviewed_at=? "
                 "WHERE school_id=?", (b["address"], b["postcode"], b["club_days"],
                                       b["reviewed_by"], b["reviewed_at"], sid))
    for order, g in enumerate(b["groups"]):
        conn.execute(
            "INSERT INTO school_club_groups (school_id, year_group_label, year_min, year_max, start_time, "
            "end_time, on_site_by, room, typical_students, sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, *g, order))
    for order, (role, name, phone, turing) in enumerate(b["contacts"]):
        conn.execute("INSERT INTO school_contacts (school_id, role, name, phone, is_turing, sort_order) "
                     "VALUES (?,?,?,?,?,?)", (sid, role, name, phone, turing, order))
    for section, content in b["guidelines"].items():
        conn.execute("INSERT INTO school_guidelines (school_id, section, content, version, valid_from, "
                     "edited_by) VALUES (?,?,?,?,?,?)",
                     (sid, section, content, 1, b["reviewed_at"], b["reviewed_by"]))
    conn.execute("INSERT INTO school_change_log (school_id, changed_at, changed_by, summary) "
                 "VALUES (?,?,?,?)", (sid, b["reviewed_at"], b["reviewed_by"],
                                      "Initial info sheet imported"))
