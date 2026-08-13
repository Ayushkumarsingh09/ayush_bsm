"""Pricing-model selection for equity index vs futures-style underlyings.

Black–Scholes–Merton (continuous dividend yield q) is correct for SPX cash
options. Futures-style options (e.g. ES) are priced with Black-76, which is
exactly BSM with ``q = r`` (forward = futures price F).

Auto-detection uses contract labels such as ``ES`` / ``futures``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PricingModel(Enum):
    BSM = "BSM"          # equity index with continuous yield q
    BLACK76 = "BLACK76"  # futures options (q := r)


@dataclass(frozen=True)
class ModelChoice:
    model: PricingModel
    q_effective: float
    label: str
    rationale: str


def detect_pricing_model(contract: str | None, explicit: str | None = None,
                         ) -> PricingModel:
    if explicit:
        key = explicit.strip().upper()
        if key in {"BLACK76", "BLACK-76", "BLACK", "FUTURES", "ES"}:
            return PricingModel.BLACK76
        if key in {"BSM", "BS", "EQUITY", "SPX"}:
            return PricingModel.BSM
    text = (contract or "").upper()
    if any(tok in text for tok in ("ES ", "ES-", "ES_", "/ES", "FUT", "FUTURE")):
        return PricingModel.BLACK76
    if text.startswith("ES") and any(ch.isdigit() for ch in text):
        return PricingModel.BLACK76
    return PricingModel.BSM


def resolve_model(contract: str | None, r: float, q: float,
                  explicit: str | None = None,
                  dividend_assumed: bool = False) -> ModelChoice:
    model = detect_pricing_model(contract, explicit)
    if model is PricingModel.BLACK76:
        return ModelChoice(
            model=model,
            q_effective=float(r),
            label="Black-76 (futures)",
            rationale=(
                "Contract looks futures-style (e.g. ES). Using Black-76, "
                "equivalent to BSM with q = r so the forward equals the "
                "futures price. This is the market-standard European futures "
                "option model."
            ),
        )
    return ModelChoice(
        model=model,
        q_effective=float(q),
        label="BSM (equity index)",
        rationale=(
            "European equity-index model with continuous dividend yield q"
            + (" (q assumed 0 — provide a yield for SPX accuracy)"
               if dividend_assumed else "")
            + "."
        ),
    )
