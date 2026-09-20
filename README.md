# Turing Education: AI Club dashboard

Internal dashboard for AI Club sessions across schools. Two kinds of data, kept separate and linked by `school_id`:

- **School info sheets** (permanent guidelines, edited by hand in the dashboard, versioned)
- **Weekly session feedback** (imported from the Google Form CSV, never edited)

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py init      # creates data/turing.db, seeds the 9 schools, imports data/sample_feedback.csv
python manage.py run       # http://127.0.0.1:8000
```

Import a newer Google Forms export from **Import** in the dashboard, or `python manage.py import FILE.csv`.
Re-importing the same file is safe: rows already present are skipped.

## How schools are identified

Deterministic keyword matching, no AI (`app/school_match.py`):

1. Exact official name, or the longest keyword found in the form's school field.
2. If the school field gives nothing, the rest of the row is searched. A single hit is accepted but marked "needs review".
3. Anything unrecognised or ambiguous waits on **Import**. You assign it to a school, or add a new school from there.
   The wording you assigned is saved as a keyword, so the same spelling matches automatically next time.

Nothing is stored without a `school_id`. Keywords for each school are editable on its edit page.

## Layout

```
app/schema.sql        relational schema (schools, sessions, feedback, comments, ai_analysis, ...)
app/normalise.py      form columns -> clean fields (the only place form headers are used)
app/importer.py       idempotent CSV import, pending queue, school creation
app/queries.py        every dashboard number, as plain database lookups
app/search.py         rule-based search: school lookup + intent words -> the same queries
app/sentiment/        swappable sentiment layer (base interface, Hugging Face model, runner)
app/templates/        pages          app/static/style.css   styling
tests/                pytest suite   manage.py              command line
```

## Sentiment: teacher first, AI second

- Percentages on every dashboard come from the teacher's selected response (Very/Mostly positive = Positive, Mixed = Mixed, Mostly/Very negative = Negative).
- AI sentiment is a separate, optional signal. Each written field is analysed on its own and stored in `ai_analysis` with `model_version`. Confidence below 0.60 is flagged for review.
- The dashboard works with no model installed.

To turn it on:

```bash
pip install transformers torch
SENTIMENT_MODEL=hf python manage.py sentiment      # CardiffNLP RoBERTa by default
SENTIMENT_MODEL=hf:distilbert-base-uncased-finetuned-sst-2-english python manage.py sentiment
```

To use your own fine-tuned model later, add a class implementing `SentimentModel` (`app/sentiment/base.py`)
and register it in `app/sentiment/__init__.py`. Nothing else changes.

## Definitions used (change in `app/config.py` / `app/queries.py`)

- **Needs attention:** Negative or Mixed response, an open action with priority 4 or 5, a partly completed or incomplete session, or any technical issue.
- **Priority:** 5 is highest; 4 and above shows as High.
- **Difficulty:** 1 too easy, 3 about right, 5 too hard. It is shown, not averaged into "quality".
- **Students reached:** total attendances across sessions.
- **This week:** Monday to Sunday of the session date, London time.
- **Info sheet stale:** not reviewed for 90 days.

## Before you host it anywhere shared

- There is **no login**. Add authentication before exposing it beyond a trusted network. Editing records a name, not an identity.
- Door codes and Wi-Fi passwords are kept off dashboards and printouts by default, but they are visible in the editor to anyone who can reach it.
- Free-text feedback is shown as written. Comments that mention ability or needs are flagged "Check wording" on the session page.
- Charts load Chart.js from a CDN; fonts from Google Fonts. Without internet, tables still show and the system font is used.
