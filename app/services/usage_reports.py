from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.models import (
    BalanceSnapshot,
    FrozenUsageReport,
    LlmBalance,
    UsageTotals,
)
from app.domain.ports import BalanceGateway, UsageRepository


MICRO_USD = Decimal("1000000")


class UsageReportService:
    def __init__(
        self,
        repository: UsageRepository,
        balance_client: BalanceGateway,
        timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.repository = repository
        self.balance_client = balance_client
        self.timezone = ZoneInfo(timezone_name)

    def inspect(self, now: datetime) -> dict[str, Any]:
        week_start, month_start, utc_now = self._boundaries(now)
        week = self.repository.aggregate(
            "deepseek", week_start.astimezone(timezone.utc), utc_now
        )
        month = self.repository.aggregate(
            "deepseek", month_start.astimezone(timezone.utc), utc_now
        )
        balance_available, balances = self._live_balance()
        selected = _preferred_balance(balances) if balance_available else None
        previous = self.repository.previous_snapshot(
            "deepseek", selected.currency if selected is not None else "CNY", week_start
        )
        return {
            "provider": "deepseek",
            "as_of": utc_now.isoformat(),
            "week_start": week_start.isoformat(),
            "month_start": month_start.isoformat(),
            "week": _totals_dict(week),
            "month": _totals_dict(month),
            "balance_available": balance_available,
            "balances": [_balance_dict(balance) for balance in balances],
            "previous_balance": (
                _snapshot_dict(previous) if previous is not None else None
            ),
        }

    def prepare_weekly(self, now: datetime) -> FrozenUsageReport:
        week_start, _month_start, utc_now = self._boundaries(now)
        totals = self.repository.aggregate(
            "deepseek", week_start.astimezone(timezone.utc), utc_now
        )
        available, balances = self._live_balance()
        selected = _preferred_balance(balances) if available else None
        previous = (
            self.repository.previous_snapshot(
                "deepseek", selected.currency, week_start
            )
            if selected is not None
            else None
        )
        report_text = _format_weekly_report(week_start, totals, selected, previous)

        zero = Decimal("0")
        snapshot = self.repository.get_or_create_snapshot(
            BalanceSnapshot(
                provider="deepseek",
                currency=selected.currency if selected is not None else "CNY",
                total_balance=(selected.total_balance if selected is not None else zero),
                granted_balance=(
                    selected.granted_balance if selected is not None else zero
                ),
                topped_up_balance=(
                    selected.topped_up_balance if selected is not None else zero
                ),
                is_available=selected is not None,
                report_week_start=week_start,
                report_text=report_text,
                captured_at=utc_now,
            )
        )
        return FrozenUsageReport(
            week_start=week_start,
            report_text=snapshot.report_text,
            balance=snapshot if snapshot.is_available else None,
        )

    def _boundaries(self, now: datetime) -> tuple[datetime, datetime, datetime]:
        utc_now = _as_utc(now)
        local_now = utc_now.astimezone(self.timezone)
        week_start = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        month_start = local_now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return week_start, month_start, utc_now

    def _live_balance(self) -> tuple[bool, tuple[LlmBalance, ...]]:
        try:
            available, balances = self.balance_client.get_balance()
        except Exception:
            return False, ()
        return bool(available), tuple(balances)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _preferred_balance(balances: tuple[LlmBalance, ...]) -> LlmBalance | None:
    if not balances:
        return None
    return next((item for item in balances if item.currency == "CNY"), balances[0])


def _totals_dict(totals: UsageTotals) -> dict[str, int | str]:
    values = asdict(totals)
    values["estimated_cost_usd"] = _usd(totals.estimated_cost_microusd)
    return values


def _balance_dict(balance: LlmBalance) -> dict[str, str]:
    return {
        "currency": balance.currency,
        "total_balance": str(balance.total_balance),
        "granted_balance": str(balance.granted_balance),
        "topped_up_balance": str(balance.topped_up_balance),
    }


def _snapshot_dict(snapshot: BalanceSnapshot) -> dict[str, str | bool]:
    return {
        "currency": snapshot.currency,
        "total_balance": str(snapshot.total_balance),
        "granted_balance": str(snapshot.granted_balance),
        "topped_up_balance": str(snapshot.topped_up_balance),
        "is_available": snapshot.is_available,
        "captured_at": snapshot.captured_at.isoformat(),
        "report_week_start": snapshot.report_week_start.isoformat(),
    }


def _usd(microusd: int) -> str:
    return format(Decimal(microusd) / MICRO_USD, ".6f")


def _format_weekly_report(
    week_start: datetime,
    totals: UsageTotals,
    balance: LlmBalance | None,
    previous: BalanceSnapshot | None,
) -> str:
    lines = [
        f"DeepSeek — неделя с {week_start:%d.%m.%Y}",
        f"Вызовы: {totals.llm_call_count:,} (repair: {totals.repair_call_count:,})",
        (
            "Токены — "
            f"cache hit: {totals.cache_hit_input_tokens:,}; "
            f"cache miss: {totals.cache_miss_input_tokens:,}; "
            f"output: {totals.output_tokens:,}"
        ),
        f"Расход по оценке: {_usd(totals.estimated_cost_microusd)} USD",
    ]
    lines = [line.replace(",", " ") for line in lines]
    if balance is None:
        lines.append("Баланс недоступен; локальная статистика сохранена.")
        return "\n".join(lines)

    lines.append(f"Баланс: {balance.total_balance:.2f} {balance.currency}")
    if previous is None:
        lines.append("прогноз появится через неделю")
        return "\n".join(lines)

    delta = balance.total_balance - previous.total_balance
    if delta > 0:
        lines.append(f"Пополнение: +{delta:.2f} {balance.currency}")
        return "\n".join(lines)
    if delta == 0:
        lines.append(f"Изменение: 0.00 {balance.currency}")
        return "\n".join(lines)

    spend = -delta
    lines.append(f"Изменение: −{spend:.2f} {balance.currency}")
    weeks = int(
        (balance.total_balance / spend).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    )
    lines.append(f"При таком расходе: примерно {weeks} недель")
    return "\n".join(lines)
