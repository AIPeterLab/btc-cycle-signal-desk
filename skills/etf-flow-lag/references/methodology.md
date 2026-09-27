# ETF Flow Lag Methodology

## Purpose

Test whether the prior completed month's IBIT net flow can size the following month's
Bitcoin allocation. The rule is intentionally lagged so it uses only information that
would have been available at the rebalance date.

## Public, No-Key Sources

### IBIT daily net flows

- Provider: The Block, "BTC Spot ETF Flows"
- JSON: `https://data.tbstat.com/dashboard/markets_structuredproducts_btcspotetfflows_daily_other.json`
- Path: `Series.IBIT.Data`
- Fields: Unix `Timestamp` and USD `Result`
- Aggregate daily observations by UTC calendar month.
- The raw endpoint is public but undocumented and may lag or change. Validate its schema,
  earliest date, latest date, and freshness on each run. Preserve the last good result if
  a refresh fails.

### BTC and QQQ prices

- Provider: Yahoo Finance public chart endpoint
- Symbols: `BTC-USD` and `QQQ`
- Interval: daily
- Use adjusted close when available; otherwise use close.
- Use the last observation of each completed calendar month.

## Allocation Rule

Training window: January 2024 through December 2025, inclusive.

1. Sum IBIT daily net flows into monthly USD totals.
2. Let `training_max` be the largest positive monthly IBIT flow in the training window.
   The verified baseline is March 2024 at `$6,201,600,000`.
3. For a positive prior-month flow:

   `flow_score = min(prior_month_flow / training_max, 1) * 100`

4. For a zero or negative flow, the unmodified scale score is zero.
5. Apply the negative-flow gate:
   - prior-month flow below zero: next-month BTC allocation is `0%`;
   - prior-month flow equal to zero: next-month BTC allocation is `100%`;
   - prior-month flow above zero: next-month BTC allocation is
     `100% - flow_score`.
6. Hold the remainder in non-interest-bearing cash and rebalance at the start of each
   month. Do not charge fees, taxes, or slippage in the baseline.

This is an inverse positive-flow scale combined with a negative-flow risk-off gate. High
positive flows reduce BTC exposure; negative flows eliminate it for the next month.

## Return Calculation

Start with `$1,000` at the December 2025 month-end close.

For each completed 2026 month:

`strategy_month_return = btc_allocation * btc_month_return`

`strategy_value *= 1 + strategy_month_return`

Compare with:

- QQQ adjusted-close buy-and-hold from the same December 2025 start date;
- BTC buy-and-hold over the identical dates.

Never include a partial current month or compare mismatched ending dates.

## Verified Baseline Result

Data through August 2026, generated September 27, 2026:

| Portfolio | Ending value | Return |
|---|---:|---:|
| Lagged IBIT-flow strategy | `$1,322.35` | `+32.23%` |
| QQQ buy-and-hold | `$1,169.53` | `+16.95%` |
| BTC buy-and-hold | `$897.61` | `-10.24%` |

The strategy exceeded QQQ by 15.28 percentage points in this short sample. Do not treat
that result as statistically robust: it covers only eight out-of-sample months and is
sensitive to the cash assumption, scale window, ETF-flow revisions, and rebalance timing.

## Review Checklist

- The training maximum uses only January 2024-December 2025.
- January 2026 uses December 2025 flow; each later month uses the immediately prior month.
- Negative prior-month flow maps to exactly 0% BTC.
- The current incomplete month is absent.
- Strategy, QQQ, and BTC start and end on identical observations.
- QQQ uses adjusted close.
- Output files record assumptions, rule text, monthly allocations, and portfolio values.
- Any alternative such as aggregate ETF flows, interest-bearing cash, transaction costs,
  or a rolling scale is reported as a separate scenario rather than silently replacing
  the baseline.
