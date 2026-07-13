# BTC Cycle Signal Desk

Public dashboard for the Bitcoin four-year-cycle tracking system.

## Signal Rule

- Signal source is Bitcoin cycle day, anchored to the most recent confirmed Bitcoin halving date.
- Hold BTC from halving day through day 540, inclusive.
- Hold Cash from day 541 until the next confirmed halving.
- For the current studied cycle, the active halving date is 2024-04-20 and day 540 is 2025-10-12.
- The 200-week SMA, realized price, and estimated electrical cost per BTC are context only. They do not override the cycle-day signal.

This dashboard does not use QQQ, QLD, SPY, SSO, TQQQ, MACD, EMA, or 5-day DCA rules.

## Files

- `index.html` - static public dashboard.
- `data/signals.json` - current status, context values, and recent history.
- `data/signals.csv` - recent signal history.
- `scripts/update_signals.py` - no-key updater using public BTC-USD and CoinMetrics data.
- `.github/workflows/daily-update.yml` - scheduled GitHub Actions refresh.
- `Real_Account_Tracking_System.doc` - plain-language operating manual from the source project.

## Refresh

Run the updater locally:

```powershell
python scripts/update_signals.py
```

The GitHub Actions workflow runs daily at `22:15 UTC`, which is `18:15 America/New_York` during daylight saving time, and can also be run manually with `workflow_dispatch`.

## Cloudflare Pages

Recommended production host: Cloudflare Pages at `https://btc.aipeterlab.com`.

Use Git integration so the existing GitHub refresh workflow stays unchanged:

- Cloudflare project name: `btc-signal-desk`
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
