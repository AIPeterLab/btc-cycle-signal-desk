#!/usr/bin/env python3
"""Refresh BTC Cycle Signal Desk data files.

The trading signal is intentionally simple:
buy / hold BTC from 500 days before the Bitcoin halving date through cycle
day 540 after the halving, inclusive; hold Cash outside that window. Context
indicators never override it.
"""

from __future__ import annotations

import csv
import html
import json
import math
import os
import re
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
ETF_FLOW_JSON_PATH = DATA_DIR / "etf_flows.json"
ETF_FLOW_SOURCE_URL = "https://www.tftc.io/bitcoin-etf-flows"
ETF_START_DATE = date(2024, 1, 11)

HALVINGS = [
    date(2012, 11, 28),
    date(2016, 7, 9),
    date(2020, 5, 11),
    date(2024, 4, 20),
]

BUY_OFFSET_DAYS = 500
SELL_OFFSET_DAYS = 540
HALVING_CYCLE_YEARS = 4

RULE_SUMMARY = (
    "Buy / hold BTC from 500 days before the Bitcoin halving date through "
    "day 540 after the halving date, inclusive. Hold Cash outside that window."
)

MINER_EFFICIENCY_J_PER_TH = float(os.environ.get("MINER_EFFICIENCY_J_PER_TH", "30"))
ELECTRICITY_COST_USD_PER_KWH = float(os.environ.get("ELECTRICITY_COST_USD_PER_KWH", "0.05"))
REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA = max(
    1, int(os.environ.get("REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA", "1"))
)


def fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "BTC Cycle Signal Desk/1.0 (+https://github.com/AIPeterLab/btc-cycle-signal-desk)"
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "BTC Cycle Signal Desk/1.0 (+https://github.com/AIPeterLab/btc-cycle-signal-desk)"
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


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


def parse_flow_amount_to_musd(sign: str, amount: str, unit: str) -> float:
    value = float(amount.replace(",", ""))
    if unit.upper() == "B":
        value *= 1000
    if sign in ("-", "−"):
        value *= -1
    return value


def parse_tftc_flow_rows_from_body(body: str) -> dict[date, float]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"\s+", " ", text.replace("\xa0", " "))
    pattern = re.compile(
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday), "
        r"([A-Z][a-z]{2} \d{1,2}, \d{4})\s*·\s*([+\-−])?\$(\d[\d,]*(?:\.\d+)?)([MB])"
    )
    by_date: dict[date, float] = {}
    for match in pattern.finditer(text):
        row_date = datetime.strptime(match.group(1), "%b %d, %Y").date()
        if row_date < ETF_START_DATE:
            continue
        by_date[row_date] = parse_flow_amount_to_musd(
            match.group(2) or "+", match.group(3), match.group(4)
        )
    return by_date


def month_archive_slugs(start_day: date, end_day: date) -> list[str]:
    slugs = []
    month = date(start_day.year, start_day.month, 1)
    end_month = date(end_day.year, end_day.month, 1)
    while month <= end_month:
        slugs.append(month.strftime("%B-%Y").lower())
        if month.month == 12:
            month = date(month.year + 1, 1, 1)
        else:
            month = date(month.year, month.month + 1, 1)
    return slugs


def tftc_etf_flow_rows(as_of_day: date) -> list[dict[str, Any]]:
    by_date: dict[date, float] = {}
    errors: list[str] = []
    for slug in month_archive_slugs(ETF_START_DATE, as_of_day):
        url = f"{ETF_FLOW_SOURCE_URL}/{slug}"
        try:
            by_date.update(parse_tftc_flow_rows_from_body(fetch_text(url)))
        except (URLError, TimeoutError, ValueError) as exc:
            errors.append(f"{slug}: {exc}")

    if not by_date:
        try:
            by_date.update(parse_tftc_flow_rows_from_body(fetch_text(ETF_FLOW_SOURCE_URL)))
        except (URLError, TimeoutError, ValueError) as exc:
            errors.append(f"live page: {exc}")

    rows = [
        {"date": row_date.isoformat(), "etf_flow_musd": round(flow, 1)}
        for row_date, flow in sorted(by_date.items())
    ]
    if not rows:
        raise RuntimeError("TFTC returned no usable BTC ETF flow rows. " + "; ".join(errors))
    return rows


def build_etf_flow_payload(yahoo_rows: list[dict[str, Any]]) -> dict[str, Any]:
    market_date = date.fromisoformat(yahoo_rows[-1]["date"])
    price_by_date = {row["date"]: float(row["close"]) for row in yahoo_rows}
    sorted_price_dates = sorted(price_by_date)
    previous_close_by_date: dict[str, float] = {}
    previous_close: float | None = None
    for row_date in sorted_price_dates:
        if previous_close is not None:
            previous_close_by_date[row_date] = previous_close
        previous_close = price_by_date[row_date]

    try:
        flow_rows = tftc_etf_flow_rows(market_date)
        etf_error = None
    except (RuntimeError, URLError, TimeoutError, ValueError) as exc:
        if ETF_FLOW_JSON_PATH.exists():
            return json.loads(ETF_FLOW_JSON_PATH.read_text(encoding="utf-8"))
        flow_rows = []
        etf_error = str(exc)

    chart_rows = []
    cumulative_flow = 0.0
    for flow_row in flow_rows:
        row_date = flow_row["date"]
        btc_close = price_by_date.get(row_date)
        if btc_close is None:
            continue
        cumulative_flow += float(flow_row["etf_flow_musd"])
        previous_close = previous_close_by_date.get(row_date)
        daily_btc_change_pct = (
            ((btc_close - previous_close) / previous_close) * 100 if previous_close else None
        )
        chart_rows.append(
            {
                "date": row_date,
                "btc_close": round(btc_close, 2),
                "daily_btc_change_pct": (
                    round(daily_btc_change_pct, 2)
                    if daily_btc_change_pct is not None
                    else None
                ),
                "etf_flow_musd": round(float(flow_row["etf_flow_musd"]), 1),
                "cumulative_etf_flow_musd": round(cumulative_flow, 1),
            }
        )

    latest = chart_rows[-1] if chart_rows else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "TFTC Bitcoin ETF Flows, sourced from Farside Investors",
        "source_url": ETF_FLOW_SOURCE_URL,
        "btc_price_source": "Yahoo Finance BTC-USD daily close",
        "start_date": ETF_START_DATE.isoformat(),
        "end_date": latest["date"] if latest else None,
        "trading_days": len(chart_rows),
        "latest_flow_musd": latest["etf_flow_musd"] if latest else None,
        "latest_btc_close": latest["btc_close"] if latest else None,
        "cumulative_etf_flow_musd": (
            round(chart_rows[-1]["cumulative_etf_flow_musd"], 1) if chart_rows else 0.0
        ),
        "error": etf_error,
        "chart": chart_rows,
    }


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
    for halving in HALVINGS:
        buy_date = halving - timedelta(days=BUY_OFFSET_DAYS)
        sell_date = halving + timedelta(days=SELL_OFFSET_DAYS)
        if buy_date <= day <= sell_date:
            return halving

    active = HALVINGS[0]
    for halving in HALVINGS:
        if day >= halving:
            active = halving
        else:
            break
    return active


def add_cycle_years(day: date, years: int = HALVING_CYCLE_YEARS) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(month=2, day=28, year=day.year + years)


def next_halving_for(day: date) -> date:
    halving = HALVINGS[-1]
    while halving - timedelta(days=BUY_OFFSET_DAYS) <= day:
        halving = add_cycle_years(halving)
    return halving


def calendar_halving_for(day: date) -> date:
    halving = HALVINGS[-1]
    while halving + timedelta(days=SELL_OFFSET_DAYS) < day:
        halving = add_cycle_years(halving)
    return halving


def signal_for(day: date) -> tuple[str, int, date, date, int, int]:
    halving = active_halving_for(day)
    cycle_day = (day - halving).days
    buy_date = halving - timedelta(days=BUY_OFFSET_DAYS)
    day_540 = halving + timedelta(days=SELL_OFFSET_DAYS)
    status = "Hold BTC" if buy_date <= day <= day_540 else "Hold Cash"
    days_from_buy = (day - buy_date).days
    days_from_day_540 = (day - day_540).days
    return status, cycle_day, buy_date, day_540, days_from_buy, days_from_day_540


def rolling_sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def completed_weekly_closes(rows: list[dict[str, Any]], as_of_day: date) -> list[dict[str, Any]]:
    weekly_rows: list[dict[str, Any]] = []
    for row in rows:
        row_date = date.fromisoformat(row["date"])
        if row_date >= as_of_day:
            continue
        if row_date.weekday() == 6:
            weekly_rows.append({"week_end": row_date, "close": float(row["close"])})
    return weekly_rows


def weekly_50_sma_signal(
    weekly_rows: list[dict[str, Any]], required_closes: int
) -> dict[str, Any]:
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(weekly_rows):
        if index < 49:
            sma = None
            above = False
        else:
            sma = statistics.fmean(float(item["close"]) for item in weekly_rows[index - 49 : index + 1])
            above = float(row["close"]) > sma
        enriched.append(
            {
                "week_end": row["week_end"],
                "close": float(row["close"]),
                "sma_50_week": sma,
                "above": above,
            }
        )

    latest = enriched[-1] if enriched else None
    consecutive = 0
    for row in reversed(enriched):
        if not row["above"]:
            break
        consecutive += 1

    return {
        "latest_completed_week_end": latest["week_end"] if latest else None,
        "latest_weekly_close": latest["close"] if latest else None,
        "sma_50_week": latest["sma_50_week"] if latest else None,
        "weekly_close_above_50w_sma": bool(latest and latest["above"]),
        "consecutive_weekly_closes_above_50w_sma": consecutive,
        "above_50w_sma_confirmed": bool(
            latest and latest["above"] and consecutive >= required_closes
        ),
    }


def exponential_moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    smoothing = 2 / (window + 1)
    ema = statistics.fmean(values[:window])
    for value in values[window:]:
        ema = (value * smoothing) + (ema * (1 - smoothing))
    return ema


def build_payload(yahoo_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if yahoo_rows is None:
        yahoo_rows = yahoo_btc_daily()
    latest = yahoo_rows[-1]
    market_date = date.fromisoformat(latest["date"])
    status, cycle_day, buy_date, day_540, days_from_buy, days_from_day_540 = signal_for(market_date)
    active_halving = active_halving_for(market_date)
    next_halving = next_halving_for(market_date)
    next_buy_date = next_halving - timedelta(days=BUY_OFFSET_DAYS)
    days_until_next_buy = (next_buy_date - market_date).days
    calendar_halving = calendar_halving_for(market_date)
    calendar_entry_date = calendar_halving - timedelta(days=BUY_OFFSET_DAYS)
    calendar_exit_date = calendar_halving + timedelta(days=SELL_OFFSET_DAYS)
    closes = [float(row["close"]) for row in yahoo_rows]
    latest_btc_close = float(latest["close"])
    weekly_signal = weekly_50_sma_signal(
        completed_weekly_closes(yahoo_rows, market_date),
        REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA,
    )
    sma_50_week = weekly_signal["sma_50_week"]
    sma_200_week = rolling_sma(closes, 200 * 7)
    daily_sma_50 = rolling_sma(closes, 50)
    daily_sma_200 = rolling_sma(closes, 200)
    ema_50 = exponential_moving_average(closes, 50)
    ema_200 = exponential_moving_average(closes, 200)
    golden_cross_confirmed = (
        daily_sma_50 is not None and daily_sma_200 is not None and daily_sma_50 > daily_sma_200
    )
    bull_market_signal = (
        "Bull Market Confirmed"
        if weekly_signal["above_50w_sma_confirmed"]
        else "Below 50-week SMA"
    )
    sma_50_week_distance_pct = (
        ((float(weekly_signal["latest_weekly_close"]) - sma_50_week) / sma_50_week) * 100
        if sma_50_week and weekly_signal["latest_weekly_close"] is not None
        else None
    )
    indicator_allocation_pct = 0
    if weekly_signal["above_50w_sma_confirmed"]:
        indicator_allocation_pct = 50 if golden_cross_confirmed else 25
    current_btc_allocation_pct = (
        100 if calendar_entry_date <= market_date <= calendar_exit_date else indicator_allocation_pct
    )
    signal_explanation = (
        f"Current live BTC price: ${latest_btc_close:,.2f} as of {market_date.isoformat()} UTC. "
        f"Latest completed weekly close: "
        f"{('$' + format(float(weekly_signal['latest_weekly_close']), ',.2f')) if weekly_signal['latest_weekly_close'] is not None else 'unavailable'} "
        f"for week ending "
        f"{weekly_signal['latest_completed_week_end'].isoformat() if weekly_signal['latest_completed_week_end'] else 'unavailable'} UTC. "
        f"Confirmed weekly signal: {bull_market_signal}; "
        f"{weekly_signal['consecutive_weekly_closes_above_50w_sma']} completed weekly close(s) above the 50-week SMA "
        f"with {REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA} required. "
        f"Current BTC allocation: {current_btc_allocation_pct}%."
    )

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
        row_status, row_cycle_day, row_buy_date, row_day_540, row_days_from_buy, row_days_from_day_540 = signal_for(row_date)
        if row_date < row_buy_date:
            notes = f"{abs(row_days_from_buy)} days before the BTC buy window."
        elif row_days_from_day_540 == 0:
            notes = "Last day of the tested BTC holding window."
        elif row_date > row_day_540:
            notes = f"{row_days_from_day_540} days after day 540."
        else:
            notes = f"Inside BTC holding window; {abs(row_days_from_day_540)} days until day 540."
        recent_history.append(
            {
                "date": row_date.isoformat(),
                "btc_close": round(float(row["close"]), 2),
                "cycle_day": row_cycle_day,
                "buy_date": row_buy_date.isoformat(),
                "sell_date": row_day_540.isoformat(),
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
        "next_halving_date": next_halving.isoformat(),
        "last_buy_date": buy_date.isoformat(),
        "last_sell_date": day_540.isoformat(),
        "next_buy_date": next_buy_date.isoformat(),
        "buy_date": buy_date.isoformat(),
        "sell_date": day_540.isoformat(),
        "cycle_day": cycle_day,
        "day_540_date": day_540.isoformat(),
        "days_from_buy_date": days_from_buy,
        "days_from_day_540": days_from_day_540,
        "days_until_next_buy_date": days_until_next_buy,
        "latest_completed_week_end": (
            weekly_signal["latest_completed_week_end"].isoformat()
            if weekly_signal["latest_completed_week_end"]
            else None
        ),
        "latest_weekly_close": (
            round(weekly_signal["latest_weekly_close"], 2)
            if weekly_signal["latest_weekly_close"] is not None
            else None
        ),
        "sma_50_week": round(sma_50_week, 2) if sma_50_week is not None else None,
        "weekly_close_above_50w_sma": weekly_signal["weekly_close_above_50w_sma"],
        "consecutive_weekly_closes_above_50w_sma": weekly_signal[
            "consecutive_weekly_closes_above_50w_sma"
        ],
        "above_50w_sma_confirmed": weekly_signal["above_50w_sma_confirmed"],
        "required_weekly_closes_above_50w_sma": REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA,
        "sma_50_week_signal": bull_market_signal,
        "sma_50_week_distance_pct": round(sma_50_week_distance_pct, 2) if sma_50_week_distance_pct is not None else None,
        "sma_200_week": round(sma_200_week, 2) if sma_200_week is not None else None,
        "daily_sma_50": round(daily_sma_50, 2) if daily_sma_50 is not None else None,
        "daily_sma_200": round(daily_sma_200, 2) if daily_sma_200 is not None else None,
        "golden_cross_confirmed": golden_cross_confirmed,
        "calendar_entry_date": calendar_entry_date.isoformat(),
        "current_btc_allocation_pct": current_btc_allocation_pct,
        "signal_explanation": signal_explanation,
        "ema_50": round(ema_50, 2) if ema_50 is not None else None,
        "ema_200": round(ema_200, 2) if ema_200 is not None else None,
        "realized_price": round(realized_price, 2) if realized_price is not None else None,
        "realized_price_source": realized_source,
        "electrical_cost_per_btc": round(electrical_cost, 2) if electrical_cost is not None else None,
        "electrical_cost_assumptions": electrical_assumptions,
        "rule_summary": RULE_SUMMARY,
        "recent_history": recent_history,
    }


def write_outputs(payload: dict[str, Any], etf_flow_payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    ETF_FLOW_JSON_PATH.write_text(json.dumps(etf_flow_payload, indent=2) + "\n", encoding="utf-8")

    fieldnames = ["date", "btc_close", "cycle_day", "buy_date", "sell_date", "status", "notes"]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(payload["recent_history"])


def main() -> int:
    yahoo_rows = yahoo_btc_daily()
    payload = build_payload(yahoo_rows)
    etf_flow_payload = build_etf_flow_payload(yahoo_rows)
    write_outputs(payload, etf_flow_payload)
    print(
        f"{payload['market_date']} {payload['status']} "
        f"cycle_day={payload['cycle_day']} btc_close={payload['btc_close']} "
        f"etf_flow_days={etf_flow_payload['trading_days']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
