"""JevClient — a thin, fast wrapper over the TypeSafe Jev Router on OpenRouter.

Jev is a "System One" model: it answers small, strongly-typed questions in a
single rapid pass and returns a confidence for each answer. FinGuard.ai uses
those confidences to decide whether to auto-remediate, escalate to a human, or
fall back to a slow reasoning LLM.

If no OpenRouter key is configured (or ``demo`` is forced) the client falls back
to a deterministic heuristic engine so the whole app still runs for a live demo.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from .schemas import (
    HIGH_CONF,
    MID_CONF,
    ConfidenceBand,
    Question,
    QType,
    decision_json_schema,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_JEV_MODEL = "typesafe/jev-router"


def confidence_band(conf: float) -> ConfidenceBand:
    """Map a 0..1 confidence to the three control-loop lanes."""
    if conf >= HIGH_CONF:
        return ConfidenceBand.HIGH
    if conf >= MID_CONF:
        return ConfidenceBand.MID
    return ConfidenceBand.LOW


@dataclass
class JevResponse:
    """Structured result of a single Jev pass over a batch of questions."""

    answers: Dict[str, Any]              # name -> value
    confidences: Dict[str, float]        # name -> 0..1
    rationales: Dict[str, str]           # name -> short text
    latency_ms: float = 0.0
    model: str = DEFAULT_JEV_MODEL
    raw: Dict[str, Any] = field(default_factory=dict)
    source: str = "jev"                  # "jev" | "demo"
    error: Optional[str] = None

    def band(self, name: str) -> ConfidenceBand:
        return confidence_band(self.confidences.get(name, 0.0))

    def overall_confidence(self) -> float:
        if not self.confidences:
            return 0.0
        return sum(self.confidences.values()) / len(self.confidences)

    def overall_band(self) -> ConfidenceBand:
        return confidence_band(self.overall_confidence())


class JevClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        timeout: int = 40,
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model or os.getenv("JEV_MODEL", DEFAULT_JEV_MODEL)
        self.reasoning_model = reasoning_model or os.getenv(
            "REASONING_MODEL", "anthropic/claude-3.5-sonnet"
        )
        self.timeout = timeout
        self.site_url = site_url or os.getenv("OPENROUTER_SITE_URL", "https://finguard.ai")
        self.app_name = app_name or os.getenv("OPENROUTER_APP_NAME", "FinGuard.ai")

        # Engine selection lives in config (.env), never in the UI.
        #   JEV_MODE = simulation | live | auto   (default: auto)
        #     simulation -> always use the deterministic demo engine
        #     live       -> always call the real Jev model via OpenRouter
        #     auto       -> live when a valid key is present, else simulation
        self.mode = (mode_arg := (os.getenv("JEV_MODE", "auto") or "auto")).strip().lower()
        if self.mode in ("sim", "simulate", "demo", "offline", "fake"):
            self.mode = "simulation"
        elif self.mode in ("real", "jev", "api", "online"):
            self.mode = "live"
        elif self.mode not in ("simulation", "live"):
            self.mode = "auto"
        _ = mode_arg  # (walrus kept for readability)

    # ------------------------------------------------------------------ #
    # Status helpers
    # ------------------------------------------------------------------ #
    def key_looks_valid(self) -> bool:
        k = (self.api_key or "").strip()
        return bool(k) and k.startswith("sk-or-") and "paste-your-key" not in k

    @property
    def simulate(self) -> bool:
        """True when the deterministic simulation engine should be used."""
        if self.mode == "simulation":
            return True
        if self.mode == "live":
            return False
        return not self.key_looks_valid()  # auto

    @property
    def live(self) -> bool:
        """True when the real Jev model will be called."""
        return not self.simulate

    @property
    def engine_label(self) -> str:
        if self.mode == "simulation":
            return "Simulation (forced via config)"
        if self.mode == "live":
            return "Live Jev (forced via config)"
        return "Live Jev (auto)" if self.live else "Simulation (auto — no key)"

    # ------------------------------------------------------------------ #
    # Core: ask a batch of typed questions in a single Jev pass
    # ------------------------------------------------------------------ #
    def ask(
        self,
        context: str,
        questions: List[Question],
        system: Optional[str] = None,
        temperature: float = 0.0,
    ) -> JevResponse:
        if not self.live:
            return self._demo(context, questions)
        try:
            return self._call_openrouter(context, questions, system, temperature)
        except Exception as exc:  # noqa: BLE001 — surface, then degrade gracefully
            demo = self._demo(context, questions)
            demo.error = f"{type(exc).__name__}: {exc}"
            demo.source = "demo"
            return demo

    # ---- Convenience single-question helpers -------------------------- #
    def choice(self, context: str, name: str, options: List[str], instruction: str) -> JevResponse:
        return self.ask(context, [Question(name, QType.CHOICE, instruction, options=options)])

    def score(self, context: str, name: str, instruction: str, scale=(1, 5), labels=None) -> JevResponse:
        return self.ask(
            context,
            [Question(name, QType.SCORE, instruction, scale=scale, scale_labels=labels or {})],
        )

    def noul(self, context: str, name: str, instruction: str) -> JevResponse:
        return self.ask(context, [Question(name, QType.NOUL, instruction)])

    # ------------------------------------------------------------------ #
    # OpenRouter call with strict structured outputs
    # ------------------------------------------------------------------ #
    def _build_schema(self, questions: List[Question]) -> Dict[str, Any]:
        return decision_json_schema(questions)

    def _call_openrouter(
        self,
        context: str,
        questions: List[Question],
        system: Optional[str],
        temperature: float,
    ) -> JevResponse:
        schema = self._build_schema(questions)
        q_lines = "\n".join(f"- {q.describe()}" for q in questions)
        sys_prompt = system or (
            "You are Jev, a split-second System-One FinOps decision engine. "
            "Answer every question strictly and concisely. For each answer set a "
            "calibrated confidence in [0,1]: use >=0.85 only when the evidence is "
            "unambiguous, 0.60-0.85 when plausible but worth human review, and "
            "<0.60 when genuinely uncertain. Keep each rationale under 25 words."
        )
        user_prompt = (
            f"CONTEXT (multi-cloud state / payload):\n{context}\n\n"
            f"Answer these questions:\n{q_lines}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "jev_decision", "strict": True, "schema": schema},
            },
        }

        t0 = time.perf_counter()
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=self.timeout)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        answers, confidences, rationales = {}, {}, {}
        for q in questions:
            block = parsed.get(q.name, {}) or {}
            answers[q.name] = block.get("value")
            confidences[q.name] = float(block.get("confidence", 0.0) or 0.0)
            rationales[q.name] = block.get("rationale", "")

        used_model = data.get("model", self.model)
        return JevResponse(
            answers=answers,
            confidences=confidences,
            rationales=rationales,
            latency_ms=latency_ms,
            model=used_model,
            raw=data,
            source="jev",
        )

    # ------------------------------------------------------------------ #
    # Slow-path: deep reasoning model for LOW-confidence cases
    # ------------------------------------------------------------------ #
    def deep_reason(self, context: str, instruction: str) -> Dict[str, Any]:
        if not self.live:
            return {
                "source": "demo",
                "text": (
                    "[demo reasoning] Jev confidence was low, so this case would be routed to the "
                    "slow reasoning model for a deeper, chain-of-thought FinOps analysis. "
                    "Configure OPENROUTER_API_KEY to run the real deep-analysis pass."
                ),
                "latency_ms": 0.0,
                "model": self.reasoning_model,
            }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
        }
        payload = {
            "model": self.reasoning_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a senior multi-cloud FinOps analyst. Give a careful, structured recommendation.",
                },
                {"role": "user", "content": f"{instruction}\n\nCONTEXT:\n{context}"},
            ],
        }
        t0 = time.perf_counter()
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=self.timeout)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        data = resp.json()
        return {
            "source": "reasoning",
            "text": data["choices"][0]["message"]["content"],
            "latency_ms": latency_ms,
            "model": data.get("model", self.reasoning_model),
        }

    # ------------------------------------------------------------------ #
    # Deterministic demo engine (runs with no API key)
    # ------------------------------------------------------------------ #
    def _demo(self, context: str, questions: List[Question]) -> JevResponse:
        t0 = time.perf_counter()
        ctx = context.lower()

        # crude signals pulled from the context text
        waste = 0.0
        for k, w in {
            "runaway": 1.0, "infinite": 1.0, "recursive": 0.9, "idle": 0.5,
            "unattached": 0.6, "available": 0.3, "encryption": 0.5,
            "0 invocations": 0.7, "0 connections": 0.7, "0 queries": 0.6,
            "no pause": 0.5, "oversized": 0.6, "spot": 0.3, "premium": 0.3,
        }.items():
            if k in ctx:
                waste += w
        # projected waste magnitude bumps severity
        for marker in ("projectedmonthlywaste", "projected_monthly_waste", "finguard"):
            if marker in ctx:
                waste += 0.4
                break
        waste = max(0.0, min(1.0, waste / 3.0))

        urgent = any(k in ctx for k in ["runaway", "infinite", "recursive", "critical"])

        answers, confidences, rationales = {}, {}, {}
        for q in questions:
            # clear-cut cases (strong runaway OR obviously trivial) -> high certainty;
            # genuinely ambiguous mid-waste resources -> low certainty (route to review / deep reason)
            clarity = abs(waste - 0.5) * 2  # 0 at ambiguous midpoint, 1 at extremes
            if q.qtype is QType.SCORE:
                lo, hi = q.scale or (1, 5)
                val = int(round(lo + waste * (hi - lo)))
                answers[q.name] = val
                confidences[q.name] = round(0.55 + 0.40 * clarity, 3)
                rationales[q.name] = "Heuristic severity from idle/runaway/encryption signals."
            elif q.qtype is QType.NOUL:
                if "urgent" in q.name.lower() or "action" in q.name.lower():
                    val = urgent or waste > 0.6
                else:
                    val = waste > 0.5
                answers[q.name] = bool(val)
                confidences[q.name] = 0.90 if (urgent or waste > 0.7 or waste < 0.15) else 0.62
                rationales[q.name] = "Heuristic boolean from runaway/idle + projected-waste signals."
            else:  # CHOICE
                opts = q.options or []
                pick = opts[0] if opts else ""
                matched = False
                for o in opts:
                    ol = o.lower()
                    if ("lambda" in ol and "lambda" in ctx) or \
                       ("ec2" in ol and ("ec2" in ctx or "instance" in ctx)) or \
                       ("vm" in ol and ("virtualmachine" in ctx or "azure" in ctx)) or \
                       ("gke" in ol and "gke" in ctx) or \
                       ("cloudrun" in ol and "cloud run" in ctx) or \
                       ("pause" in ol and ("idle" in ctx or "synapse" in ctx or "redshift" in ctx)) or \
                       ("delete" in ol and ("orphan" in ctx or "unattached" in ctx or "available" in ctx)) or \
                       ("encrypt" in ol and "encryption" in ctx) or \
                       ("lifecycle" in ol and ("cold" in ctx or "archive" in ctx)) or \
                       ("premium" in ol and waste < 0.4) or \
                       ("simple" in ol and waste < 0.3) or \
                       ("reasoning" in ol and waste > 0.7):
                        pick, matched = o, True
                        break
                answers[q.name] = pick
                confidences[q.name] = round((0.68 if matched else 0.50) + 0.22 * clarity, 3)
                rationales[q.name] = "Heuristic routing match on provider / workload keywords."

        # realistic, deterministic System-One latency (~46-106 ms) seeded by the payload
        jitter = int(hashlib.sha1(context.encode("utf-8")).hexdigest(), 16) % 60
        latency_ms = 46.0 + jitter + (time.perf_counter() - t0) * 1000.0
        return JevResponse(
            answers=answers,
            confidences=confidences,
            rationales=rationales,
            latency_ms=latency_ms,
            model=f"{self.model} (demo heuristic)",
            source="demo",
        )
