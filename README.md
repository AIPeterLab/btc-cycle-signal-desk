# BTC Cycle Signal Desk

Public dashboard for the Bitcoin four-year-cycle tracking system.

## Signal Rule

- Signal source is the Bitcoin halving date, with fixed pre-halving and post-halving day counts.
- Buy / hold BTC from 500 days before the Bitcoin halving date through day 540 after the halving date, inclusive.
- Hold Cash before the pre-halving buy date and after the day-540 sell date.
- For the current studied cycle, the active halving date is 2024-04-20, the buy date is 2022-12-07, and the day-540 sell date is 2025-10-12.
- Inside the calendar cycle window, the MA120 2-up/2-down defensive gate controls whether the cycle allocation is 100% BTC or cash.
- Outside the calendar cycle window, the confirmed 50-week SMA and daily SMA50/SMA200 golden cross remain early-entry overlays only.
- The 200-week SMA, BTC EMA50, BTC EMA200, realized price, and estimated electrical cost per BTC are context only. They do not override the strategy allocation.

This dashboard does not use QQQ, QLD, SPY, SSO, TQQQ, MACD, EMA crossover, or 5-day DCA rules.

## Files

- `index.html` - static public dashboard.
- `data/signals.json` - current status, context values, and recent history.
- `data/signals.csv` - recent signal history.
- `data/ibit_weekly_flows.json` - chart-ready weekly IBIT net flows and BTC closes.
- `data/ibit_weekly_flows.csv` - tabular weekly IBIT net flows and BTC closes.
- `data/ibit_weekly_flows.svg` - backend-generated Bitcoin price versus IBIT flow chart.
- `data/ibit_monthly_flows.json` - chart-ready monthly IBIT net flows and BTC closes.
- `data/ibit_monthly_flows.csv` - tabular monthly IBIT net flows and BTC closes.
- `data/ibit_monthly_flows.svg` - backend-generated monthly IBIT flow chart.
- `data/pure_gate_btc.json` and `.csv` - reproducible BTC-USD pure-gate paper backtest.
- `data/pure_gate_bitx.json` and `.csv` - reproducible BITX pure-gate paper backtest.
- `scripts/update_signals.py` - no-key updater using public BTC-USD and CoinMetrics data.
- `scripts/backtest_pure_gate.py` - stdlib-only IBIT pure-gate paper backtest.
- `scripts/backtest_ibit_flow_allocation.py` - reproducible lagged-IBIT-flow research backtest.
- `skills/etf-flow-lag/` - reusable agent instructions for reviewing and repeating that backtest.
- `.github/workflows/daily-update.yml` - dispatch-only GitHub Actions refresh.
- `_headers` - Cloudflare Pages cache rules matching the QLD/SSO signal desks.
- `Real_Account_Tracking_System.doc` - plain-language operating manual from the source project.

## Refresh

Run the updater locally:

```powershell
python scripts/update_signals.py
```

The updater also retrieves The Block's public daily IBIT net-flow dataset, aggregates
completed Friday-ending weeks, pairs them with Yahoo Finance BTC-USD Friday closes,
and refreshes the three IBIT chart artifacts. If that optional source is temporarily
unavailable, the core signal refresh continues and the last good chart is preserved.
It also creates equivalent monthly artifacts using completed calendar months and the
final BTC close of each month.

## Experimental Paper Sleeve: IBIT Flow Pure Gate

This research sleeve is separate from the live halving-cycle strategy and never
changes its signal or allocation. For each calendar month beginning February 2024,
it sums IBIT's prior completed UTC calendar-month net USD flow from The Block. A
negative prior-month flow holds non-interest-bearing cash; a zero or positive flow
holds 100% of the selected exposure asset. It rebalances at month start with no
fees, taxes, or slippage and is tested independently on BTC-USD spot and BITX using
Yahoo Finance adjusted closes (falling back to close).

Run the reproducible backtest with:

```powershell
python scripts/backtest_pure_gate.py
```

Backtest totals exclude the incomplete current month. The generated JSON also
records a clearly labeled current-month paper snapshot for dashboard context.

The daily schedule is centralized in the AIPeterLab Cloudflare Worker. The Worker dispatches this repo's GitHub Actions workflow at the New York refresh window, and the workflow can also be run manually with `workflow_dispatch`. Keep this repository workflow dispatch-only; do not add a GitHub `schedule:` block.

## Cloudflare Pages

Recommended production host: Cloudflare Pages at `https://btc.aipeterlab.com`.

Use Git integration so Cloudflare Pages redeploys after the dispatched GitHub refresh workflow commits new data:

- Cloudflare Git project name: `btc-signal-desk-git`
- GitHub repository: `AIPeterLab/btc-cycle-signal-desk`
- Production branch: `main`
- Framework preset: `None`
- Build command: leave blank
- Build output directory: `/`
- Root directory: leave blank / repository root
- Environment variables: none
- Custom domain: `btc.aipeterlab.com`

The dashboard uses relative paths for `data/signals.json` and `data/signals.csv`, so it works from the Cloudflare root domain without code changes.

## Disclaimer

This is a rules-based tracking dashboard for a studied strategy. It is not financial advice.
