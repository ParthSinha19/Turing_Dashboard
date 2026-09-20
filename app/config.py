"""Central configuration. Everything can be overridden with environment variables."""
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
TEMPLATE_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
LOGO_DIR = STATIC_DIR / "logos"
DB_PATH = Path(os.environ.get("TURING_DB", BASE_DIR / "data" / "turing.db"))
SAMPLE_CSV = BASE_DIR / "data" / "sample_feedback.csv"
FEEDBACK_FORM_URL = os.environ.get("FEEDBACK_FORM_URL", "https://forms.gle/6e6Si1yLYPsj6VGL9")

# Sentiment model: "none" (default), "hf" or "hf:<huggingface-model-id>"
SENTIMENT_MODEL = os.environ.get("SENTIMENT_MODEL", "none")
DEFAULT_HF_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
LOW_CONFIDENCE = 0.60          # AI results below this are flagged for human review
MIXED_THRESHOLD = 0.30         # both positive and negative probability >= this -> "Mixed"

HIGH_PRIORITY = 4              # priority >= this counts as high (5 = highest)
REVIEW_STALE_DAYS = 90         # school info sheet counts as stale after this many days
TIMEZONE = ZoneInfo("Europe/London")


def today() -> date:
    """Current date in London. TURING_TODAY=YYYY-MM-DD overrides it (useful for demos/tests)."""
    override = os.environ.get("TURING_TODAY")
    if override:
        return date.fromisoformat(override)
    return datetime.now(TIMEZONE).date()
