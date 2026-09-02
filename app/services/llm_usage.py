from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP


PEAK_RANGES = ((time(1), time(4)), (time(6), time(10)))
OFF_PEAK = (Decimal("0.007"), Decimal("0.22"), Decimal("0.66"))
PEAK = (Decimal("0.014"), Decimal("0.44"), Decimal("1.32"))


def estimate_flash_cost_microusd(
    hit: int,
    miss: int,
    output: int,
    requested_at: datetime,
) -> int:
    utc_time = (
        requested_at.replace(tzinfo=timezone.utc)
        if requested_at.tzinfo is None
        else requested_at.astimezone(timezone.utc)
    ).time()
    rates = PEAK if any(start <= utc_time < end for start, end in PEAK_RANGES) else OFF_PEAK
    total = Decimal(hit) * rates[0] + Decimal(miss) * rates[1] + Decimal(output) * rates[2]
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
