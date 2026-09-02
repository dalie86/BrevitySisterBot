from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.models import BalanceSnapshot, LlmBalance, UsageTotals
from app.integrations.deepseek import DeepSeekApiError
from app.services.usage_reports import UsageReportService


NOW = datetime(2026, 9, 6, 18, 30, tzinfo=timezone.utc)
MOSCOW = ZoneInfo("Europe/Moscow")
WEEK_START = datetime(2026, 8, 31, 0, 0, tzinfo=MOSCOW)
WEEK_START_UTC = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
MONTH_START = datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)


class FakeUsageRepository:
    def __init__(self, previous=None) -> None:
        self.previous = previous
        self.aggregate_calls = []
        self.snapshot_calls = []
        self.snapshots = {}

    def aggregate(self, provider, start, end):
        self.aggregate_calls.append((provider, start, end))
        if start == WEEK_START_UTC:
            return UsageTotals(
                llm_call_count=12,
                repair_call_count=2,
                cache_hit_input_tokens=1_200,
                cache_miss_input_tokens=3_400,
                output_tokens=560,
                estimated_cost_microusd=1_250_000,
            )
        return UsageTotals(
            llm_call_count=20,
            repair_call_count=3,
            cache_hit_input_tokens=2_000,
            cache_miss_input_tokens=5_000,
            output_tokens=800,
            estimated_cost_microusd=2_500_000,
        )

    def previous_snapshot(self, provider, currency, week_start):
        assert provider == "deepseek"
        assert week_start == WEEK_START
        return self.previous if currency == "CNY" else None

    def get_or_create_snapshot(self, snapshot):
        self.snapshot_calls.append(snapshot)
        key = (snapshot.provider, snapshot.currency, snapshot.report_week_start)
        if key not in self.snapshots:
            self.snapshots[key] = snapshot
        return self.snapshots[key]


class FakeBalanceClient:
    def __init__(
        self, total=Decimal("49"), *, fail=False, currency="CNY", error=None
    ) -> None:
        self.total = total
        self.fail = fail
        self.currency = currency
        self.error = error

    def get_balance(self):
        if self.error is not None:
            raise self.error
        if self.fail:
            raise RuntimeError("live endpoint unavailable")
        return True, (
            LlmBalance(
                currency=self.currency,
                total_balance=self.total,
                granted_balance=Decimal("0"),
                topped_up_balance=self.total,
            ),
        )


def previous(total: str) -> BalanceSnapshot:
    return BalanceSnapshot(
        provider="deepseek",
        currency="CNY",
        total_balance=Decimal(total),
        granted_balance=Decimal("0"),
        topped_up_balance=Decimal(total),
        is_available=True,
        report_week_start=datetime(2026, 8, 23, 21, 0, tzinfo=timezone.utc),
        report_text="old",
        captured_at=datetime(2026, 8, 30, 18, 30, tzinfo=timezone.utc),
    )


def test_inspect_uses_moscow_week_and_month_boundaries_and_includes_local_stats() -> None:
    repository = FakeUsageRepository()
    service = UsageReportService(repository, FakeBalanceClient())

    result = service.inspect(NOW)

    assert repository.aggregate_calls == [
        ("deepseek", WEEK_START_UTC, NOW),
        ("deepseek", MONTH_START, NOW),
    ]
    assert result["balance_available"] is True
    assert result["week"]["llm_call_count"] == 12
    assert result["month"]["estimated_cost_usd"] == "2.500000"
    assert result["balances"][0]["currency"] == "CNY"
    assert result["balances"][0]["total_balance"] == "49"
    assert result["previous_balance"] is None


def test_inspect_includes_previous_valid_balance_snapshot() -> None:
    repository = FakeUsageRepository(previous("50"))

    result = UsageReportService(repository, FakeBalanceClient()).inspect(NOW)

    assert result["previous_balance"]["currency"] == "CNY"
    assert result["previous_balance"]["total_balance"] == "50"
    assert result["previous_balance"]["is_available"] is True


def test_inspect_balance_failure_does_not_block_local_stats() -> None:
    repository = FakeUsageRepository()
    service = UsageReportService(repository, FakeBalanceClient(fail=True))

    result = service.inspect(NOW)

    assert result["balance_available"] is False
    assert result["balances"] == []
    assert result["week"]["llm_call_count"] == 12
    assert result["month"]["llm_call_count"] == 20


def test_first_weekly_report_contains_usage_balance_and_first_run_forecast() -> None:
    repository = FakeUsageRepository()
    report = UsageReportService(repository, FakeBalanceClient()).prepare_weekly(NOW)

    assert report.week_start == WEEK_START
    assert "Вызовы: 12" in report.report_text
    assert "repair: 2" in report.report_text
    assert "cache hit: 1 200" in report.report_text
    assert "cache miss: 3 400" in report.report_text
    assert "output: 560" in report.report_text
    assert "1.250000 USD" in report.report_text
    assert "49.00 CNY" in report.report_text
    assert "прогноз появится через неделю" in report.report_text


def test_weekly_spend_delta_and_runway_are_reported_from_previous_cny_snapshot() -> None:
    repository = FakeUsageRepository(previous("50"))

    report = UsageReportService(repository, FakeBalanceClient(Decimal("49"))).prepare_weekly(
        NOW
    )

    assert "−1.00 CNY" in report.report_text
    assert "примерно 49 недель" in report.report_text


def test_weekly_top_up_is_reported_without_runway() -> None:
    repository = FakeUsageRepository(previous("50"))

    report = UsageReportService(repository, FakeBalanceClient(Decimal("52"))).prepare_weekly(
        NOW
    )

    assert "пополнение" in report.report_text.lower()
    assert "+2.00 CNY" in report.report_text
    assert "недель" not in report.report_text


def test_weekly_report_prefers_cny_and_freezes_first_text_on_retry() -> None:
    repository = FakeUsageRepository()
    service = UsageReportService(repository, FakeBalanceClient(Decimal("49")))
    first = service.prepare_weekly(NOW)
    service.balance_client = FakeBalanceClient(Decimal("48"))

    retry = service.prepare_weekly(NOW)

    assert retry.report_text == first.report_text
    assert retry.balance.total_balance == Decimal("49")
    assert len(repository.snapshot_calls) == 2


def test_weekly_report_handles_non_cny_and_unavailable_balance_safely() -> None:
    non_cny = UsageReportService(
        FakeUsageRepository(), FakeBalanceClient(Decimal("10"), currency="USD")
    ).prepare_weekly(NOW)
    unavailable = UsageReportService(
        FakeUsageRepository(), FakeBalanceClient(fail=True)
    ).prepare_weekly(NOW)

    assert "10.00 USD" in non_cny.report_text
    assert "Баланс недоступен" in unavailable.report_text
    assert unavailable.balance is None


def test_unavailable_balance_is_frozen_as_unavailable_not_as_zero_balance() -> None:
    repository = FakeUsageRepository()

    report = UsageReportService(
        repository, FakeBalanceClient(fail=True)
    ).prepare_weekly(NOW)

    snapshot = repository.snapshot_calls[0]
    assert snapshot.is_available is False
    assert snapshot.total_balance == Decimal("0")
    assert snapshot.granted_balance == Decimal("0")
    assert snapshot.topped_up_balance == Decimal("0")
    assert report.balance is None


def test_immediately_previous_unavailable_snapshot_suppresses_weekly_delta() -> None:
    unavailable = BalanceSnapshot(
        provider="deepseek",
        currency="CNY",
        total_balance=Decimal("0"),
        granted_balance=Decimal("0"),
        topped_up_balance=Decimal("0"),
        is_available=False,
        report_week_start=datetime(2026, 8, 23, 21, 0, tzinfo=timezone.utc),
        report_text="unavailable",
        captured_at=datetime(2026, 8, 30, 18, 30, tzinfo=timezone.utc),
    )

    report = UsageReportService(
        FakeUsageRepository(unavailable), FakeBalanceClient(Decimal("49"))
    ).prepare_weekly(NOW)

    assert "прошлый баланс не был получен" in report.report_text
    assert "−49.00 CNY" not in report.report_text
    assert "примерно 0 недель" not in report.report_text


def test_insufficient_balance_is_distinguished_from_temporary_failure() -> None:
    error = DeepSeekApiError(
        "DeepSeek insufficient balance", status_code=402
    )

    report = UsageReportService(
        FakeUsageRepository(), FakeBalanceClient(error=error)
    ).prepare_weekly(NOW)

    assert "недостаточно средств" in report.report_text.lower()
