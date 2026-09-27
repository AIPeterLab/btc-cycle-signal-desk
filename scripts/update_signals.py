#!/usr/bin/env python3
"""Refresh BTC Cycle Signal Desk data files.

The trading signal is intentionally simple:
buy / hold BTC from 500 days before the Bitcoin halving date through cycle
day 540 after the halving, inclusive; hold Cash outside that window. Context
indicators never override it.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict
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
IBIT_JSON_PATH = DATA_DIR / "ibit_weekly_flows.json"
IBIT_CSV_PATH = DATA_DIR / "ibit_weekly_flows.csv"
IBIT_SVG_PATH = DATA_DIR / "ibit_weekly_flows.svg"
IBIT_MONTHLY_JSON_PATH = DATA_DIR / "ibit_monthly_flows.json"
IBIT_MONTHLY_CSV_PATH = DATA_DIR / "ibit_monthly_flows.csv"
IBIT_MONTHLY_SVG_PATH = DATA_DIR / "ibit_monthly_flows.svg"

THE_BLOCK_IBIT_FLOW_URL = (
    "https://data.tbstat.com/dashboard/"
    "markets_structuredproducts_btcspotetfflows_daily_other.json"
)

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
MA120_GATE_CONFIRMATION_DAYS = 2


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


def the_block_ibit_daily() -> tuple[list[dict[str, Any]], int | None]:
    payload = fetch_json(THE_BLOCK_IBIT_FLOW_URL)
    series = payload.get("Series", {}).get("IBIT", {}).get("Data", [])
    rows: list[dict[str, Any]] = []
    seen_dates: set[date] = set()

    for item in series:
        timestamp = item.get("Timestamp")
        flow = parse_float(item.get("Result"))
        if timestamp is None or flow is None:
            continue
        flow_date = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()
        if flow_date in seen_dates:
            raise RuntimeError(f"The Block returned duplicate IBIT flow date {flow_date}.")
        seen_dates.add(flow_date)
        rows.append({"date": flow_date, "flow_usd": flow})

    rows.sort(key=lambda row: row["date"])
    if not rows:
        raise RuntimeError("The Block returned no usable IBIT daily flow rows.")
    if rows[0]["date"] > date(2024, 1, 11):
        raise RuntimeError("The Block IBIT history does not reach the fund's launch.")

    runtime = payload.get("Runtime")
    return rows, int(runtime) if runtime is not None else None


def ibit_weekly_chart_data(
    daily_flows: list[dict[str, Any]], yahoo_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    flows_by_monday: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_flows:
        flow_date = row["date"]
        monday = flow_date - timedelta(days=flow_date.weekday())
        flows_by_monday[monday].append(row)

    btc_by_date = {
        date.fromisoformat(row["date"]): float(row["close"]) for row in yahoo_rows
    }
    latest_flow_date = max(row["date"] for row in daily_flows)
    weekly_rows: list[dict[str, Any]] = []

    for monday in sorted(flows_by_monday):
        friday = monday + timedelta(days=4)
        next_monday = monday + timedelta(days=7)
        # Include a week only after a later week has begun, or once Friday's
        # observation is present. This prevents a partial latest week.
        complete = latest_flow_date >= next_monday or any(
            row["date"].weekday() == 4 for row in flows_by_monday[monday]
        )
        if not complete:
            continue

        btc_close = btc_by_date.get(friday)
        if btc_close is None:
            prior_closes = [
                (market_day, close)
                for market_day, close in btc_by_date.items()
                if monday <= market_day <= friday
            ]
            if not prior_closes:
                continue
            btc_close = max(prior_closes, key=lambda item: item[0])[1]

        week_flows = flows_by_monday[monday]
        weekly_rows.append(
            {
                "week_end": friday.isoformat(),
                "ibit_net_flow_usd": round(
                    sum(float(row["flow_usd"]) for row in week_flows), 2
                ),
                "btc_close_usd": round(btc_close, 2),
                "flow_observations": len(week_flows),
                "last_flow_date": max(row["date"] for row in week_flows).isoformat(),
            }
        )

    if not weekly_rows:
        raise RuntimeError("No completed IBIT weekly flow rows could be built.")
    return weekly_rows


def ibit_monthly_chart_data(
    daily_flows: list[dict[str, Any]], yahoo_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    flows_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_flows:
        flows_by_month[row["date"].strftime("%Y-%m")].append(row)

    btc_by_month: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for row in yahoo_rows:
        market_day = date.fromisoformat(row["date"])
        btc_by_month[market_day.strftime("%Y-%m")].append(
            (market_day, float(row["close"]))
        )

    latest_flow_month = max(row["date"] for row in daily_flows).strftime("%Y-%m")
    monthly_rows: list[dict[str, Any]] = []
    for month in sorted(flows_by_month):
        # A month is final only after the source has begun publishing a later month.
        if month >= latest_flow_month:
            continue
        price_rows = btc_by_month.get(month, [])
        if not price_rows:
            continue
        month_flows = flows_by_month[month]
        month_end_day, btc_close = max(price_rows, key=lambda item: item[0])
        monthly_rows.append(
            {
                "month": month,
                "month_end": month_end_day.isoformat(),
                "ibit_net_flow_usd": round(
                    sum(float(row["flow_usd"]) for row in month_flows), 2
                ),
                "btc_close_usd": round(btc_close, 2),
                "flow_observations": len(month_flows),
                "last_flow_date": max(row["date"] for row in month_flows).isoformat(),
            }
        )

    if not monthly_rows:
        raise RuntimeError("No completed IBIT monthly flow rows could be built.")
    return monthly_rows


def svg_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render_ibit_svg(
    periods: list[dict[str, Any]], *, date_key: str = "week_end", period_label: str = "Weekly"
) -> str:
    width, height = 1440, 760
    left, right, top, bottom = 105, 110, 72, 115
    plot_width = width - left - right
    plot_height = height - top - bottom
    flows = [float(row["ibit_net_flow_usd"]) / 1_000_000 for row in periods]
    prices = [float(row["btc_close_usd"]) for row in periods]
    flow_limit = max(abs(min(flows)), abs(max(flows)), 1.0) * 1.1
    price_min = min(prices)
    price_max = max(prices)
    price_padding = max((price_max - price_min) * 0.08, 1.0)
    price_low = max(0.0, price_min - price_padding)
    price_high = price_max + price_padding

    def x_at(index: int) -> float:
        return left + ((index + 0.5) / len(periods)) * plot_width

    def flow_y(value: float) -> float:
        return top + ((flow_limit - value) / (flow_limit * 2)) * plot_height

    def price_y(value: float) -> float:
        return top + ((price_high - value) / (price_high - price_low)) * plot_height

    zero_y = flow_y(0)
    title_period = period_label.lower()
    subtitle = (
        "Friday-ending weeks · Green line: IBIT net flow · Blue line: BTC close"
        if period_label == "Weekly"
        else "Completed calendar months · Green line: IBIT net flow · Blue line: BTC month-end close"
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">Bitcoin price versus IBIT {title_period} net inflows</title>',
        f'<desc id="desc">The green line shows {title_period} IBIT net fund flow in US dollars, with red points for outflows. The blue line shows the corresponding Bitcoin closing price.</desc>',
        '<rect width="100%" height="100%" fill="#07111f"/>',
        f'<text x="105" y="35" fill="#f4f7fb" font-family="Arial, sans-serif" font-size="25" font-weight="700">Bitcoin Price vs. IBIT {period_label} Net Inflows</text>',
        f'<text x="105" y="58" fill="#98a9bd" font-family="Arial, sans-serif" font-size="13">{subtitle}</text>',
    ]

    for tick in range(-4, 5):
        flow_value = flow_limit * tick / 4
        y = flow_y(flow_value)
        parts.append(
            f'<line x1="{left}" y1="{svg_number(y)}" x2="{width-right}" y2="{svg_number(y)}" stroke="#223148" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left-12}" y="{svg_number(y+4)}" text-anchor="end" fill="#91a3b8" font-family="Arial, sans-serif" font-size="11">{flow_value:,.0f}M</text>'
        )

    for tick in range(5):
        price_value = price_low + (price_high - price_low) * tick / 4
        y = price_y(price_value)
        parts.append(
            f'<text x="{width-right+12}" y="{svg_number(y+4)}" fill="#63b3ff" font-family="Arial, sans-serif" font-size="11">${price_value/1000:,.0f}k</text>'
        )

    flow_points = " ".join(
        f'{svg_number(x_at(index))},{svg_number(flow_y(flow))}'
        for index, flow in enumerate(flows)
    )
    parts.append(
        f'<polyline points="{flow_points}" fill="none" stroke="#28c98b" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
    )
    point_radius = 3.5 if len(periods) <= 50 else 2.2
    for index, (row, flow) in enumerate(zip(periods, flows)):
        color = "#28c98b" if flow >= 0 else "#ff6474"
        parts.append(
            f'<circle cx="{svg_number(x_at(index))}" cy="{svg_number(flow_y(flow))}" r="{svg_number(point_radius)}" fill="{color}" stroke="#07111f" stroke-width="1"><title>{row[date_key]}: ${flow:,.1f}M</title></circle>'
        )

    points = " ".join(
        f'{svg_number(x_at(index))},{svg_number(price_y(price))}'
        for index, price in enumerate(prices)
    )
    parts.append(
        f'<polyline points="{points}" fill="none" stroke="#55aaff" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
    )
    parts.append(
        f'<line x1="{left}" y1="{svg_number(zero_y)}" x2="{width-right}" y2="{svg_number(zero_y)}" stroke="#d8e1ec" stroke-width="1.25"/>'
    )

    label_step = max(1, len(periods) // 10)
    for index in range(0, len(periods), label_step):
        label = periods[index][date_key][:7]
        x = x_at(index)
        parts.append(
            f'<text x="{svg_number(x)}" y="{height-bottom+28}" transform="rotate(35 {svg_number(x)} {height-bottom+28})" fill="#91a3b8" font-family="Arial, sans-serif" font-size="11">{label}</text>'
        )

    parts.extend(
        [
            f'<text x="{left}" y="{height-24}" fill="#70849b" font-family="Arial, sans-serif" font-size="11">Sources: The Block (IBIT daily net flows); Yahoo Finance (BTC-USD). Generated {datetime.now(timezone.utc).date().isoformat()} UTC.</text>',
            f'<text x="{width-right}" y="35" text-anchor="end" fill="#28c98b" font-family="Arial, sans-serif" font-size="12">━ IBIT net flow <tspan fill="#ff6474">● outflow</tspan></text>',
            f'<text x="{width-right}" y="53" text-anchor="end" fill="#55aaff" font-family="Arial, sans-serif" font-size="12">━ BTC price</text>',
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


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


def confirmed_ma120_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state = False
    consecutive_above = 0
    consecutive_below = 0
    latest_sma: float | None = None
    latest_above = False
    closes: list[float] = []

    for index, row in enumerate(rows):
        closes.append(float(row["close"]))
        latest_sma = rolling_sma(closes, 120)
        if latest_sma is None:
            consecutive_above = 0
            consecutive_below = 0
            latest_above = False
            continue

        latest_above = float(row["close"]) >= latest_sma
        if latest_above:
            consecutive_above += 1
            consecutive_below = 0
        else:
            consecutive_below += 1
            consecutive_above = 0

        if not state and consecutive_above >= MA120_GATE_CONFIRMATION_DAYS:
            state = True
        elif state and consecutive_below >= MA120_GATE_CONFIRMATION_DAYS:
            state = False

    return {
        "daily_sma_120": latest_sma,
        "close_above_sma_120": latest_above,
        "consecutive_closes_above_sma_120": consecutive_above,
        "consecutive_closes_below_sma_120": consecutive_below,
        "ma120_gate_confirmed": state,
        "ma120_gate_rule": "2 up / 2 down",
    }


def strategy_status_for_allocation(allocation_pct: int) -> str:
    if allocation_pct >= 100:
        return "Hold BTC"
    if allocation_pct > 0:
        return f"Hold {allocation_pct}% BTC"
    return "Hold Cash"


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


def strategy_allocation_details(
    yahoo_rows: list[dict[str, Any]], as_of_day: date
) -> dict[str, Any]:
    rows_to_day = [
        row for row in yahoo_rows if date.fromisoformat(row["date"]) <= as_of_day
    ]
    closes = [float(row["close"]) for row in rows_to_day]
    weekly_signal = weekly_50_sma_signal(
        completed_weekly_closes(yahoo_rows, as_of_day),
        REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA,
    )
    daily_sma_50 = rolling_sma(closes, 50)
    daily_sma_200 = rolling_sma(closes, 200)
    ma120_gate = confirmed_ma120_gate(rows_to_day)
    golden_cross_confirmed = (
        daily_sma_50 is not None and daily_sma_200 is not None and daily_sma_50 > daily_sma_200
    )

    indicator_allocation_pct = 0
    if weekly_signal["above_50w_sma_confirmed"]:
        indicator_allocation_pct = 50 if golden_cross_confirmed else 25

    calendar_halving = calendar_halving_for(as_of_day)
    calendar_entry_date = calendar_halving - timedelta(days=BUY_OFFSET_DAYS)
    calendar_exit_date = calendar_halving + timedelta(days=SELL_OFFSET_DAYS)
    cycle_window_active = calendar_entry_date <= as_of_day <= calendar_exit_date
    cycle_window_allocation_pct = (
        100 if cycle_window_active and ma120_gate["ma120_gate_confirmed"] else 0
    )
    final_strategy_allocation_pct = (
        cycle_window_allocation_pct if cycle_window_active else indicator_allocation_pct
    )

    if cycle_window_active and ma120_gate["ma120_gate_confirmed"]:
        ma120_gate_explanation = (
            "The calendar cycle window is active and the MA120 defensive gate is confirmed bullish, "
            "so the cycle allocation is 100% BTC."
        )
    elif cycle_window_active:
        ma120_gate_explanation = (
            "The calendar cycle window is active, but the MA120 defensive gate is not confirmed bullish, "
            "so the cycle allocation is held in cash."
        )
    elif ma120_gate["ma120_gate_confirmed"]:
        ma120_gate_explanation = (
            "BTC is above the confirmed MA120 defensive gate, but the calendar cycle window is not active, "
            "so MA120 does not create a full cycle allocation."
        )
    else:
        ma120_gate_explanation = (
            "The calendar cycle window is not active and the MA120 defensive gate is not confirmed bullish."
        )

    return {
        "weekly_signal": weekly_signal,
        "daily_sma_50": daily_sma_50,
        "daily_sma_200": daily_sma_200,
        "ma120_gate": ma120_gate,
        "golden_cross_confirmed": golden_cross_confirmed,
        "indicator_allocation_pct": indicator_allocation_pct,
        "calendar_entry_date": calendar_entry_date,
        "calendar_exit_date": calendar_exit_date,
        "cycle_window_active": cycle_window_active,
        "cycle_window_allocation_pct": cycle_window_allocation_pct,
        "current_btc_allocation_pct": final_strategy_allocation_pct,
        "final_strategy_allocation_pct": final_strategy_allocation_pct,
        "final_strategy_status": strategy_status_for_allocation(final_strategy_allocation_pct),
        "ma120_gate_explanation": ma120_gate_explanation,
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
    closes = [float(row["close"]) for row in yahoo_rows]
    latest_btc_close = float(latest["close"])
    allocation_details = strategy_allocation_details(yahoo_rows, market_date)
    weekly_signal = allocation_details["weekly_signal"]
    sma_50_week = weekly_signal["sma_50_week"]
    sma_200_week = rolling_sma(closes, 200 * 7)
    daily_sma_50 = allocation_details["daily_sma_50"]
    ma120_gate = allocation_details["ma120_gate"]
    daily_sma_200 = allocation_details["daily_sma_200"]
    ema_50 = exponential_moving_average(closes, 50)
    ema_200 = exponential_moving_average(closes, 200)
    golden_cross_confirmed = allocation_details["golden_cross_confirmed"]
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
    cycle_window_active = allocation_details["cycle_window_active"]
    cycle_window_allocation_pct = allocation_details["cycle_window_allocation_pct"]
    current_btc_allocation_pct = allocation_details["current_btc_allocation_pct"]
    final_strategy_allocation_pct = allocation_details["final_strategy_allocation_pct"]
    final_strategy_status = allocation_details["final_strategy_status"]
    ma120_gate_explanation = allocation_details["ma120_gate_explanation"]
    calendar_entry_date = allocation_details["calendar_entry_date"]
    signal_explanation = (
        f"Current live BTC price: ${latest_btc_close:,.2f} as of {market_date.isoformat()} UTC. "
        f"Latest completed weekly close: "
        f"{('$' + format(float(weekly_signal['latest_weekly_close']), ',.2f')) if weekly_signal['latest_weekly_close'] is not None else 'unavailable'} "
        f"for week ending "
        f"{weekly_signal['latest_completed_week_end'].isoformat() if weekly_signal['latest_completed_week_end'] else 'unavailable'} UTC. "
        f"Confirmed weekly signal: {bull_market_signal}; "
        f"{weekly_signal['consecutive_weekly_closes_above_50w_sma']} completed weekly close(s) above the 50-week SMA "
        f"with {REQUIRED_WEEKLY_CLOSES_ABOVE_50W_SMA} required. "
        f"Final strategy allocation: {final_strategy_allocation_pct}%."
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
        row_allocation_details = strategy_allocation_details(yahoo_rows, row_date)
        row_status, row_cycle_day, row_buy_date, row_day_540, row_days_from_buy, row_days_from_day_540 = signal_for(row_date)
        if row_date < row_buy_date:
            notes = f"{abs(row_days_from_buy)} days before the BTC buy window."
        elif row_days_from_day_540 == 0:
            notes = "Last day of the tested BTC holding window."
        elif row_date > row_day_540:
            notes = f"{row_days_from_day_540} days after day 540."
        else:
            notes = f"Inside BTC holding window; {abs(row_days_from_day_540)} days until day 540."
        notes = (
            f"{notes} Final allocation: "
            f"{row_allocation_details['final_strategy_allocation_pct']}% BTC."
        )
        recent_history.append(
            {
                "date": row_date.isoformat(),
                "btc_close": round(float(row["close"]), 2),
                "cycle_day": row_cycle_day,
                "buy_date": row_buy_date.isoformat(),
                "sell_date": row_day_540.isoformat(),
                "status": row_status,
                "final_strategy_status": row_allocation_details["final_strategy_status"],
                "final_strategy_allocation_pct": row_allocation_details[
                    "final_strategy_allocation_pct"
                ],
                "cycle_window_active": row_allocation_details["cycle_window_active"],
                "ma120_gate_confirmed": row_allocation_details["ma120_gate"][
                    "ma120_gate_confirmed"
                ],
                "notes": notes,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_date": market_date.isoformat(),
        "btc_close": round(float(latest["close"]), 2),
        "status": status,
        "final_strategy_status": final_strategy_status,
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
        "daily_sma_120": round(ma120_gate["daily_sma_120"], 2) if ma120_gate["daily_sma_120"] is not None else None,
        "close_above_sma_120": ma120_gate["close_above_sma_120"],
        "consecutive_closes_above_sma_120": ma120_gate["consecutive_closes_above_sma_120"],
        "consecutive_closes_below_sma_120": ma120_gate["consecutive_closes_below_sma_120"],
        "ma120_gate_confirmed": ma120_gate["ma120_gate_confirmed"],
        "ma120_gate_rule": ma120_gate["ma120_gate_rule"],
        "daily_sma_200": round(daily_sma_200, 2) if daily_sma_200 is not None else None,
        "golden_cross_confirmed": golden_cross_confirmed,
        "calendar_entry_date": calendar_entry_date.isoformat(),
        "cycle_window_active": cycle_window_active,
        "cycle_window_allocation_pct": cycle_window_allocation_pct,
        "current_btc_allocation_pct": current_btc_allocation_pct,
        "final_strategy_allocation_pct": final_strategy_allocation_pct,
        "ma120_gate_explanation": ma120_gate_explanation,
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


def write_outputs(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    fieldnames = [
        "date",
        "btc_close",
        "cycle_day",
        "buy_date",
        "sell_date",
        "status",
        "final_strategy_status",
        "final_strategy_allocation_pct",
        "cycle_window_active",
        "ma120_gate_confirmed",
        "notes",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(payload["recent_history"])


def write_ibit_outputs(
    weeks: list[dict[str, Any]], source_runtime: int | None
) -> None:
    generated_at = datetime.now(timezone.utc)
    source_updated_at = (
        datetime.fromtimestamp(source_runtime, tz=timezone.utc).isoformat()
        if source_runtime is not None
        else None
    )
    chart_payload = {
        "generated_at": generated_at.isoformat(),
        "source_updated_at": source_updated_at,
        "source": "The Block — BTC Spot ETF Flows",
        "source_url": THE_BLOCK_IBIT_FLOW_URL,
        "btc_price_source": "Yahoo Finance BTC-USD daily adjusted close",
        "methodology": (
            "Daily IBIT net fund flows are summed into Friday-ending calendar weeks. "
            "BTC price is the Friday UTC close. Partial latest weeks are excluded."
        ),
        "units": {"ibit_net_flow": "USD", "btc_close": "USD"},
        "weeks": weeks,
    }
    IBIT_JSON_PATH.write_text(
        json.dumps(chart_payload, indent=2) + "\n", encoding="utf-8"
    )

    fieldnames = [
        "week_end",
        "ibit_net_flow_usd",
        "btc_close_usd",
        "flow_observations",
        "last_flow_date",
    ]
    with IBIT_CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(weeks)

    IBIT_SVG_PATH.write_text(render_ibit_svg(weeks), encoding="utf-8")


def write_ibit_monthly_outputs(
    months: list[dict[str, Any]], source_runtime: int | None
) -> None:
    generated_at = datetime.now(timezone.utc)
    source_updated_at = (
        datetime.fromtimestamp(source_runtime, tz=timezone.utc).isoformat()
        if source_runtime is not None
        else None
    )
    chart_payload = {
        "generated_at": generated_at.isoformat(),
        "source_updated_at": source_updated_at,
        "source": "The Block — BTC Spot ETF Flows",
        "source_url": THE_BLOCK_IBIT_FLOW_URL,
        "btc_price_source": "Yahoo Finance BTC-USD daily adjusted close",
        "methodology": (
            "Daily IBIT net fund flows are summed into completed calendar months. "
            "BTC price is the final UTC daily close of each month. The current "
            "incomplete month is excluded."
        ),
        "units": {"ibit_net_flow": "USD", "btc_close": "USD"},
        "months": months,
    }
    IBIT_MONTHLY_JSON_PATH.write_text(
        json.dumps(chart_payload, indent=2) + "\n", encoding="utf-8"
    )

    fieldnames = [
        "month",
        "month_end",
        "ibit_net_flow_usd",
        "btc_close_usd",
        "flow_observations",
        "last_flow_date",
    ]
    with IBIT_MONTHLY_CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(months)

    IBIT_MONTHLY_SVG_PATH.write_text(
        render_ibit_svg(months, date_key="month", period_label="Monthly"),
        encoding="utf-8",
    )


def main() -> int:
    yahoo_rows = yahoo_btc_daily()
    payload = build_payload(yahoo_rows)
    write_outputs(payload)
    ibit_message = "IBIT chart unchanged"
    try:
        ibit_daily, source_runtime = the_block_ibit_daily()
        ibit_weeks = ibit_weekly_chart_data(ibit_daily, yahoo_rows)
        ibit_months = ibit_monthly_chart_data(ibit_daily, yahoo_rows)
        write_ibit_outputs(ibit_weeks, source_runtime)
        write_ibit_monthly_outputs(ibit_months, source_runtime)
        ibit_message = (
            f"IBIT chart weeks={len(ibit_weeks)} "
            f"latest={ibit_weeks[-1]['week_end']} months={len(ibit_months)} "
            f"latest_month={ibit_months[-1]['month']}"
        )
    except (RuntimeError, URLError, TimeoutError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Warning: IBIT chart refresh failed; existing chart artifacts were preserved: {exc}",
            file=sys.stderr,
        )
    print(
        f"{payload['market_date']} {payload['status']} "
        f"cycle_day={payload['cycle_day']} btc_close={payload['btc_close']} {ibit_message}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
