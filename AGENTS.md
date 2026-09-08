# Codex Project Instructions

## Scope

This repository is the public BTC Cycle Signal Desk. Preserve the strategy rules and deployment workflow documented in `README.md` and `CLOUDFLARE_PAGES.md`.

## Durable Rules

- Treat `README.md` as the authoritative summary of the signal methodology.
- Do not add QQQ, QLD, SPY, SSO, TQQQ, MACD, EMA-crossover, or five-day-DCA rules to this dashboard.
- Keep `.github/workflows/daily-update.yml` dispatch-only. The central AIPeterLab Cloudflare Worker owns the schedule; do not add a GitHub Actions `schedule:` trigger.
- Run `python scripts/update_signals.py` to refresh `data/signals.json` and `data/signals.csv`.
- The updater must remain usable without API keys. Public Yahoo Finance and CoinMetrics endpoints are its data sources.
- Treat the 200-week SMA, EMA50, EMA200, realized price, and estimated electrical cost as context only; they must not override the strategy allocation.
- Preserve relative dashboard data paths so the static site continues to work at the Cloudflare Pages root.
- Never commit `.env` files, credentials, access tokens, private keys, Wrangler account/cache data, Codex state, or generated Python bytecode.

## Verification

- After Python changes, run `python -m py_compile scripts/update_signals.py`.
- When network access is available, run `python scripts/update_signals.py` and review both generated data files before committing them.
- For dashboard changes, serve the repository root with a local static HTTP server and verify `index.html` loads `data/signals.json` successfully.
- Review `git diff --check` and `git status --short` before committing.

## Operations

Deployment and recovery details are in `CLOUDFLARE_PAGES.md` and `MIGRATION_HANDOFF.md`. Account credentials and Cloudflare/GitHub authorization are intentionally external to this repository.
