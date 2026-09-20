from .. import config
from .base import SentimentModel, SentimentResult  # noqa: F401


def get_model(spec: str | None = None):
    """Return a model instance, or None when no model is configured (the dashboard still works)."""
    spec = (spec or config.SENTIMENT_MODEL or "none").strip()
    if spec in ("", "none"):
        return None
    if spec == "hf" or spec.startswith("hf:"):
        from .hf import HuggingFaceSentiment
        return HuggingFaceSentiment(spec[3:]) if spec.startswith("hf:") else HuggingFaceSentiment()
    raise ValueError(f"Unknown SENTIMENT_MODEL '{spec}'. Use none, hf, or hf:<model-id>.")
