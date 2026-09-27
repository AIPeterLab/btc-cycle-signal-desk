---
name: etf-flow-lag
description: Reproduce, review, or extend the BTC Signal Desk's monthly lagged-IBIT-flow allocation backtest and compare it with QQQ or BTC buy-and-hold. Use for requests about the ETF-flow lag strategy, its source data, allocation rules, 2026 results, or repeatable validation; do not use for the dashboard's authoritative halving-cycle strategy.
---

# ETF Flow Lag

Use this skill for research on the separate ETF-flow allocation experiment. Never present
it as part of the live BTC Cycle Signal Desk strategy unless the user explicitly authorizes
that strategy change.

## Workflow

1. Read [references/methodology.md](references/methodology.md) before calculating,
   reviewing, or changing results.
2. Run `python scripts/backtest_ibit_flow_allocation.py` from the repository root.
3. Review `data/ibit_flow_backtest_2026.json` and
   `data/ibit_flow_backtest_2026.csv`; do not rely on console output alone.
4. Confirm the final month is completed, prior-month flows are used without lookahead,
   the comparison periods match, and a negative prior-month flow produces 0% BTC.
5. Run `python -m py_compile scripts/backtest_ibit_flow_allocation.py` and
   `git diff --check` after code changes.

## Boundaries

- The named signal is IBIT monthly net flow, not aggregate US spot-Bitcoin ETF flow.
  Ask or clearly label the change before substituting aggregate flows.
- Keep the unallocated share in non-interest-bearing cash unless the user requests a
  different cash return assumption.
- Exclude the current incomplete calendar month.
- Treat the analysis as a backtest, not proof of causality or investment advice.
- Preserve the main dashboard's rules in `README.md`; this experimental strategy must
  not override its allocation.
