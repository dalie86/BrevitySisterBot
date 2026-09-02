from datetime import datetime, timezone

import pytest

from app.services.llm_usage import estimate_flash_cost_microusd


@pytest.mark.parametrize(
    ("instant", "expected_microusd"),
    [
        (datetime(2026, 7, 20, 0, 59, 59, tzinfo=timezone.utc), 220),
        (datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc), 440),
        (datetime(2026, 7, 20, 3, 59, 59, tzinfo=timezone.utc), 440),
        (datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc), 220),
        (datetime(2026, 7, 20, 5, 59, 59, tzinfo=timezone.utc), 220),
        (datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc), 440),
        (datetime(2026, 7, 20, 9, 59, 59, tzinfo=timezone.utc), 440),
        (datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc), 220),
        (datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc), 440),
    ],
)
def test_flash_cost_uses_utc_half_open_peak_ranges(
    instant: datetime, expected_microusd: int
) -> None:
    assert estimate_flash_cost_microusd(0, 1000, 0, instant) == expected_microusd


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc), (7, 220, 660)),
        (datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc), (14, 440, 1320)),
    ],
)
def test_flash_cost_applies_exact_rates_to_each_token_bucket(
    instant: datetime, expected: tuple[int, int, int]
) -> None:
    hit_cost = estimate_flash_cost_microusd(1000, 0, 0, instant)
    miss_cost = estimate_flash_cost_microusd(0, 1000, 0, instant)
    output_cost = estimate_flash_cost_microusd(0, 0, 1000, instant)

    assert (hit_cost, miss_cost, output_cost) == expected


def test_flash_cost_rounds_once_after_summing_all_buckets() -> None:
    instant = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)

    assert estimate_flash_cost_microusd(50, 1, 0, instant) == 1


def test_flash_cost_converts_aware_time_to_utc() -> None:
    instant = datetime.fromisoformat("2026-07-20T04:00:00+03:00")

    assert estimate_flash_cost_microusd(0, 1000, 0, instant) == 440
