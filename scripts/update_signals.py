#!/usr/bin/env python3
"""Refresh BTC Cycle Signal Desk data files.

The trading signal is intentionally simple:
hold BTC from halving day through cycle day 540, inclusive; hold Cash after
day 540 until the next confirmed halving. Context indicators never override it.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
JSON_PATH = DATA_DIR / "signals.json"
CSV_PATH = DATA_DIR / "signals.csv"

HALVINGS = [
    date(2012, 11, 28),
    date(2016, 7, 9),
    date(2020, 5, 11),
    date(2024, 4, 20),
]

RULE_SUMMARY = (
    "Hold BTC from halving day through day 540, inclusive. "
    "Hold Cash from day 541 until the next confirmed halving."
)

MINER_EFFICIENCY_J_PER_TH = float(os.environ.get("MINER_EFFICIENCY_J_PER_TH", "30"))
ELECTRICITY_COST_USD_PER_KWH = float(os.environ.get("ELECTRICITY_COST_USD_PER_KWH", "0.05"))


def fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "BTC Cycle Signal Desk/1.0 (+https://github.com/AIPeterLab/btc-cycle-signal-desk)"
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
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def yahoo_btc_daily() -> list[dict[str, Any]]:
    start = int(datetime(2010, 7, 17, tzinfo=timezone.utc).timestamp())
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
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?{params}"
    payload = fetch_json(url)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
    rows: list[dict[str, Any]] = []

    for index, stamp in enumerate(timestamps):
        close = None
        if index < len(adjclose):
            close = parse_float(adjclose[index])
        if close is None and index < len(quote.get("close", [])):
            close = parse_float(quote["close"][index])
        if close is None:
            continue
        market_date = datetime.fromtimestamp(stamp, tz=timezone.utc).date()
        rows.append({"date": market_date.isoformat(), "close": close})

    if not rows:
        raise RuntimeError("Yahoo returned no usable BTC-USD daily close rows.")
    return rows


def coinmetrics_rows() -> list[dict[str, Any]]:
    params = urlencode(
        {
            "assets": "btc",
            "metrics": "ReferenceRateUSD,PriceUSD,CapMVRVCur,HashRate,IssTotNtv,SplyCur",
            "frequency": "1d",
            "page_size": "10000",
        }
    )
    url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?{params}"
    payload = fetch_json(url)
    rows = payload.get("data", [])
    if not rows:
        raise RuntimeError("CoinMetrics returned no BTC rows.")
    return rows


def latest_complete_coinmetrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(rows):
        price = parse_float(row.get("ReferenceRateUSD")) or parse_float(row.get("PriceUSD"))
        mvrv = parse_float(row.get("CapMVRVCur"))
        hashrate = parse_float(row.get("HashRate"))
        issued = parse_float(row.get("IssTotNtv"))
        if price and mvrv and hashrate and issued and issued > 0:
            return row
    return None


def active_halving_for(day: date) -> date:
    active = HALVINGS[0]
    for halving in HALVINGS:
        if day >= halving:
            active = halving
        else:
            break
    return active


def signal_for(day: date) -> tuple[str, int, date, int]:
    halving = active_halving_for(day)
    cycle_day = (day - halving).days
    day_540 = halving + timedelta(days=540)
    status = "Hold BTC" if 0 <= cycle_day <= 540 else "Hold Cash"
    days_from_day_540 = (day - day_540).days
    return status, cycle_day, day_540, days_from_day_540


def rolling_sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def build_payload() -> dict[str, Any]:
    yahoo_rows = yahoo_btc_daily()
    latest = yahoo_rows[-1]
    market_date = date.fromisoformat(latest["date"])
    status, cycle_day, day_540, days_from_day_540 = signal_for(market_date)
    active_halving = active_halving_for(market_date)
    closes = [float(row["close"]) for row in yahoo_rows]
    sma_200_week = rolling_sma(closes, 200 * 7)

    realized_price = None
    realized_source = "CoinMetrics MVRV unavailable; on-chain cost-basis context not computed."
    electrical_cost = None
    electrical_assumptions = (
        f"Estimated mining electrical cost, context only. Assumes "
        f"{MINER_EFFICIENCY_J_PER_TH:g} J/TH and ${ELECTRICITY_COST_USD_PER_KWH:g}/kWh."
    )

    try:
        cm_latest = latest_complete_coinmetrics(coinmetrics_rows())
    except (RuntimeError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        cm_latest = None
        realized_source = f"CoinMetrics unavailable during this run: {exc}"

    if cm_latest:
        cm_date = str(cm_latest.get("time", ""))[:10]
        cm_price = parse_float(cm_latest.get("ReferenceRateUSD")) or parse_float(cm_latest.get("PriceUSD"))
        mvrv = parse_float(cm_latest.get("CapMVRVCur"))
        hashrate = parse_float(cm_latest.get("HashRate"))
        issued = parse_float(cm_latest.get("IssTotNtv"))

        if cm_price and mvrv:
            realized_price = cm_price / mvrv
            realized_source = f"On-chain cost basis, context only. CoinMetrics row date: {cm_date}."

        if hashrate and issued and issued > 0:
            energy_kwh_per_day = hashrate * MINER_EFFICIENCY_J_PER_TH * 86400 / 3_600_000
            electrical_cost_per_day = energy_kwh_per_day * ELECTRICITY_COST_USD_PER_KWH
            electrical_cost = electrical_cost_per_day / issued
            electrical_assumptions = (
                f"Estimated mining electrical cost, context only. CoinMetrics row date: {cm_date}; "
                f"assumes {MINER_EFFICIENCY_J_PER_TH:g} J/TH and "
                f"${ELECTRICITY_COST_USD_PER_KWH:g}/kWh."
            )

    recent_history = []
    for row in yahoo_rows[-14:]:
        row_date = date.fromisoformat(row["date"])
        row_status, row_cycle_day, row_day_540, row_days_from_day_540 = signal_for(row_date)
        if row_days_from_day_540 == 0:
            notes = "Last day of the tested BTC holding window."
        elif row_days_from_day_540 > 0:
            notes = f"{row_days_from_day_540} days after day 540."
        else:
            notes = f"{abs(row_days_from_day_540)} days before day 540."
        recent_history.append(
            {
                "date": row_date.isoformat(),
                "btc_close": round(float(row["close"]), 2),
                "cycle_day": row_cycle_day,
                "status": row_status,
                "notes": notes,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_date": market_date.isoformat(),
        "btc_close": round(float(latest["close"]), 2),
        "status": status,
        "active_halving_date": active_halving.isoformat(),
        "cycle_day": cycle_day,
        "day_540_date": day_540.isoformat(),
        "days_from_day_540": days_from_day_540,
        "sma_200_week": round(sma_200_week, 2) if sma_200_week is not None else None,
        "realized_price": round(realized_price, 2) if realized_price is not None else None,
        "realized_price_source": realized_source,
        "electrical_cost_per_btc": round(electrical_cost, 2) if electrical_cost is not None else None,
        "electrical_cost_assumptions": electrical_assumptions,
        "rule_summary": RULE_SUMMARY,
        "recent_history": recent_history,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    fieldnames = ["date", "btc_close", "cycle_day", "status", "notes"]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload["recent_history"])


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(
        f"{payload['market_date']} {payload['status']} "
        f"cycle_day={payload['cycle_day']} btc_close={payload['btc_close']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
