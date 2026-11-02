"""Typed question primitives for the Jev System-One engine.

Three question types mirror the FinGuard.ai spec:
  * Choice  -> pick one from a fixed set of options
  * Score   -> an ordered integer scale (e.g. waste severity 1..5)
  * Noul    -> a yes/no boolean (urgent? policy violated?)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class QType(str, Enum):
    CHOICE = "choice"
    SCORE = "score"
    NOUL = "noul"  # yes/no boolean


class ConfidenceBand(str, Enum):
    HIGH = "high"  # auto-remediate
    MID = "mid"    # escalate to human (Slack / Jira)
    LOW = "low"    # route to slow reasoning LLM


# Confidence thresholds that drive the three-way control loop.
HIGH_CONF = 0.85
MID_CONF = 0.60


@dataclass
class Question:
    """A single structured question for Jev to answer in one pass."""

    name: str
    qtype: QType
    instruction: str
    # For CHOICE: the allowed options.
    options: Optional[List[str]] = None
    # For SCORE: inclusive (min, max) integer bounds.
    scale: Optional[Tuple[int, int]] = None
    # Optional human-readable meaning for each score point (for UI / prompt).
    scale_labels: Dict[int, str] = field(default_factory=dict)

    def value_json_schema(self) -> Dict[str, Any]:
        """JSON-schema fragment describing the `value` of the answer."""
        if self.qtype is QType.CHOICE:
            if not self.options:
                raise ValueError(f"Choice question '{self.name}' needs options")
            return {"type": "string", "enum": list(self.options)}
        if self.qtype is QType.SCORE:
            lo, hi = self.scale or (1, 5)
            return {"type": "integer", "enum": list(range(lo, hi + 1))}
        if self.qtype is QType.NOUL:
            return {"type": "boolean"}
        raise ValueError(f"Unknown qtype {self.qtype}")

    def describe(self) -> str:
        """One-line description injected into the Jev prompt."""
        if self.qtype is QType.CHOICE:
            return f"[{self.name}] CHOICE one of {self.options}: {self.instruction}"
        if self.qtype is QType.SCORE:
            lo, hi = self.scale or (1, 5)
            labels = "; ".join(f"{k}={v}" for k, v in sorted(self.scale_labels.items()))
            lab = f" ({labels})" if labels else ""
            return f"[{self.name}] SCORE integer {lo}..{hi}{lab}: {self.instruction}"
        return f"[{self.name}] NOUL yes/no boolean: {self.instruction}"


def decision_json_schema(questions: List["Question"]) -> Dict[str, Any]:
    """Strict JSON schema shared by the Jev and OpenAI clients."""
    props: Dict[str, Any] = {}
    for q in questions:
        props[q.name] = {
            "type": "object",
            "properties": {
                "value": q.value_json_schema(),
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": ["value", "confidence", "rationale"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": props,
        "required": [q.name for q in questions],
        "additionalProperties": False,
    }
