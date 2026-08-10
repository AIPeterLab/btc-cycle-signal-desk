# BTC Cycle Signal Desk

Public dashboard for the Bitcoin four-year-cycle tracking system.

## Signal Rule

- Signal source is the Bitcoin halving date, with fixed pre-halving and post-halving day counts.
- Buy / hold BTC from 500 days before the Bitcoin halving date through day 540 after the halving date, inclusive.
- Hold Cash before the pre-halving buy date and after the day-540 sell date.
- For the current studied cycle, the active halving date is 2024-04-20, the buy date is 2022-12-07, and the day-540 sell date is 2025-10-12.
- The 200-week SMA, BTC EMA50, BTC EMA200, realized price, and estimated electrical cost per BTC are context only. They do not override the buy-window signal.

This dashboard does not use QQQ, QLD, SPY, SSO, TQQQ, MACD, EMA crossover, or 5-day DCA rules.

## Files

- `index.html` - static public dashboard.
- `data/signals.json` - current status, context values, and recent history.
- `data/signals.csv` - recent signal history.
- `scripts/update_signals.py` - no-key updater using public BTC-USD and CoinMetrics data.
- `.github/workflows/daily-update.yml` - dispatch-only GitHub Actions refresh.
- `_headers` - Cloudflare Pages cache rules matching the QLD/SSO signal desks.
- `Real_Account_Tracking_System.doc` - plain-language operating manual from the source project.

## Refresh

Run the updater locally:

```powershell
python scripts/update_signals.py
```

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
