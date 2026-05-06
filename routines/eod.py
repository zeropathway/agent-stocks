"""
End-of-day routine — runs 15:50 ET daily.
Phase 6: detects closed positions, writes full journals, updates lessons.md and market_context.md.
"""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from broker import Broker
from journal.writer import write_exit, ExitEvent, purge_old_journals
from journal.lessons_updater import update_lessons
from context.updater import update_context
from data.fetcher import fetch_bars
from data.indicators import add_all_indicators, get_signal_summary
from data.universe import EQUITY_SYMBOLS
from integrations.clickup import post_daily_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_EXEC_LOG     = Path(__file__).parent.parent / "execution_log.json"
_JOURNAL_DIR  = Path(__file__).parent.parent / "journal" / "trades"


def _detect_exits(broker: Broker) -> list[ExitEvent]:
    """
    Compare open positions against last execution log to detect fills that
    have since been closed (stop hit, target hit, or manual close).
    Returns a list of ExitEvent for positions that closed since last check.
    """
    if not _EXEC_LOG.exists():
        return []

    try:
        last = json.loads(_EXEC_LOG.read_text(encoding="utf-8"))
    except Exception:
        return []

    # Only process logs from today — reject stale entries from previous sessions
    generated_at = last.get("generated_at", "")
    try:
        from datetime import timezone as _tz
        log_date = datetime.fromisoformat(generated_at).astimezone(_tz.utc).date()
    except Exception:
        log_date = None
    if log_date != date.today():
        log.info(
            "execution_log.json is from %s — skipping stale exit detection", log_date
        )
        return []

    symbol = last.get("symbol")
    fill_price = last.get("fill_price")
    fill_qty = last.get("fill_qty", 0)

    if not symbol or not fill_price or not fill_qty:
        return []

    # Check if we still hold the position
    position = broker.get_position(symbol)
    if position is not None:
        log.info("Still holding %s — no exit to record", symbol)
        return []

    # Position closed since last log — compute P&L from current price or last known
    current_price = broker.get_latest_price(symbol)
    exit_price = current_price or fill_price
    pnl_dollars = (exit_price - float(fill_price)) * int(fill_qty)
    pnl_pct = pnl_dollars / (float(fill_price) * int(fill_qty)) * 100

    return [ExitEvent(
        symbol=symbol,
        exit_price=exit_price,
        exit_qty=int(fill_qty),
        exit_reason="eod_close",
        exit_date=date.today().isoformat(),
        pnl_dollars=pnl_dollars,
        pnl_pct=pnl_pct,
    )]


def run():
    log.info("=== END-OF-DAY ROUTINE START ===")
    broker = Broker()

    acct = broker.get_account()
    log.info("Final equity: $%.2f | Cash: $%.2f", acct["equity"], acct["cash"])

    # Cancel any remaining limit buy orders; leave stop-sells (GTC) open
    orders = broker.get_orders(status="open")
    entry_orders = [o for o in orders if o["side"] == "buy"]
    if entry_orders:
        log.info("Cancelling %d open entry orders at EOD", len(entry_orders))
        for o in entry_orders:
            broker.cancel_order(o["id"])

    positions = broker.get_positions()
    if positions:
        log.info("Holding %d position(s) overnight:", len(positions))
        total_pnl = 0.0
        for p in positions:
            log.info("  %s qty=%s uPnL=$%.2f (%.1f%%)",
                     p["symbol"], p["qty"], p["unrealized_pl"], p["unrealized_plpc"] * 100)
            total_pnl += p["unrealized_pl"]
        log.info("Total unrealized P&L: $%.2f", total_pnl)
    else:
        log.info("Flat into close — no open positions")

    # ── Phase 6: Journal exits ────────────────────────────────────────
    exits = _detect_exits(broker)
    for exit_event in exits:
        log.info("Recording exit: %s pnl=$%.2f", exit_event.symbol, exit_event.pnl_dollars)
        write_exit(exit_event)

    # ── Phase 6: Update lessons.md ────────────────────────────────────
    log.info("Updating lessons.md...")
    lessons = update_lessons()
    if lessons:
        log.info("Lessons updated (%d chars)", len(lessons))

    # ── Phase 6: Rewrite market_context.md ───────────────────────────
    log.info("Updating market_context.md...")
    try:
        bars = fetch_bars(EQUITY_SYMBOLS, days=300)
        signals = {}
        for sym, df in bars.items():
            if not df.empty:
                df = add_all_indicators(df)
                signals[sym] = get_signal_summary(df)

        session_pnl = sum(e.pnl_dollars for e in exits)
        update_context(
            signals=signals,
            theses={},
            session_pnl=session_pnl,
            lessons_snippet=lessons[:300] if lessons else "",
        )
        log.info("Market context updated")
    except Exception as e:
        log.error("Context update failed: %s", e)

    # ── Retention: purge old journals ────────────────────────────────
    purge_old_journals()

    # ── Phase 7: ClickUp daily summary ───────────────────────────────
    session_pnl = sum(e.pnl_dollars for e in exits)
    posted = post_daily_summary(
        session_pnl=session_pnl,
        trades_taken=len(exits),
        extra_notes=lessons[:300] if lessons else "",
    )
    if posted:
        log.info("ClickUp summary posted")
    else:
        log.info("ClickUp skipped (credentials not set or posting disabled)")

    log.info("Session date: %s", date.today().isoformat())
    log.info("=== END-OF-DAY ROUTINE END ===")


if __name__ == "__main__":
    run()
