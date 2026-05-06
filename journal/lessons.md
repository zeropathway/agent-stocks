# Lessons Learned

> This file is updated by the agent after every trading session.
> Each entry is appended by `journal/lessons_updater.py` (Phase 6).

---

## Session: 2026-05-05 — Initial Setup

- Paper account initialized with $100,000 equity.
- Phase 1 complete: broker connectivity verified, risk rules encoded in code.
- No trades taken. Awaiting Phase 2 (market data + indicators).


---

## Session: 2026-05-05

**What Worked**
- Entry technical alignment was solid on AAPL (2024-01-15): RSI at 61.8 (below overbought), price above SMA200, and bullish MACD confluence — this setup produced a clean long entry with no adverse excursion noted; preserve this multi-indicator confirmation checklist as a required entry gate.
- R/R of 1.93:1 on AAPL was correctly structured before entry — the trade was profitable, confirming that pre-trade ratio discipline was properly applied.

**What to Improve**
- AAPL was held from 2024-01-15 to 2026-05-05 (16+ months) and exited at only +0.26R — the position vastly underperformed its 1.93R target while capital was locked up for over a year; a time-based stop or R-multiple review trigger (e.g., "if not at +0.5R within 60 days, reassess and consider exit") would have freed capital far earlier.
- Exit reason was `eod_close` — not target hit, not stop hit, not a thesis-based decision; this indicates no active trade management occurred over the holding period, which is a process failure regardless of the win label.
- The $303 target was never reached despite a bullish thesis with 4/5 conviction, suggesting the catalyst (iPhone emerging market beat) either didn't materialize as expected or was already priced in at entry — pre-trade catalyst timing validation is missing from the journal.

**Rule Changes to Consider**
- Add a **maximum holding period of 60 calendar days** with a mandatory thesis re-evaluation; if the trade is below +0.5R at day 60, close or scale down unless a new documented catalyst justifies continuation.
- Require a **catalyst confirmation checkpoint** (e.g., earnings date logged at entry) so exits can be managed around the actual event rather than drifting to an `eod_close` default.


---

## Session: 2026-05-06

- **What worked: Multi-confirmation trend entry (AAPL 2024-01-15)** — Entry required price above SMA200 + bullish MACD + RSI in 60-65 zone (61.8). This trifecta produced a clean +0.54R win without stopping out. Keep this stack as a baseline filter.
- **What worked: RSI in mid-60s, not overbought (AAPL)** — Entering at RSI 61.8 left room to run. Avoiding chases above 70 likely prevented an immediate fade.

- **What to improve: R/R below 2.0 on a 4/5 conviction trade (AAPL)** — Setup was 1.93 R/R; high-conviction trades should demand ≥2.0. The trade also exited via `eod_close` at +0.54R, well short of the $303 target — meaning we took a fraction of planned reward.
- **What to improve: Risk per trade was 5.4% of notional (AAPL)** — Stop at $262 vs entry $276.83 is ~2.3 ATR, which is fine, but $148 risk on a $2,768 position is heavy if scaled across a portfolio. Size the position to risk, not pick a round share count.
- **What to improve: Exit logic — `eod_close` on a trend trade (AAPL)** — A trending bullish thesis with a $303 target shouldn't be closed on EOD mechanics at $284.54. We left ~$18/share of planned upside on the table.

- **Rule change: Require R/R ≥ 2.0 for conviction ≥4/5; reject or re-price the stop/target otherwise.**
- **Rule change: Cap per-trade risk at 1–2% of account (not notional), and replace `eod_close` exits on open-trend trades with a trailing stop (e.g., 1.5×ATR or below prior swing low) so winners can reach target.**
