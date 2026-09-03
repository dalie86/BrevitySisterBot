from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.content import BotCopy
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
        copy: BotCopy | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.repository = repository
        self.balance_client = balance_client
        self.copy = copy or BotCopy.load(
            Path(__file__).resolve().parents[2] / "content" / "bot_copy.ru.toml"
        )
        self.timezone = ZoneInfo(timezone_name)

    def inspect(self, now: datetime) -> dict[str, Any]:
        week_start, month_start, utc_now = self._boundaries(now)
        week = self.repository.aggregate(
            "deepseek", week_start.astimezone(timezone.utc), utc_now
        )
        month = self.repository.aggregate(
            "deepseek", month_start.astimezone(timezone.utc), utc_now
        )
        balance_status, balances = self._live_balance()
        balance_available = balance_status == "available"
        previous = self.repository.previous_snapshot("deepseek", week_start)
        return {
            "provider": "deepseek",
            "as_of": utc_now.isoformat(),
            "week_start": week_start.isoformat(),
            "month_start": month_start.isoformat(),
            "week": _totals_dict(week),
            "month": _totals_dict(month),
            "balance_available": balance_available,
            "balance_status": balance_status,
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
        balance_status, balances = self._live_balance()
        selected = (
            _preferred_balance(balances) if balance_status == "available" else None
        )
        previous = self.repository.previous_snapshot("deepseek", week_start)
        report_text = _format_weekly_report(
            self.copy,
            week_start,
            totals,
            selected,
            previous,
            balance_status,
        )

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

    def _live_balance(self) -> tuple[str, tuple[LlmBalance, ...]]:
        try:
            available, balances = self.balance_client.get_balance()
        except Exception as exc:
            status = (
                "insufficient_balance"
                if getattr(exc, "status_code", None) == 402
                else "temporarily_unavailable"
            )
            return status, ()
        return ("available" if available else "temporarily_unavailable"), tuple(
            balances
        )


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
    copy: BotCopy,
    week_start: datetime,
    totals: UsageTotals,
    balance: LlmBalance | None,
    previous: BalanceSnapshot | None,
    balance_status: str,
) -> str:
    lines = [
        copy.render(
            "usage_report",
            "weekly_base",
            week_start=f"{week_start:%d.%m.%Y}",
            llm_calls=_number(totals.llm_call_count),
            repair_calls=_number(totals.repair_call_count),
            cache_hit_tokens=_number(totals.cache_hit_input_tokens),
            cache_miss_tokens=_number(totals.cache_miss_input_tokens),
            output_tokens=_number(totals.output_tokens),
            estimated_cost_usd=_usd(totals.estimated_cost_microusd),
        )
    ]
    if balance is None:
        key = (
            "insufficient_balance"
            if balance_status == "insufficient_balance"
            else "temporary_balance_failure"
        )
        lines.append(copy.value("usage_report", key))
        return "\n".join(lines)

    lines.append(
        copy.render(
            "usage_report",
            "balance",
            total_balance=f"{balance.total_balance:.2f}",
            currency=balance.currency,
        )
    )
    if previous is None:
        lines.append(copy.value("usage_report", "first_forecast"))
        return "\n".join(lines)
    if not previous.is_available:
        lines.append(copy.value("usage_report", "previous_unavailable"))
        return "\n".join(lines)
    if previous.currency != balance.currency:
        lines.append(copy.value("usage_report", "first_forecast"))
        return "\n".join(lines)

    delta = balance.total_balance - previous.total_balance
    if delta > 0:
        lines.append(
            copy.render(
                "usage_report",
                "top_up",
                delta=f"{delta:.2f}",
                currency=balance.currency,
            )
        )
        return "\n".join(lines)
    if delta == 0:
        lines.append(
            copy.render("usage_report", "unchanged", currency=balance.currency)
        )
        return "\n".join(lines)

    spend = -delta
    lines.append(
        copy.render(
            "usage_report",
            "spend_delta",
            delta=f"{spend:.2f}",
            currency=balance.currency,
        )
    )
    weeks = int(
        (balance.total_balance / spend).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    )
    lines.append(copy.render("usage_report", "runway", weeks=weeks))
    return "\n".join(lines)


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")
