"""Pretrained Hugging Face baseline (Phase 2). Imported lazily so the dashboard runs without it."""
from .. import config
from .base import SentimentModel, SentimentResult


class HuggingFaceSentiment(SentimentModel):
    def __init__(self, model_id: str = config.DEFAULT_HF_MODEL, revision: str | None = None):
        try:
            from transformers import pipeline
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install 'transformers' and 'torch' to use the Hugging Face model.") from exc
        self.name = model_id
        self._revision = revision or "main"
        self._pipe = pipeline("text-classification", model=model_id, revision=revision,
                              top_k=None, truncation=True, max_length=256)

    @property
    def version(self) -> str:
        return f"{self.name}@{self._revision}"

    def predict(self, texts):
        results = []
        for scores in self._pipe(list(texts)):
            p = {s["label"].lower(): float(s["score"]) for s in scores}
            pos, neg, neu = p.get("positive", 0.0), p.get("negative", 0.0), p.get("neutral", 0.0)
            # A pretrained 3-class model has no "mixed" class. Treat clear positive AND negative
            # signal in the same text as Mixed; the rule is deliberately simple and visible here.
            if min(pos, neg) >= config.MIXED_THRESHOLD:
                label, conf = "Mixed", min(1.0, 2 * min(pos, neg))
            else:
                label = max((("Positive", pos), ("Negative", neg), ("Neutral", neu)), key=lambda x: x[1])[0]
                conf = max(pos, neg, neu)
            results.append(SentimentResult(label, round(conf, 4), {"positive": pos, "neutral": neu, "negative": neg}))
        return results
