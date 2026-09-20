"""Deterministic school identification from keywords. No AI involved.

The school field is matched first. If that finds nothing, the rest of the row's
text is searched and the result is flagged for review. Ties are never guessed.
"""
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional


def norm(text) -> str:
    t = (text or "").lower().replace("’", "'").replace("'", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Match:
    school_id: Optional[str]
    method: str                 # exact | keyword | text | none | ambiguous
    needs_review: bool = False
    candidates: tuple = ()


def _aliases(conn: sqlite3.Connection):
    return [(r["alias"], r["school_id"]) for r in conn.execute(
        "SELECT a.alias, a.school_id FROM school_aliases a JOIN schools s USING (school_id) "
        "WHERE s.active = 1")]


def _hits(text: str, aliases) -> dict:
    padded = f" {norm(text)} "
    hits: dict[str, int] = {}
    for alias, sid in aliases:
        if f" {alias} " in padded:
            hits[sid] = max(hits.get(sid, 0), len(alias))
    return hits


def match_school(conn: sqlite3.Connection, school_text, other_texts=()) -> Match:
    aliases = _aliases(conn)
    n = norm(school_text)
    if n:
        exact = [r["school_id"] for r in conn.execute("SELECT school_id, name FROM schools")
                 if norm(r["name"]) == n]
        if len(exact) == 1:
            return Match(exact[0], "exact")
        hits = _hits(school_text, aliases)
        if hits:
            best = max(hits.values())
            top = tuple(sid for sid, length in hits.items() if length == best)
            if len(top) == 1:
                return Match(top[0], "keyword")
            return Match(None, "ambiguous", True, top)
    # nothing in the school field: search the remaining text, but only accept a single school
    hits = _hits(" ".join(t for t in other_texts if t), aliases)
    if len(hits) == 1:
        return Match(next(iter(hits)), "text", needs_review=True)
    if len(hits) > 1:
        return Match(None, "ambiguous", True, tuple(hits))
    return Match(None, "none", True)


def find_school_in_query(conn: sqlite3.Connection, query: str) -> Optional[str]:
    """Used by search: the school whose longest alias appears in the query, if unique."""
    hits = _hits(query, _aliases(conn))
    if not hits:
        return None
    best = max(hits.values())
    top = [sid for sid, length in hits.items() if length == best]
    return top[0] if len(top) == 1 else None
