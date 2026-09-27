#!/usr/bin/env python3
"""Backtest a lagged, inverse IBIT-flow allocation against QQQ buy-and-hold."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from update_signals import THE_BLOCK_IBIT_FLOW_URL, fetch_json, parse_float


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
JSON_PATH = DATA_DIR / "ibit_flow_backtest_2026.json"
CSV_PATH = DATA_DIR / "ibit_flow_backtest_2026.csv"
INITIAL_CAPITAL = 1_000.0
TRAINING_START = "2024-01"
TRAINING_END = "2025-12"
BACKTEST_YEAR = 2026


def yahoo_daily(symbol: str) -> list[dict[str, Any]]:
    start = int(datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp())
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
        raise RuntimeError(f"Yahoo returned no usable {symbol} rows.")
    return rows


def ibit_monthly_flows() -> dict[str, float]:
    payload = fetch_json(THE_BLOCK_IBIT_FLOW_URL)
    daily = payload.get("Series", {}).get("IBIT", {}).get("Data", [])
    monthly: dict[str, float] = defaultdict(float)
    for item in daily:
        flow = parse_float(item.get("Result"))
        stamp = item.get("Timestamp")
        if flow is None or stamp is None:
            continue
        month = datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime("%Y-%m")
        monthly[month] += flow
    if not monthly:
        raise RuntimeError("The Block returned no usable IBIT flow history.")
    return dict(monthly)


def month_end_prices(rows: list[dict[str, Any]]) -> dict[str, tuple[date, float]]:
    monthly: dict[str, tuple[date, float]] = {}
    for row in rows:
        month = row["date"].strftime("%Y-%m")
        current = monthly.get(month)
        if current is None or row["date"] > current[0]:
            monthly[month] = (row["date"], float(row["close"]))
    return monthly


def previous_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    if number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{number - 1:02d}"


def main() -> int:
    flows = ibit_monthly_flows()
    training_flows = [
        flow
        for month, flow in flows.items()
        if TRAINING_START <= month <= TRAINING_END
    ]
    if not training_flows:
        raise RuntimeError("No IBIT flows were available in the training window.")
    training_max = max(training_flows)
    if training_max <= 0:
        raise RuntimeError("The training-window maximum IBIT flow must be positive.")

    btc_prices = month_end_prices(yahoo_daily("BTC-USD"))
    qqq_prices = month_end_prices(yahoo_daily("QQQ"))
    start_month = f"{BACKTEST_YEAR - 1}-12"
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    common_months = sorted(
        month
        for month in set(btc_prices) & set(qqq_prices)
        if month.startswith(f"{BACKTEST_YEAR}-") and month < current_month
    )
    if start_month not in btc_prices or start_month not in qqq_prices:
        raise RuntimeError("December 2025 starting prices are unavailable.")

    strategy_value = INITIAL_CAPITAL
    qqq_start = qqq_prices[start_month][1]
    btc_start = btc_prices[start_month][1]
    rows: list[dict[str, Any]] = []
    previous_btc = btc_start

    for month in common_months:
        prior_flow_month = previous_month(month)
        if prior_flow_month not in flows:
            continue
        prior_flow = flows[prior_flow_month]
        flow_score = min(max(prior_flow / training_max * 100, 0), 100)
        btc_allocation_pct = 0 if prior_flow < 0 else 100 - flow_score
        btc_close = btc_prices[month][1]
        btc_return = btc_close / previous_btc - 1
        strategy_return = (btc_allocation_pct / 100) * btc_return
        strategy_value *= 1 + strategy_return
        qqq_value = INITIAL_CAPITAL * qqq_prices[month][1] / qqq_start
        btc_buy_hold_value = INITIAL_CAPITAL * btc_close / btc_start
        rows.append(
            {
                "month": month,
                "signal_flow_month": prior_flow_month,
                "prior_month_ibit_flow_usd": round(prior_flow, 2),
                "flow_score": round(flow_score, 4),
                "btc_allocation_pct": round(btc_allocation_pct, 4),
                "btc_monthly_return_pct": round(btc_return * 100, 4),
                "strategy_monthly_return_pct": round(strategy_return * 100, 4),
                "strategy_value": round(strategy_value, 2),
                "qqq_value": round(qqq_value, 2),
                "btc_buy_hold_value": round(btc_buy_hold_value, 2),
            }
        )
        previous_btc = btc_close

    if not rows:
        raise RuntimeError("No completed 2026 backtest months were available.")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "initial_capital": INITIAL_CAPITAL,
        "training_window": {"start": TRAINING_START, "end": TRAINING_END},
        "training_max_monthly_ibit_flow_usd": round(training_max, 2),
        "rule": (
            "Score prior-month IBIT flow from 0 to 100 using the maximum positive "
            "monthly flow in Jan 2024-Dec 2025 as 100 and any flow at or below zero "
            "as 0. A negative prior-month flow forces a 0% BTC allocation. Otherwise, "
            "current-month BTC allocation equals 100 minus that score; the remainder "
            "is non-interest-bearing cash. Rebalance at each month start."
        ),
        "comparison": "QQQ adjusted-close buy-and-hold over identical dates",
        "period": {"start": rows[0]["month"], "end": rows[-1]["month"]},
        "results": {
            "strategy_ending_value": rows[-1]["strategy_value"],
            "strategy_return_pct": round(
                (rows[-1]["strategy_value"] / INITIAL_CAPITAL - 1) * 100, 2
            ),
            "qqq_ending_value": rows[-1]["qqq_value"],
            "qqq_return_pct": round(
                (rows[-1]["qqq_value"] / INITIAL_CAPITAL - 1) * 100, 2
            ),
            "btc_buy_hold_ending_value": rows[-1]["btc_buy_hold_value"],
            "btc_buy_hold_return_pct": round(
                (rows[-1]["btc_buy_hold_value"] / INITIAL_CAPITAL - 1) * 100, 2
            ),
        },
        "months": rows,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(result["results"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
