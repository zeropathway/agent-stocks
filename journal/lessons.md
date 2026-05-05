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
