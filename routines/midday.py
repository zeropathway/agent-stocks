"""
Mid-day routine — runs 12:30 ET daily.

Actions:
  1. Loss-limit check — cancel all orders and halt if daily limit breached.
  2. Log open positions with unrealised P&L.
  3. Cancel stale limit entry orders from this morning.
  4. Tighten stop-loss to breakeven on any position up ≥5% from entry.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from broker import Broker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_BREAKEVEN_TRIGGER_PCT = 0.05   # move stop to breakeven when position is up 5%+
_BREAKEVEN_CUSHION     = 0.002  # stop placed 0.2% above entry (covers commissions)


def _tighten_stops(broker: Broker, positions: list[dict]) -> None:
    """
    For each open position that is ≥ BREAKEVEN_TRIGGER_PCT above its average
    entry price, replace the GTC stop-loss with a breakeven stop.
    This locks in a near-zero loss floor once the trade has moved in our favour.
    """
    for p in positions:
        sym       = p["symbol"]
        entry     = float(p["avg_entry_price"])
        qty       = float(p["qty"])
        current   = broker.get_latest_price(sym)

        if not current or entry <= 0:
            continue

        pct_gain = (current - entry) / entry
        if pct_gain < _BREAKEVEN_TRIGGER_PCT:
            continue

        new_stop = round(entry * (1 + _BREAKEVEN_CUSHION), 2)
        log.info(
            "%s is +%.1f%% ($%.2f → $%.2f) — tightening stop to breakeven $%.2f",
            sym, pct_gain * 100, entry, current, new_stop,
        )
        ok = broker.move_stop(sym, qty, new_stop)
        if ok:
            log.info("Breakeven stop set for %s @ $%.2f", sym, new_stop)
        else:
            log.warning("Failed to set breakeven stop for %s", sym)


def run():
    log.info("=== MID-DAY ROUTINE START ===")
    broker = Broker()

    # 1. Loss limit check
    if not broker.check_daily_loss_limit():
        log.warning("Daily loss limit reached — cancelling all open orders, no new entries")
        cancelled = broker.cancel_all_orders()
        log.info("Cancelled %d open orders", cancelled)
        return

    # 2. Log open positions
    positions = broker.get_positions()
    log.info("Open positions: %d", len(positions))
    for p in positions:
        price = broker.get_latest_price(p["symbol"])
        log.info(
            "  %s qty=%s avg=$%.2f now=$%.2f uPnL=$%.2f (%.1f%%)",
            p["symbol"], p["qty"], p["avg_entry_price"],
            price or 0, p["unrealized_pl"], p["unrealized_plpc"] * 100,
        )

    # 3. Cancel stale limit entry orders from this morning
    orders = broker.get_orders(status="open")
    log.info("Open orders: %d", len(orders))
    for o in orders:
        log.info(
            "  %s %s %s qty=%s status=%s",
            o["side"], o["symbol"], o["type"], o["qty"], o["status"],
        )

    stale_entries = [
        o for o in orders
        if o["side"] == "buy" and o["type"] == "limit"
    ]
    if stale_entries:
        log.info("Cancelling %d stale limit entry order(s)", len(stale_entries))
        for o in stale_entries:
            broker.cancel_order(o["id"])

    # 4. Tighten stops to breakeven where applicable
    if positions:
        _tighten_stops(broker, positions)

    log.info("=== MID-DAY ROUTINE END ===")


if __name__ == "__main__":
    run()
