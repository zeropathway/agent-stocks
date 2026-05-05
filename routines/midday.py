"""
Mid-day routine — runs 12:30 ET daily.
Phase 5: checks open positions vs. stop levels; cancels stale orders; re-checks loss limit.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from broker import Broker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def run():
    log.info("=== MID-DAY ROUTINE START ===")
    broker = Broker()

    # Loss limit check — halt if breached since open
    if not broker.check_daily_loss_limit():
        log.warning("Daily loss limit reached — cancelling all open orders, no new entries")
        cancelled = broker.cancel_all_orders()
        log.info("Cancelled %d open orders", cancelled)
        return

    positions = broker.get_positions()
    log.info("Open positions: %d", len(positions))
    for p in positions:
        price = broker.get_latest_price(p["symbol"])
        log.info(
            "  %s qty=%s avg=$%.2f now=$%.2f uPnL=$%.2f (%.1f%%)",
            p["symbol"], p["qty"], p["avg_entry_price"],
            price or 0, p["unrealized_pl"], p["unrealized_plpc"] * 100,
        )

    orders = broker.get_orders(status="open")
    log.info("Open orders: %d", len(orders))
    for o in orders:
        log.info("  %s %s %s qty=%s status=%s",
                 o["side"], o["symbol"], o["type"], o["qty"], o["status"])

    # Cancel any entry orders older than this session (stale GTC entries)
    # Stop-loss orders (GTC sell stops) are intentionally left open
    stale_entries = [
        o for o in orders
        if o["side"] == "buy" and o["type"] == "limit"
    ]
    if stale_entries:
        log.info("Cancelling %d stale limit entry orders", len(stale_entries))
        for o in stale_entries:
            broker.cancel_order(o["id"])

    log.info("=== MID-DAY ROUTINE END ===")
    # TODO Phase 6: re-score open positions, tighten stops if profitable


if __name__ == "__main__":
    run()
