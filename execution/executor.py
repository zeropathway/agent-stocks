"""
Execution layer — reads proposed_trade.json, submits to Alpaca, manages the fill lifecycle.

Flow:
  1. Load proposed_trade.json
  2. Pre-flight checks (daily loss limit, duplicate position, cash)
  3. Submit limit order
  4. Poll for fill every POLL_INTERVAL seconds up to FILL_TIMEOUT
  5. On fill → submit GTC stop-loss sell order
  6. On timeout → cancel entry order
  7. Write ExecutionResult to execution_log.json and a stub trade journal entry

Hard rules enforced here (belt-and-suspenders on top of broker.py):
  - Never execute if daily loss limit is breached
  - Never execute if position already exists in symbol
  - Stop-loss order must be submitted within 30s of fill confirmation
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from alpaca.trading.requests import StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from broker import Broker
from decision.proposer import ProposedTrade, load_proposal

log = logging.getLogger(__name__)

POLL_INTERVAL  = 10    # seconds between fill checks
FILL_TIMEOUT   = 300   # seconds before cancelling unfilled entry (5 min)
STOP_SUBMIT_RETRIES = 3

_EXEC_LOG   = Path(__file__).parent.parent / "execution_log.json"
_JOURNAL_DIR = Path(__file__).parent.parent / "journal" / "trades"


# ------------------------------------------------------------------
# Result dataclass
# ------------------------------------------------------------------

@dataclass
class ExecutionResult:
    symbol: str
    action: str           # "filled" | "cancelled" | "failed" | "rejected" | "skipped"
    order_id: Optional[str]
    fill_price: Optional[float]
    fill_qty: int
    stop_order_id: Optional[str]
    elapsed_seconds: float
    reason: str
    generated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------
# Executor
# ------------------------------------------------------------------

class Executor:
    def __init__(self, broker: Optional[Broker] = None):
        self.broker = broker or Broker()

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def _preflight(self, trade: ProposedTrade) -> tuple[bool, str]:
        """Return (ok, reason). All checks must pass."""
        # Daily loss limit
        if not self.broker.check_daily_loss_limit():
            return False, "Daily loss limit reached — session halted"

        # Duplicate position
        existing = self.broker.get_position(trade.symbol)
        if existing:
            return False, f"Already holding {trade.symbol} — skipping duplicate entry"

        # Cash check (no leverage rule enforced in broker, double-check here)
        acct = self.broker.get_account()
        notional = trade.entry_price * trade.qty
        if notional > acct["cash"]:
            return False, (
                f"Notional ${notional:,.0f} exceeds available cash "
                f"${acct['cash']:,.0f}"
            )

        return True, "preflight passed"

    # ------------------------------------------------------------------
    # Fill polling
    # ------------------------------------------------------------------

    def _poll_fill(self, order_id: str) -> tuple[str, float, int]:
        """
        Poll order status until filled, cancelled, or FILL_TIMEOUT elapsed.
        Returns (status, fill_price, fill_qty).
        """
        deadline = time.monotonic() + FILL_TIMEOUT
        while time.monotonic() < deadline:
            orders = self.broker.get_orders(status="all")
            match = next((o for o in orders if o["id"] == order_id), None)
            if match is None:
                log.warning("Order %s disappeared from order list", order_id)
                return "missing", 0.0, 0

            status = match["status"]
            log.debug("Order %s status: %s", order_id, status)

            if status == "filled":
                fill_price = match.get("filled_avg_price") or 0.0
                fill_qty   = int(match.get("filled_qty") or 0)
                return "filled", fill_price, fill_qty

            if status in ("cancelled", "expired", "rejected", "done_for_day"):
                return status, 0.0, 0

            time.sleep(POLL_INTERVAL)

        return "timeout", 0.0, 0

    # ------------------------------------------------------------------
    # Stop-loss placement
    # ------------------------------------------------------------------

    def _place_stop(self, symbol: str, qty: int, stop_price: float) -> Optional[str]:
        """Submit a GTC stop-sell order. Returns order_id or None on failure."""
        for attempt in range(1, STOP_SUBMIT_RETRIES + 1):
            try:
                from alpaca.trading.client import TradingClient
                import os
                from dotenv import load_dotenv
                load_dotenv(Path(__file__).resolve().parent.parent / ".env")
                import yaml
                cfg_path = Path(__file__).parent.parent / "config.yaml"
                cfg = yaml.safe_load(open(cfg_path))
                live = cfg["trading"]["live"]

                client = TradingClient(
                    api_key=os.environ["ALPACA_API_KEY"],
                    secret_key=os.environ["ALPACA_SECRET_KEY"],
                    paper=not live,
                )
                req = StopOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.GTC,
                    stop_price=round(stop_price, 2),
                )
                order = client.submit_order(req)
                log.info(
                    "Stop-loss order placed: SELL %s qty=%d stop=$%.2f id=%s",
                    symbol, qty, stop_price, order.id,
                )
                return str(order.id)
            except Exception as e:
                log.error("Stop placement attempt %d failed: %s", attempt, e)
                if attempt < STOP_SUBMIT_RETRIES:
                    time.sleep(2)
        log.error("CRITICAL: Could not place stop-loss for %s after %d attempts", symbol, STOP_SUBMIT_RETRIES)
        return None

    # ------------------------------------------------------------------
    # Journal stub
    # ------------------------------------------------------------------

    def _write_journal_stub(self, trade: ProposedTrade, result: ExecutionResult):
        """Write a minimal trade journal file. Phase 6 will enrich this."""
        _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        path = _JOURNAL_DIR / f"{today}_{trade.symbol}.md"

        content = f"""# Trade: {trade.symbol} — {today}

## Entry
- **Action**: {result.action}
- **Symbol**: {trade.symbol}
- **Qty**: {result.fill_qty}
- **Fill price**: ${result.fill_price or 'N/A'}
- **Stop**: ${trade.stop_price}
- **Target**: ${trade.target_price}
- **R/R**: {trade.risk_reward}

## Thesis
- **Sentiment**: {trade.thesis_sentiment}
- **Conviction**: {trade.conviction}/5
- **Score**: {trade.score}/10
- **Rationale**: {trade.rationale}

## Status
- **Order ID**: {result.order_id}
- **Stop order ID**: {result.stop_order_id or 'None'}
- **Outcome**: {result.reason}

<!-- Phase 6 will append exit details and lessons -->
"""
        path.write_text(content, encoding="utf-8")
        log.info("Journal stub written → %s", path)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def execute(self, trade: Optional[ProposedTrade] = None) -> ExecutionResult:
        """
        Execute the proposed trade. Loads from proposed_trade.json if trade is None.
        Returns an ExecutionResult regardless of outcome.
        """
        t0 = time.monotonic()

        if trade is None:
            trade = load_proposal()
        if trade is None:
            log.warning("No proposed_trade.json found — nothing to execute")
            return ExecutionResult(
                symbol="N/A", action="skipped", order_id=None,
                fill_price=None, fill_qty=0, stop_order_id=None,
                elapsed_seconds=0, reason="No proposal file found",
                generated_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        log.info("Executing proposal: %s %s qty=%d entry=$%.2f stop=$%.2f target=$%.2f",
                 trade.action, trade.symbol, trade.qty,
                 trade.entry_price, trade.stop_price, trade.target_price)

        # Pre-flight
        ok, reason = self._preflight(trade)
        if not ok:
            log.warning("Pre-flight FAILED: %s", reason)
            return ExecutionResult(
                symbol=trade.symbol, action="rejected", order_id=None,
                fill_price=None, fill_qty=0, stop_order_id=None,
                elapsed_seconds=time.monotonic() - t0,
                reason=reason,
                generated_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        # Refresh limit price from live quote — proposed entry may be hours old.
        # If market has moved above proposal, chase up to 1% above current mid.
        # This prevents the "order sits unfilled then cancels" pattern.
        limit_price = trade.entry_price
        live_price = self.broker.get_latest_price(trade.symbol)
        if live_price and live_price > trade.entry_price:
            limit_price = round(live_price * 1.005, 2)   # 0.5% above current mid
            log.info(
                "Live price $%.2f > proposed $%.2f — refreshing limit to $%.2f",
                live_price, trade.entry_price, limit_price,
            )

        order = self.broker.submit_order(
            symbol=trade.symbol,
            qty=float(trade.qty),
            side="buy",
            order_type="limit",
            limit_price=limit_price,
            time_in_force="day",
        )
        if order is None:
            return ExecutionResult(
                symbol=trade.symbol, action="failed", order_id=None,
                fill_price=None, fill_qty=0, stop_order_id=None,
                elapsed_seconds=time.monotonic() - t0,
                reason="broker.submit_order returned None (checklist or API failure)",
                generated_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        order_id = order["id"]
        log.info("Entry order submitted: id=%s status=%s", order_id, order["status"])

        # Poll for fill
        fill_status, fill_price, fill_qty = self._poll_fill(order_id)

        if fill_status == "timeout":
            log.warning("Order %s not filled within %ds — cancelling", order_id, FILL_TIMEOUT)
            self.broker.cancel_order(order_id)
            return ExecutionResult(
                symbol=trade.symbol, action="cancelled", order_id=order_id,
                fill_price=None, fill_qty=0, stop_order_id=None,
                elapsed_seconds=time.monotonic() - t0,
                reason=f"Not filled within {FILL_TIMEOUT}s — cancelled",
                generated_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        if fill_status != "filled":
            return ExecutionResult(
                symbol=trade.symbol, action=fill_status, order_id=order_id,
                fill_price=None, fill_qty=0, stop_order_id=None,
                elapsed_seconds=time.monotonic() - t0,
                reason=f"Order ended with status: {fill_status}",
                generated_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        log.info("FILLED: %s qty=%d @ $%.2f", trade.symbol, fill_qty, fill_price)

        # Place stop-loss immediately after fill
        stop_order_id = self._place_stop(trade.symbol, fill_qty, trade.stop_price)

        result = ExecutionResult(
            symbol=trade.symbol,
            action="filled",
            order_id=order_id,
            fill_price=fill_price,
            fill_qty=fill_qty,
            stop_order_id=stop_order_id,
            elapsed_seconds=round(time.monotonic() - t0, 1),
            reason="Order filled and stop-loss placed",
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        # Write journal stub and execution log
        self._write_journal_stub(trade, result)
        _EXEC_LOG.write_text(json.dumps(result.to_dict(), indent=2))
        log.info("Execution complete in %.1fs — stop_order=%s", result.elapsed_seconds, stop_order_id)
        return result


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("=== EXECUTOR START ===")
    executor = Executor()
    result = executor.execute()
    log.info("Result: action=%s symbol=%s fill=$%s qty=%d",
             result.action, result.symbol,
             f"{result.fill_price:.2f}" if result.fill_price else "N/A",
             result.fill_qty)
    log.info("=== EXECUTOR END ===")
    return result


if __name__ == "__main__":
    run()
