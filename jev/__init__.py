"""Jev — the fast "System One" split-second decision engine for FinGuard.ai.

Exposes a thin client over OpenRouter that answers three primitive question
types used throughout the FinOps control loop:

    * Choice  — pick one option from an enum        (routing / remediation target)
    * Score   — an ordered scale (e.g. 1..5)        (waste severity)
    * Noul    — a yes/no boolean                     (urgent? policy violated?)

Each answer carries a confidence (0..1) that the control loop uses to decide
whether to auto-remediate, escalate to humans, or route to a slow reasoning LLM.
"""

from .client import JevClient, JevResponse, confidence_band
from .schemas import Question, QType, ConfidenceBand

__all__ = [
    "JevClient",
    "JevResponse",
    "Question",
    "QType",
    "ConfidenceBand",
    "confidence_band",
]
