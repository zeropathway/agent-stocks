"""
Pre-market routine — runs 08:00 ET daily.
Phase 4: full pipeline — data → indicators → research → score → rules → trade proposal.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from broker import Broker
from data.fetcher import fetch_bars
from data.indicators import add_all_indicators, get_signal_summary
from data.universe import EQUITY_SYMBOLS
from research.news import fetch_headlines
from research.earnings import fetch_earnings_info
from research.thesis import generate_thesis
from decision.scorer import rank_candidates
from decision.rules import filter_candidates
from decision.proposer import run_decision_pass
from execution.executor import Executor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_MAX_THESIS_TICKERS = 5   # top N by signal score get full Claude research


def run():
    log.info("=== PRE-MARKET ROUTINE START ===")
    broker = Broker()

    if not broker.check_daily_loss_limit():
        log.warning("Daily loss limit already reached — no new entries today")
        return

    acct = broker.get_account()
    equity = acct["equity"]
    log.info("Equity: $%.2f | Cash: $%.2f", equity, acct["cash"])

    open_positions = broker.get_positions()
    log.info("Open positions: %d / 5", len(open_positions))

    # ── Phase 2: Market data + indicators ─────────────────────────────
    log.info("Fetching market data for %d symbols...", len(EQUITY_SYMBOLS))
    bars = fetch_bars(EQUITY_SYMBOLS, days=300)

    signals: dict = {}
    for sym, df in bars.items():
        if df.empty:
            continue
        df = add_all_indicators(df)
        signals[sym] = get_signal_summary(df)

    bullish_count = sum(1 for s in signals.values() if s["bias"] == "bullish")
    bearish_count = sum(1 for s in signals.values() if s["bias"] == "bearish")
    log.info("Market breadth: %d bullish / %d bearish out of %d scanned",
             bullish_count, bearish_count, len(signals))

    # ── Phase 3: Research on top N candidates ─────────────────────────
    # Pre-rank by raw bullish signal count to prioritise Claude calls
    pre_ranked = sorted(signals.items(),
                        key=lambda kv: kv[1]["bullish_signals"] - kv[1]["bearish_signals"],
                        reverse=True)

    log.info("Running research pass on top %d candidates...", _MAX_THESIS_TICKERS)
    theses: dict = {}
    for sym, signal in pre_ranked[:_MAX_THESIS_TICKERS]:
        headlines = fetch_headlines(sym, max_items=8)
        earnings  = fetch_earnings_info(sym)
        thesis    = generate_thesis(sym, signal, headlines, earnings)
        theses[sym] = thesis
        log.info("  %s: %s conviction=%d earnings_risk=%s",
                 sym, thesis.sentiment, thesis.conviction, thesis.earnings_risk)

    # ── Phase 4: Score → rules → propose ──────────────────────────────
    ranked   = rank_candidates(signals, theses)
    approved = filter_candidates(ranked, signals, theses, open_positions, equity)

    log.info("Scoring complete — %d/%d passed all rules",
             len(approved), len(ranked))
    for detail, rules in approved:
        log.info("  APPROVED: %s score=%.1f", detail.symbol, detail.total)
    for detail in ranked:
        if not any(d.symbol == detail.symbol for d, _ in approved):
            log.info("  REJECTED: %s score=%.1f", detail.symbol, detail.total)

    trade = run_decision_pass(ranked, approved, signals, theses, equity)

    if trade:
        log.info(
            "PROPOSED TRADE: %s %s qty=%d entry=$%.2f stop=$%.2f target=$%.2f R/R=%.1f",
            trade.action.upper(), trade.symbol, trade.qty,
            trade.entry_price, trade.stop_price, trade.target_price, trade.risk_reward,
        )
        log.info("  Rationale: %s", trade.rationale)

        # ── Phase 5: Execute the proposal ────────────────────────────
        log.info("Submitting to executor...")
        executor = Executor(broker=broker)
        result = executor.execute(trade)
        log.info(
            "Execution result: action=%s fill=$%s qty=%d stop_order=%s",
            result.action,
            f"{result.fill_price:.2f}" if result.fill_price else "N/A",
            result.fill_qty,
            result.stop_order_id or "none",
        )
    else:
        log.info("No trade proposed this session.")

    log.info("=== PRE-MARKET ROUTINE END ===")
    return {"signals": signals, "theses": theses, "ranked": ranked,
            "approved": approved, "trade": trade}


if __name__ == "__main__":
    run()
