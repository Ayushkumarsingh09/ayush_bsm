"""Centralized application constants and defaults.

All magic numbers used across the application live here so that they can be
changed in exactly one place.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Chain construction defaults
# ---------------------------------------------------------------------------
DEFAULT_STRIKE_INTERVAL: float = 25.0
DEFAULT_STRIKES_EACH_SIDE: int = 28  # 28 below + ATM + 28 above = 57 strikes

# ---------------------------------------------------------------------------
# Market input defaults (UI starting values only -- fully user configurable)
# ---------------------------------------------------------------------------
DEFAULT_SPOT: float = 6137.0
DEFAULT_VOLATILITY: float = 0.185      # 18.50 %
DEFAULT_RISK_FREE_RATE: float = 0.0525  # 5.25 %
DEFAULT_DIVIDEND_YIELD: float = 0.0135  # 1.35 %

# ---------------------------------------------------------------------------
# Numerical guards
# ---------------------------------------------------------------------------
EPS_TIME: float = 1e-10       # T below this (in years) is treated as expired
EPS_SIGMA: float = 1e-10      # sigma below this is treated as zero volatility
MAX_SIGMA: float = 5.0        # 500% vol -- sanity upper bound for validation
MIN_PRICE_INPUT: float = 0.0

# ---------------------------------------------------------------------------
# Day count / display conventions
# ---------------------------------------------------------------------------
DAYS_PER_YEAR_DISPLAY: float = 365.0  # used for per-day Greek display scaling

# ---------------------------------------------------------------------------
# Validation bounds (soft warnings vs hard errors)
# ---------------------------------------------------------------------------
RATE_HARD_MIN: float = -1.0
RATE_HARD_MAX: float = 1.0
RATE_SOFT_MIN: float = -0.02
RATE_SOFT_MAX: float = 0.15
DIV_HARD_MIN: float = -0.5
DIV_HARD_MAX: float = 1.0
DIV_SOFT_MAX: float = 0.10
SIGMA_SOFT_MAX: float = 2.0   # >200% vol is suspicious for SPX

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
DEFAULT_DECIMALS: int = 4
APP_TITLE: str = "SPX BSM Options Analytics"
