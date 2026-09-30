#!/usr/bin/env python3
"""Backtest the experimental prior-week IBIT-flow pure gate."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backtest_pure_gate import (
    INITIAL_CAPITAL,
    THE_BLOCK_IBIT_FLOW_URL,
    fetch_json,
    parse_float,
    yahoo_daily,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RULE_TEXT = (
    "For each Monday-starting UTC week, use the prior completed Monday-Friday "
    "week's IBIT net USD flow. A negative flow sets exposure to 0% and holds "
    "non-interest-bearing cash; a zero or positive flow sets exposure to 100%. "
    "Rebalance at week start."
)


def monday_for(day: date) -> date:
    return day - timedelta(days=day.weekday())


def ibit_weekly_flows() -> tuple[dict[date, float], int | None]:
    payload = fetch_json(THE_BLOCK_IBIT_FLOW_URL)
    series = payload.get("Series", {}).get("IBIT", {}).get("Data", [])
    weekly: dict[date, float] = defaultdict(float)
    seen_dates: set[date] = set()
    for item in series:
        stamp = item.get("Timestamp")
        flow = parse_float(item.get("Result"))
        if stamp is None or flow is None:
            continue
        flow_date = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()
        if flow_date in seen_dates:
            raise RuntimeError(f"Duplicate IBIT flow date: {flow_date}")
        seen_dates.add(flow_date)
        weekly[monday_for(flow_date)] += flow
    if not weekly:
        raise RuntimeError("The Block returned no usable IBIT weekly flow history.")
    runtime = payload.get("Runtime")
    return dict(weekly), int(runtime) if runtime is not None else None


def weekly_prices(rows: list[dict[str, Any]]) -> dict[date, dict[str, Any]]:
    prices: dict[date, dict[str, Any]] = {}
    for row in rows:
        # Match the Monday-Friday IBIT signal calendar. BTC weekend candles must
        # not silently turn a Friday-ending strategy into Sunday-to-Sunday.
        if row["date"].weekday() > 4:
            continue
        monday = monday_for(row["date"])
        if monday not in prices or row["date"] > prices[monday]["date"]:
            prices[monday] = row
    return prices


def backtest_weekly_asset(
    symbol: str,
    weekly_flows: dict[date, float],
    price_rows: list[dict[str, Any]],
    source_runtime: int | None,
) -> dict[str, Any]:
    prices = weekly_prices(price_rows)
    current_monday = monday_for(datetime.now(timezone.utc).date())
    exposure_weeks = sorted(
        week
        for week in prices
        if week >= date(2024, 1, 15)
        and week <= current_monday
        and week - timedelta(days=7) in weekly_flows
        and week - timedelta(days=7) in prices
    )
    if not exposure_weeks:
        raise RuntimeError(f"No {symbol} weekly pure-gate periods are available.")

    first_week = exposure_weeks[0]
    start_row = prices[first_week - timedelta(days=7)]
    start_price = float(start_row["close"])
    prior_price = start_price
    strategy_value = INITIAL_CAPITAL
    rows: list[dict[str, Any]] = []

    for week in exposure_weeks:
        signal_week = week - timedelta(days=7)
        prior_flow = float(weekly_flows[signal_week])
        allocation_pct = 0 if prior_flow < 0 else 100
        price_row = prices[week]
        asset_close = float(price_row["close"])
        asset_return = asset_close / prior_price - 1
        strategy_return = (allocation_pct / 100) * asset_return
        strategy_value *= 1 + strategy_return
        rows.append(
            {
                "week_start": week.isoformat(),
                "week_end": (week + timedelta(days=4)).isoformat(),
                "is_complete": week < current_monday,
                "signal_flow_week_start": signal_week.isoformat(),
                "signal_flow_week_end": (signal_week + timedelta(days=4)).isoformat(),
                "prior_week_ibit_flow_usd": round(prior_flow, 2),
                "signal": "OUT" if prior_flow < 0 else "IN",
                "allocation_pct": allocation_pct,
                "price_date": price_row["date"].isoformat(),
                "adjusted_close": round(asset_close, 6),
                "asset_week_return_pct": round(asset_return * 100, 6),
                "strategy_week_return_pct": round(strategy_return * 100, 6),
                "strategy_value": round(strategy_value, 2),
                "buy_hold_value": round(INITIAL_CAPITAL * asset_close / start_price, 2),
            }
        )
        prior_price = asset_close

    completed_rows = [row for row in rows if row["is_complete"]]
    if not completed_rows:
        raise RuntimeError(f"No completed {symbol} weekly rows are available.")
    completed = completed_rows[-1]
    current = rows[-1]

    start_2026 = max(
        (row for row in price_rows if row["date"] <= date(2025, 12, 31)),
        key=lambda row: row["date"],
    )
    ytd_start_price = float(start_2026["close"])
    ytd_value = INITIAL_CAPITAL
    ytd_prior_price = ytd_start_price
    for row in rows:
        if row["price_date"] <= start_2026["date"].isoformat():
            continue
        period_return = float(row["adjusted_close"]) / ytd_prior_price - 1
        ytd_value *= 1 + (float(row["allocation_pct"]) / 100) * period_return
        ytd_prior_price = float(row["adjusted_close"])

    current_price = float(current["adjusted_close"])
    completed_value = float(completed["strategy_value"])
    completed_price = float(completed["adjusted_close"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_updated_at": (
            datetime.fromtimestamp(source_runtime, tz=timezone.utc).isoformat()
            if source_runtime is not None
            else None
        ),
        "strategy_name": "Experimental IBIT Weekly Flow Pure Gate (paper)",
        "exposure_asset": symbol,
        "initial_capital_usd": INITIAL_CAPITAL,
        "rule": RULE_TEXT,
        "assumptions": {
            "flow_source": "The Block BTC Spot ETF Flows, Series.IBIT.Data",
            "flow_source_url": THE_BLOCK_IBIT_FLOW_URL,
            "flow_calendar": "Monday-Friday UTC week",
            "price_source": f"Yahoo Finance {symbol} daily chart",
            "price_field": "Adjusted close, falling back to close",
            "rebalance": "Week start",
            "cash_return": 0,
            "fees_taxes_slippage": 0,
            "incomplete_current_week_in_backtest_totals": False,
        },
        "start": {
            "week_end": start_row["date"].isoformat(),
            "adjusted_close": round(start_price, 6),
        },
        "completed_week_backtest": {
            "end_week": completed["week_end"],
            "price_date": completed["price_date"],
            "strategy_ending_value": round(completed_value, 2),
            "strategy_return_pct": round((completed_value / INITIAL_CAPITAL - 1) * 100, 2),
            "buy_hold_ending_value": round(INITIAL_CAPITAL * completed_price / start_price, 2),
            "buy_hold_return_pct": round((completed_price / start_price - 1) * 100, 2),
        },
        "current_paper_snapshot": {
            "week_start": current["week_start"],
            "price_date": current["price_date"],
            "signal": current["signal"],
            "allocation_pct": current["allocation_pct"],
            "signal_flow_week_start": current["signal_flow_week_start"],
            "signal_flow_week_end": current["signal_flow_week_end"],
            "prior_week_ibit_flow_usd": current["prior_week_ibit_flow_usd"],
            "strategy_value": current["strategy_value"],
            "strategy_return_pct": round((float(current["strategy_value"]) / INITIAL_CAPITAL - 1) * 100, 2),
            "buy_hold_value": current["buy_hold_value"],
            "buy_hold_return_pct": round((float(current["buy_hold_value"]) / INITIAL_CAPITAL - 1) * 100, 2),
        },
        "ytd_2026_current_snapshot": {
            "start_date": start_2026["date"].isoformat(),
            "start_adjusted_close": round(ytd_start_price, 6),
            "price_date": current["price_date"],
            "strategy_value": round(ytd_value, 2),
            "strategy_return_pct": round((ytd_value / INITIAL_CAPITAL - 1) * 100, 2),
            "buy_hold_value": round(INITIAL_CAPITAL * current_price / ytd_start_price, 2),
            "buy_hold_return_pct": round((current_price / ytd_start_price - 1) * 100, 2),
        },
        "weeks": rows,
    }


def write_result(symbol: str, result: dict[str, Any]) -> None:
    slug = symbol.lower().replace("-usd", "")
    json_path = DATA_DIR / f"pure_gate_weekly_{slug}.json"
    csv_path = DATA_DIR / f"pure_gate_weekly_{slug}.csv"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result["weeks"][0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(result["weeks"])


def build_all() -> dict[str, dict[str, Any]]:
    flows, source_runtime = ibit_weekly_flows()
    return {
        symbol: backtest_weekly_asset(symbol, flows, yahoo_daily(symbol), source_runtime)
        for symbol in ("BTC-USD", "BITX")
    }


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for symbol, result in build_all().items():
        write_result(symbol, result)
        snapshot = result["current_paper_snapshot"]
        ytd = result["ytd_2026_current_snapshot"]
        print(
            f"{symbol}: {snapshot['signal']} "
            f"flow=${snapshot['prior_week_ibit_flow_usd']:,.1f} "
            f"all_time=${snapshot['strategy_value']:,.2f} "
            f"ytd=${ytd['strategy_value']:,.2f} as_of={snapshot['price_date']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
