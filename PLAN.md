# Trading Agent — Build Plan

## Phase 1 — Foundation ✅
- [x] Repo scaffold (directories, .env, config.yaml)
- [x] `broker.py` — Alpaca paper wrapper with pre-trade checklist
- [x] `tests/test_broker.py` — auth, balance, order round-trip
- [x] `README.md`, `requirements.txt`, `PLAN.md`
- [x] Hello-world order on paper account

---

## Phase 2 — Market Data Ingestion + Indicators ✅
- [x] `data/fetcher.py` — fetch OHLCV bars from Alpaca data API, cache to parquet
- [x] `data/indicators.py` — RSI(14), SMA(20/50/200), MACD(12/26/9), ATR(14) via `ta` library
- [x] Cache invalidation: re-fetch if parquet is older than `config.yaml cache_ttl_minutes`
- [x] `data/universe.py` — maintain a watchlist (top S&P500 + crypto pairs)
- [x] Unit tests: 35 passing, 1 skipped (order round-trip when market closed)

---

## Phase 3 — Research Layer (News + Earnings) ✅
- [x] `research/news.py` — Yahoo Finance RSS + finviz headlines, deduplicated and sorted
- [x] `research/earnings.py` — yfinance earnings calendar (handles ETF/dict edge case)
- [x] `research/thesis.py` — Claude Sonnet via tool_use → typed Thesis dataclass, 30-min cache
  - Fields: sentiment, conviction (1–5), catalyst, risk_factors, technical_alignment, earnings_risk, summary
- [x] Thesis cached to `data/cache/thesis_{ticker}_{date}.json`
- [x] 21 tests passing; live premarket run produces real Claude theses (NVDA: 15d to earnings flagged)

---

## Phase 4 — Decision Layer ✅
- [x] `decision/scorer.py` — signal + thesis → numeric score [-10, +10] with full component breakdown
- [x] `decision/rules.py` — 7 hard filters: min score, above SMA200, RSI cap, earnings blackout, no duplicate position, max positions, sector exposure
- [x] `decision/proposer.py` — Claude Sonnet (conv≤4) / Opus (conv=5) → `proposed_trade.json` via tool_use; geometry validated (stop < entry, target > entry, R/R ≥ 1.5)
- [x] `proposed_trade.json` written atomically; Phase 5 executor reads it
- [x] Escalation: Sonnet default, Opus when conviction=5; 20 tests all passing
- [x] Live run: BUY AAPL qty=79 entry=$279.04 stop=$266.39 target=$298.00 R/R=1.5

---

## Phase 5 — Execution Layer ✅
- [x] `execution/executor.py` — preflight (loss limit, duplicate, cash), limit order submission, 10s poll loop (5 min timeout), GTC stop-loss after fill, cancel stale
- [x] `execution_log.json` written on every execution attempt
- [x] Journal stub written to `journal/trades/` on fill (Phase 6 enriches it)
- [x] Routines updated: premarket calls executor; midday cancels stale entries; EOD cancels entry orders, leaves stop-sells open
- [x] 11 execution tests (unit + integration), 87 total passing
- [x] Model routing: Opus 4.7 for ALL trade proposals; Sonnet 4.6 for thesis research

---

## Phase 6 — Journaling + Self-Update Loop ✅
- [x] `journal/writer.py` — entry (on fill) + exit (on close) markdown journals; `journal_dir` param for clean testing
- [x] `journal/lessons_updater.py` — Claude Sonnet reads last 5 completed journals → appends dated lessons to lessons.md
- [x] `context/updater.py` — Claude Sonnet rewrites market_context.md each EOD (regime, leading/lagging sectors, key risks, bias)
- [x] EOD routine: detects exits via execution_log.json, writes journals, updates lessons + context, purges 90d+ files
- [x] 16 journal tests + 103 total passing; live EOD run: +$38.30 P&L, 1744-char lessons, full context briefing generated

---

## Phase 7 — Routines + ClickUp Webhook ✅
- [x] `routines/scheduler.py` — local 24/7 loop; weekday + holiday guard; clean SIGTERM shutdown; logs to scheduler.log
- [x] `integrations/clickup.py` — POST daily summary task to ClickUp list (set CLICKUP_API_TOKEN + CLICKUP_LIST_ID in .env)
- [x] ClickUp wired into EOD routine
- [x] Git repo initialised + pushed: https://github.com/zeropathway/trading-agent (private)
- [x] Three CCR routines created (disabled — enable after setting env vars in CCR environment):
      - Pre-Market  → trig_014jmsN7Fz4Ey8RvCPpU2xH4  (08:00 ET / 12:00 UTC weekdays)
      - Mid-Day     → trig_01Etj5vqjZGXaxugdKahvwm2   (12:30 ET / 16:30 UTC weekdays)
      - End of Day  → trig_0137ThCgdGyjTspDosmo6NzV   (15:50 ET / 19:50 UTC weekdays)
- [x] 119 tests passing (16 Phase 7 tests added)

---

## Future / Backlog
- [ ] Crypto venue via `ccxt` (Coinbase or Kraken)
- [ ] Options flow scanner (Phase 2+ only, requires config flag)
- [ ] Backtesting harness against historical parquet data
- [ ] Slack/Telegram alert channel for fills and halt events
- [ ] Live trading flip (after 30-day paper track record)
