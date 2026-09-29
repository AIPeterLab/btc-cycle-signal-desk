#!/usr/bin/env python3
"""Backtest the experimental lagged IBIT-flow pure gate for BTC and BITX."""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
THE_BLOCK_IBIT_FLOW_URL = (
    "https://data.tbstat.com/dashboard/"
    "markets_structuredproducts_btcspotetfflows_daily_other.json"
)
INITIAL_CAPITAL = 1_000.0
FIRST_SIGNAL_MONTH = "2024-02"
RULE_TEXT = (
    "For each calendar month, use the prior completed UTC calendar month's IBIT "
    "net USD flow. A negative flow sets exposure to 0% and holds non-interest-bearing "
    "cash; a zero or positive flow sets exposure to 100%. Rebalance at month start."
)


def fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "BTC Cycle Signal Desk/1.0 "
                "(+https://github.com/AIPeterLab/btc-cycle-signal-desk)"
            )
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def yahoo_daily(symbol: str) -> list[dict[str, Any]]:
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
    params = urlencode(
        {
            "period1": start,
            "period2": end,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    payload = fetch_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}"
    )
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
    rows: list[dict[str, Any]] = []

    for index, stamp in enumerate(timestamps):
        close = parse_float(adjusted[index]) if index < len(adjusted) else None
        if close is None and index < len(quote.get("close", [])):
            close = parse_float(quote["close"][index])
        if close is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(stamp, tz=timezone.utc).date(),
                "close": close,
            }
        )
    if not rows:
        raise RuntimeError(f"Yahoo returned no usable {symbol} daily rows.")
    return rows


def ibit_monthly_flows() -> tuple[dict[str, float], int | None]:
    payload = fetch_json(THE_BLOCK_IBIT_FLOW_URL)
    series = payload.get("Series", {}).get("IBIT", {}).get("Data", [])
    monthly: dict[str, float] = defaultdict(float)
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
        monthly[flow_date.strftime("%Y-%m")] += flow
    if not monthly or min(monthly) > "2024-01":
        raise RuntimeError("The Block returned incomplete IBIT flow history.")
    runtime = payload.get("Runtime")
    return dict(monthly), int(runtime) if runtime is not None else None


def month_end_prices(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    monthly: dict[str, dict[str, Any]] = {}
    for row in rows:
        month = row["date"].strftime("%Y-%m")
        if month not in monthly or row["date"] > monthly[month]["date"]:
            monthly[month] = row
    return monthly


def previous_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year - 1:04d}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"


def backtest_asset(
    symbol: str,
    monthly_flows: dict[str, float],
    price_rows: list[dict[str, Any]],
    source_runtime: int | None,
) -> dict[str, Any]:
    prices = month_end_prices(price_rows)
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    if "2024-01" not in prices:
        raise RuntimeError(f"{symbol} January 2024 starting price is unavailable.")

    months = [
        month
        for month in sorted(prices)
        if FIRST_SIGNAL_MONTH <= month <= current_month
        and previous_month(month) in monthly_flows
    ]
    if not months:
        raise RuntimeError(f"No {symbol} pure-gate months are available.")

    start_price = float(prices["2024-01"]["close"])
    prior_price = start_price
    gate_value = INITIAL_CAPITAL
    rows: list[dict[str, Any]] = []
    ytd_gate_value = INITIAL_CAPITAL
    ytd_buy_hold_start: float | None = None

    for month in months:
        flow_month = previous_month(month)
        prior_flow = monthly_flows[flow_month]
        allocation_pct = 0 if prior_flow < 0 else 100
        asset_close = float(prices[month]["close"])
        asset_return = asset_close / prior_price - 1
        strategy_return = (allocation_pct / 100) * asset_return
        gate_value *= 1 + strategy_return

        if month == "2026-01":
            ytd_buy_hold_start = prior_price
        if month >= "2026-01":
            ytd_gate_value *= 1 + strategy_return

        is_complete = month < current_month
        rows.append(
            {
                "month": month,
                "is_complete": is_complete,
                "signal_flow_month": flow_month,
                "prior_month_ibit_flow_usd": round(prior_flow, 2),
                "signal": "OUT" if prior_flow < 0 else "IN",
                "allocation_pct": allocation_pct,
                "price_date": prices[month]["date"].isoformat(),
                "adjusted_close": round(asset_close, 6),
                "asset_month_return_pct": round(asset_return * 100, 6),
                "strategy_month_return_pct": round(strategy_return * 100, 6),
                "strategy_value": round(gate_value, 2),
                "buy_hold_value": round(
                    INITIAL_CAPITAL * asset_close / start_price, 2
                ),
                "ytd_strategy_value": (
                    round(ytd_gate_value, 2) if month >= "2026-01" else None
                ),
            }
        )
        prior_price = asset_close

    completed_rows = [row for row in rows if row["is_complete"]]
    if not completed_rows:
        raise RuntimeError(f"No completed {symbol} rows are available.")
    completed = completed_rows[-1]
    current = rows[-1]
    ytd_rows = [row for row in rows if row["month"] >= "2026-01"]
    if ytd_buy_hold_start is None or not ytd_rows:
        raise RuntimeError(f"No 2026 {symbol} rows are available.")

    current_price = float(current["adjusted_close"])
    ytd_buy_hold_value = INITIAL_CAPITAL * current_price / ytd_buy_hold_start
    completed_price = float(completed["adjusted_close"])
    completed_gate_value = float(completed["strategy_value"])
    completed_buy_hold_value = INITIAL_CAPITAL * completed_price / start_price

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_updated_at": (
            datetime.fromtimestamp(source_runtime, tz=timezone.utc).isoformat()
            if source_runtime is not None
            else None
        ),
        "strategy_name": "Experimental IBIT Flow Pure Gate (paper)",
        "exposure_asset": symbol,
        "initial_capital_usd": INITIAL_CAPITAL,
        "rule": RULE_TEXT,
        "assumptions": {
            "flow_source": "The Block BTC Spot ETF Flows, Series.IBIT.Data",
            "flow_source_url": THE_BLOCK_IBIT_FLOW_URL,
            "flow_calendar": "UTC calendar month",
            "price_source": f"Yahoo Finance {symbol} daily chart",
            "price_field": "Adjusted close, falling back to close",
            "rebalance": "Month start",
            "cash_return": 0,
            "fees_taxes_slippage": 0,
            "incomplete_current_month_in_backtest_totals": False,
        },
        "start": {
            "month_end": "2024-01",
            "price_date": prices["2024-01"]["date"].isoformat(),
            "adjusted_close": round(start_price, 6),
        },
        "completed_month_backtest": {
            "end_month": completed["month"],
            "price_date": completed["price_date"],
            "strategy_ending_value": round(completed_gate_value, 2),
            "strategy_return_pct": round(
                (completed_gate_value / INITIAL_CAPITAL - 1) * 100, 2
            ),
            "buy_hold_ending_value": round(completed_buy_hold_value, 2),
            "buy_hold_return_pct": round(
                (completed_buy_hold_value / INITIAL_CAPITAL - 1) * 100, 2
            ),
        },
        "current_paper_snapshot": {
            "month": current["month"],
            "price_date": current["price_date"],
            "signal": current["signal"],
            "allocation_pct": current["allocation_pct"],
            "signal_flow_month": current["signal_flow_month"],
            "prior_month_ibit_flow_usd": current["prior_month_ibit_flow_usd"],
            "strategy_value": current["strategy_value"],
            "strategy_return_pct": round(
                (float(current["strategy_value"]) / INITIAL_CAPITAL - 1) * 100, 2
            ),
            "buy_hold_value": current["buy_hold_value"],
            "buy_hold_return_pct": round(
                (float(current["buy_hold_value"]) / INITIAL_CAPITAL - 1) * 100, 2
            ),
        },
        "ytd_2026_current_snapshot": {
            "start_month_end": "2025-12",
            "start_adjusted_close": round(ytd_buy_hold_start, 6),
            "end_month": current["month"],
            "price_date": current["price_date"],
            "strategy_value": current["ytd_strategy_value"],
            "strategy_return_pct": round(
                (float(current["ytd_strategy_value"]) / INITIAL_CAPITAL - 1) * 100,
                2,
            ),
            "buy_hold_value": round(ytd_buy_hold_value, 2),
            "buy_hold_return_pct": round(
                (ytd_buy_hold_value / INITIAL_CAPITAL - 1) * 100, 2
            ),
        },
        "months": rows,
    }


def write_result(symbol: str, result: dict[str, Any]) -> None:
    slug = symbol.lower().replace("-usd", "")
    json_path = DATA_DIR / f"pure_gate_{slug}.json"
    csv_path = DATA_DIR / f"pure_gate_{slug}.csv"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    fieldnames = list(result["months"][0])
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(result["months"])


def build_all() -> dict[str, dict[str, Any]]:
    flows, source_runtime = ibit_monthly_flows()
    return {
        symbol: backtest_asset(symbol, flows, yahoo_daily(symbol), source_runtime)
        for symbol in ("BTC-USD", "BITX")
    }


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results = build_all()
    for symbol, result in results.items():
        write_result(symbol, result)
        snapshot = result["current_paper_snapshot"]
        ytd = result["ytd_2026_current_snapshot"]
        print(
            f"{symbol}: {snapshot['signal']} flow=${snapshot['prior_month_ibit_flow_usd']:,.1f} "
            f"all_time=${snapshot['strategy_value']:,.2f} "
            f"ytd=${ytd['strategy_value']:,.2f} as_of={snapshot['price_date']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
