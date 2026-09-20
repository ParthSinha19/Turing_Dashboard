"""The contract every sentiment model must satisfy.

The dashboard only ever talks to this interface, so a model can be swapped for a
fine-tuned one by adding a class and changing SENTIMENT_MODEL. Nothing else changes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

LABELS = ("Positive", "Negative", "Mixed", "Neutral")


@dataclass
class SentimentResult:
    label: str                      # one of LABELS
    confidence: float               # 0-1, confidence in `label`
    scores: dict = field(default_factory=dict)


class SentimentModel(ABC):
    name: str = "unnamed"

    @property
    @abstractmethod
    def version(self) -> str:
        """Identifies the exact model that produced a prediction (stored with every row)."""

    @abstractmethod
    def predict(self, texts: list[str]) -> list[SentimentResult]:
        ...
