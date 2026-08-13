"""Centralized Greek display-convention configuration.

All Greeks are computed internally in *raw mathematical units*:

* Prices and Greeks are in index points (SPX points, i.e. dollars for a
  notional multiplier of 1).
* Volatility derivatives are per *unit* of volatility (sigma = 1.00 means
  100 volatility points).
* Time derivatives are per *year* of calendar time.
* Rate derivatives are per *unit* of rate (r = 1.00 means 100%).

Display scaling to trader-friendly conventions is applied only at the
presentation layer, driven by this module. This guarantees a single source
of truth and prevents silently mixed conventions.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import DAYS_PER_YEAR_DISPLAY


@dataclass(frozen=True)
class GreekConventions:
    """Display conventions applied to raw (mathematical) Greek values.

    Attributes:
        theta_unit: ``per_day`` (annual theta / days_per_year) or ``per_year``.
        vega_unit: ``per_1pct_vol`` (raw x 0.01) or ``per_unit_vol``.
        rho_unit: ``per_1pct_rate`` (raw x 0.01) or ``per_unit_rate``.
        days_per_year: calendar days used for per-day conversions.
    """

    theta_unit: str = "per_day"
    vega_unit: str = "per_1pct_vol"
    rho_unit: str = "per_1pct_rate"
    days_per_year: float = DAYS_PER_YEAR_DISPLAY

    # ------------------------------------------------------------------
    # Scale factors: displayed value = raw value * factor
    # ------------------------------------------------------------------
    @property
    def theta_factor(self) -> float:
        return 1.0 / self.days_per_year if self.theta_unit == "per_day" else 1.0

    @property
    def vega_factor(self) -> float:
        return 0.01 if self.vega_unit == "per_1pct_vol" else 1.0

    @property
    def rho_factor(self) -> float:
        return 0.01 if self.rho_unit == "per_1pct_rate" else 1.0

    def factor_for(self, transform: str) -> float:
        """Return the display scale factor for a named transform.

        Transforms used by the Greek registry:
            ``none``      -- no scaling (delta, gamma, speed)
            ``per_pct``   -- per +1 volatility/rate percentage point (x0.01)
            ``per_day``   -- per calendar day (x 1/days_per_year)
            ``per_pct2``  -- second derivative in vol, per (1 pct pt)^2 (x1e-4)
            ``per_pct3``  -- third derivative in vol, per (1 pct pt)^3 (x1e-6)
        """
        if transform == "none":
            return 1.0
        if transform == "per_pct":
            return 0.01
        if transform == "per_day":
            return 1.0 / self.days_per_year
        if transform == "per_pct2":
            return 1e-4
        if transform == "per_pct3":
            return 1e-6
        raise ValueError(f"Unknown display transform: {transform!r}")


DEFAULT_CONVENTIONS = GreekConventions()
