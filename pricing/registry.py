"""Scalable Greek registry.

Each Greek is described by a :class:`GreekSpec` carrying its mathematical
definition, formula, units, display convention transform and help text.
The UI, export and documentation layers are all driven from this registry,
so adding a new Greek is a single-entry change plus its pricing function
and test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GreekSpec:
    key: str                 # column key, e.g. "delta" -> call_delta/put_delta
    label: str               # display name
    order: int               # 1, 2, 3 (derivative order bucket)
    group: str               # "First Order", "Second Order", "Third Order+"
    per_side: bool           # True if call and put values differ
    definition: str          # mathematical definition, e.g. "dV/dS"
    formula: str             # human-readable BSM formula
    raw_unit: str            # unit of the internally computed value
    display_transform: str   # key into GreekConventions.factor_for
    display_unit: str        # unit of the displayed value
    help_text: str           # tooltip / guide text


GREEK_REGISTRY: list[GreekSpec] = [
    GreekSpec(
        key="delta", label="Delta", order=1, group="First Order", per_side=True,
        definition="\u2202V/\u2202S",
        formula="Call: e^(-qT) N(d1)   |   Put: e^(-qT) [N(d1) - 1]",
        raw_unit="per 1 index point of spot", display_transform="none",
        display_unit="per 1 index point of spot",
        help_text=("Approximate option-value change for a 1-point move in the "
                   "underlying. Call delta in (0, e^(-qT)), put delta in "
                   "(-e^(-qT), 0)."),
    ),
    GreekSpec(
        key="gamma", label="Gamma", order=2, group="Second Order", per_side=False,
        definition="\u2202\u00b2V/\u2202S\u00b2",
        formula="e^(-qT) \u03c6(d1) / (S \u03c3 \u221aT)",
        raw_unit="delta change per 1 index point", display_transform="none",
        display_unit="delta change per 1 index point",
        help_text=("Rate of change of delta per 1-point move in the underlying. "
                   "Identical for calls and puts; peaks near ATM."),
    ),
    GreekSpec(
        key="vega", label="Vega", order=1, group="First Order", per_side=False,
        definition="\u2202V/\u2202\u03c3",
        formula="S e^(-qT) \u03c6(d1) \u221aT",
        raw_unit="per 1.00 change in \u03c3 (100 vol pts)",
        display_transform="per_pct",
        display_unit="per +1 volatility percentage point",
        help_text=("Option-value change for a +1 percentage-point change in "
                   "implied volatility (e.g. 18.5% -> 19.5%), other inputs "
                   "held constant. Identical for calls and puts."),
    ),
    GreekSpec(
        key="theta", label="Theta", order=1, group="First Order", per_side=True,
        definition="\u2202V/\u2202t (calendar time)",
        formula=("-S e^(-qT) \u03c6(d1)\u03c3/(2\u221aT) "
                 "\u00b1 dividend and rate carry terms"),
        raw_unit="per year", display_transform="per_day",
        display_unit="per calendar day (annual / 365)",
        help_text=("Expected option-value change as one calendar day passes "
                   "with all other inputs unchanged. Displayed per day "
                   "(annual theta / 365)."),
    ),
    GreekSpec(
        key="rho", label="Rho", order=1, group="First Order", per_side=True,
        definition="\u2202V/\u2202r",
        formula="Call: K T e^(-rT) N(d2)   |   Put: -K T e^(-rT) N(-d2)",
        raw_unit="per 1.00 change in r (100 pct pts)",
        display_transform="per_pct",
        display_unit="per +1 percentage point of rate",
        help_text=("Option-value change for a +1 percentage-point change in "
                   "the risk-free rate (e.g. 5.25% -> 6.25%)."),
    ),
    GreekSpec(
        key="vanna", label="Vanna", order=2, group="Second Order", per_side=False,
        definition="\u2202\u00b2V/\u2202S\u2202\u03c3 = \u2202\u0394/\u2202\u03c3",
        formula="-e^(-qT) \u03c6(d1) d2 / \u03c3",
        raw_unit="delta change per 1.00 of \u03c3", display_transform="per_pct",
        display_unit="delta change per +1 vol percentage point",
        help_text=("Change in delta for a +1 percentage-point change in "
                   "volatility (equivalently, change in raw vega per 1-point "
                   "spot move). Identical for calls and puts."),
    ),
    GreekSpec(
        key="volga", label="Volga", order=2, group="Second Order", per_side=False,
        definition="\u2202\u00b2V/\u2202\u03c3\u00b2 (Vomma)",
        formula="Vega \u00b7 d1 d2 / \u03c3",
        raw_unit="per (1.00 of \u03c3)\u00b2", display_transform="per_pct2",
        display_unit="value change per (1 vol pct pt)\u00b2",
        help_text=("Convexity of option value in volatility: change of "
                   "displayed vega per +1 vol percentage point. Identical for "
                   "calls and puts."),
    ),
    GreekSpec(
        key="charm", label="Charm", order=3, group="Third Order+", per_side=True,
        definition="\u2202\u0394/\u2202t (calendar time)",
        formula=("q e^(-qT) N(\u00b1d1) - e^(-qT) \u03c6(d1) "
                 "[2(r-q)T - d2\u03c3\u221aT] / (2T\u03c3\u221aT)"),
        raw_unit="delta change per year", display_transform="per_day",
        display_unit="delta change per calendar day",
        help_text=("Delta decay: expected change in delta as one calendar day "
                   "passes with other inputs unchanged (annual / 365)."),
    ),
    GreekSpec(
        key="speed", label="Speed", order=3, group="Third Order+", per_side=False,
        definition="\u2202\u00b3V/\u2202S\u00b3 = \u2202\u0393/\u2202S",
        formula="-(\u0393/S) (d1/(\u03c3\u221aT) + 1)",
        raw_unit="gamma change per 1 index point", display_transform="none",
        display_unit="gamma change per 1 index point",
        help_text=("Rate of change of gamma with respect to spot. Identical "
                   "for calls and puts."),
    ),
    GreekSpec(
        key="zomma", label="Zomma", order=3, group="Third Order+", per_side=False,
        definition="\u2202\u0393/\u2202\u03c3",
        formula="\u0393 (d1 d2 - 1) / \u03c3",
        raw_unit="gamma change per 1.00 of \u03c3", display_transform="per_pct",
        display_unit="gamma change per +1 vol percentage point",
        help_text=("Change in gamma for a +1 percentage-point change in "
                   "volatility. Identical for calls and puts."),
    ),
    GreekSpec(
        key="color", label="Color", order=3, group="Third Order+", per_side=False,
        definition="\u2202\u0393/\u2202t (calendar time)",
        # Leading sign is positive for calendar-time Color (dt = -dT).
        # Many textbooks print Color as dGamma/dT with the opposite sign.
        formula=("+e^(-qT) \u03c6(d1)/(2ST\u03c3\u221aT) \u00b7 "
                 "[2qT + 1 + d1(2(r-q)T - d2\u03c3\u221aT)/(\u03c3\u221aT)]"),
        raw_unit="gamma change per year", display_transform="per_day",
        display_unit="gamma change per calendar day",
        help_text=("Gamma decay: expected change in gamma as one calendar day "
                   "passes (annual / 365). Sign matches \u2202\u0393/\u2202t "
                   "(calendar time), not \u2202\u0393/\u2202T. Identical for "
                   "calls and puts."),
    ),
    GreekSpec(
        key="ultima", label="Ultima", order=3, group="Third Order+", per_side=False,
        definition="\u2202\u00b3V/\u2202\u03c3\u00b3",
        formula="-(Vega/\u03c3\u00b2) [d1 d2 (1 - d1 d2) + d1\u00b2 + d2\u00b2]",
        raw_unit="per (1.00 of \u03c3)\u00b3", display_transform="per_pct3",
        display_unit="value change per (1 vol pct pt)\u00b3",
        help_text=("Third-order sensitivity of option value to volatility: "
                   "change of displayed volga per +1 vol percentage point. "
                   "Identical for calls and puts."),
    ),
]

GREEKS_BY_KEY: dict[str, GreekSpec] = {g.key: g for g in GREEK_REGISTRY}
GREEK_GROUPS: list[str] = ["First Order", "Second Order", "Third Order+"]


def greeks_in_group(group: str) -> list[GreekSpec]:
    return [g for g in GREEK_REGISTRY if g.group == group]
