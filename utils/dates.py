"""Time-to-expiry computation with explicit day-count conventions.

Convention used by the initial implementation (documented in the UI):

    T = remaining_seconds / (days_per_year * 24 * 3600)

with ``days_per_year = 365`` (ACT/365F). ACT/360 is also available. The
computation is done on full timestamps (date + time), so same-day expiries
with hours remaining produce a small positive T rather than zero, and
fractional days are handled naturally.

Timezone handling: if both timestamps are naive they are compared as-is
(assumed same zone). If one is aware and the other naive, the naive one is
assumed to be in the aware one's zone.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum


class DayCount(Enum):
    ACT_365 = "ACT/365"
    ACT_360 = "ACT/360"

    @property
    def days_per_year(self) -> float:
        return 365.0 if self is DayCount.ACT_365 else 360.0


def _align_tz(expiry: datetime, now: datetime) -> tuple[datetime, datetime]:
    e_aware = expiry.tzinfo is not None
    n_aware = now.tzinfo is not None
    if e_aware and not n_aware:
        now = now.replace(tzinfo=expiry.tzinfo)
    elif n_aware and not e_aware:
        expiry = expiry.replace(tzinfo=now.tzinfo)
    return expiry, now


def time_to_expiry(expiry: datetime, now: datetime | None = None,
                   convention: DayCount = DayCount.ACT_365) -> float:
    """Return T in years. Negative if the expiry has passed.

    The caller decides how to treat T <= 0 (the pricing engine treats it
    as expired and prices intrinsic value).
    """
    if now is None:
        now = datetime.now()
    expiry, now = _align_tz(expiry, now)
    remaining_seconds = (expiry - now).total_seconds()
    return remaining_seconds / (convention.days_per_year * 24.0 * 3600.0)


def days_to_expiry(expiry: datetime, now: datetime | None = None) -> float:
    """Calendar days to expiry (fractional). Negative if expired."""
    if now is None:
        now = datetime.now()
    expiry, now = _align_tz(expiry, now)
    return (expiry - now).total_seconds() / 86400.0
