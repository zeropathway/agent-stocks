"""
Alpaca broker wrapper — paper trading by default.
All order submission goes through submit_order(), which runs a pre-trade checklist
that fails closed (returns None without raising on any rule violation).
"""

import os
import uuid
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def _load_config() -> dict:
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(cfg_path) as f:
        return yaml.safe_load(f)


class PreTradeChecklistFailed(Exception):
    pass


class Broker:
    def __init__(self):
        self.cfg = _load_config()
        live = self.cfg["trading"]["live"]

        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]

        # Hard guard: refuse to use live client unless config explicitly says so
        if live:
            log.warning("LIVE TRADING MODE ACTIVE")
        else:
            log.info("Paper trading mode active")

        self._client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=not live,
        )
        self._data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key,
        )

        self._risk = self.cfg["risk"]
        self._session_loss_halted = False

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        acct = self._client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
            "daytrade_count": acct.daytrade_count,
            "trading_blocked": acct.trading_blocked,
        }

    def get_equity(self) -> float:
        return self.get_account()["equity"]

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "avg_entry_price": float(p.avg_entry_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "side": p.side.value,
            }
            for p in positions
        ]

    def get_open_position_count(self) -> int:
        return len(self._client.get_all_positions())

    def get_position(self, symbol: str) -> Optional[dict]:
        try:
            p = self._client.get_open_position(symbol)
            return {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "avg_entry_price": float(p.avg_entry_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "side": p.side.value,
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_orders(self, status: str = "open") -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        req = GetOrdersRequest(status=status_map.get(status, QueryOrderStatus.OPEN))
        orders = self._client.get_orders(filter=req)
        return [
            {
                "id": str(o.id),
                "client_order_id": o.client_order_id,
                "symbol": o.symbol,
                "qty": float(o.qty) if o.qty else None,
                "side": o.side.value,
                "type": o.order_type.value,
                "status": o.status.value,
                "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": str(o.submitted_at),
            }
            for o in orders
        ]

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(order_id)
            log.info("Cancelled order %s", order_id)
            return True
        except Exception as e:
            log.error("Failed to cancel order %s: %s", order_id, e)
            return False

    def cancel_all_orders(self) -> int:
        cancelled = self._client.cancel_orders()
        count = len(cancelled) if cancelled else 0
        log.info("Cancelled %d orders", count)
        return count

    def close_position(self, symbol: str) -> bool:
        try:
            self._client.close_position(symbol)
            log.info("Closed position %s", symbol)
            return True
        except Exception as e:
            log.error("Failed to close position %s: %s", symbol, e)
            return False

    def move_stop(self, symbol: str, qty: float, new_stop: float) -> bool:
        """
        Replace the GTC stop-loss for a position.
        Cancels all open GTC sell-stop orders for the symbol, then places
        a new one at new_stop. Returns True on success.
        """
        from alpaca.trading.requests import StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        # Cancel existing stop orders for this symbol
        try:
            orders = self.get_orders(status="open")
            for o in orders:
                if (o["symbol"] == symbol and o["side"] == "sell"
                        and "stop" in o.get("type", "")):
                    self.cancel_order(o["id"])
                    log.info("Cancelled old stop order %s for %s", o["id"], symbol)
        except Exception as e:
            log.error("Failed to cancel existing stops for %s: %s", symbol, e)

        # Place new stop
        try:
            req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=round(new_stop, 2),
            )
            order = self._client.submit_order(req)
            log.info(
                "Stop moved: %s qty=%.0f new_stop=$%.2f id=%s",
                symbol, qty, new_stop, order.id,
            )
            return True
        except Exception as e:
            log.error("Failed to place new stop for %s at $%.2f: %s", symbol, new_stop, e)
            return False

    # ------------------------------------------------------------------
    # Pre-trade checklist — fails closed
    # ------------------------------------------------------------------

    def _run_checklist(
        self,
        symbol: str,
        side: str,
        qty: float,
        equity: float,
        notional: float,
    ) -> tuple[bool, str]:
        """Returns (passed, reason). All rules must pass."""
        r = self._risk

        # 1. Paper-only guard
        if self.cfg["trading"]["live"]:
            # Allow, but log loudly
            log.warning("LIVE ORDER: %s %s %s", side, qty, symbol)

        # 2. Session halt check
        if self._session_loss_halted:
            return False, "Session halted: daily loss limit reached"

        # 3. No shorting in v1
        if not r["allow_shorting"] and side == "sell":
            existing = self.get_position(symbol)
            if not existing or float(existing["qty"]) < qty:
                return False, f"Shorting not allowed (v1 constraint)"

        # 4. No leverage
        if not r["allow_leverage"]:
            acct = self.get_account()
            if notional > acct["cash"]:
                return False, f"Order notional ${notional:.2f} exceeds cash ${acct['cash']:.2f} (no leverage)"

        # 5. Max open positions
        if side == "buy" and self.get_open_position_count() >= r["max_open_positions"]:
            return False, f"Max open positions ({r['max_open_positions']}) already reached"

        # 6. Notional not absurd (sanity: < 50% equity per trade)
        if notional > equity * 0.50:
            return False, f"Notional ${notional:.2f} exceeds 50% of equity (sanity cap)"

        return True, "ok"

    def check_daily_loss_limit(self) -> bool:
        """Call at session start and after each fill. Returns True if trading is allowed."""
        acct = self.get_account()
        equity = acct["equity"]
        # Alpaca gives last_equity as prior session close
        last_equity = float(self._client.get_account().last_equity)
        pnl_pct = (equity - last_equity) / last_equity if last_equity else 0
        if pnl_pct <= self._risk["daily_loss_limit"]:
            log.warning("Daily loss limit hit: %.2f%% — halting new entries", pnl_pct * 100)
            self._session_loss_halted = True
        else:
            self._session_loss_halted = False
        return not self._session_loss_halted

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Submit an order through the pre-trade checklist.
        Returns the order dict on success, None if checklist fails.
        Idempotent: pass the same client_order_id to avoid duplicates.
        """
        symbol = symbol.upper()
        side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif_enum = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        # Estimate notional for checklist
        equity = self.get_equity()
        latest_price = self.get_latest_price(symbol)
        if latest_price is None:
            log.error("Cannot get price for %s — aborting order", symbol)
            return None
        notional = qty * latest_price

        passed, reason = self._run_checklist(symbol, side.lower(), qty, equity, notional)
        if not passed:
            log.warning("Pre-trade checklist FAILED for %s: %s", symbol, reason)
            return None

        coid = client_order_id or f"agent-{symbol}-{date.today().isoformat()}-{uuid.uuid4().hex[:8]}"

        try:
            if order_type == "market":
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side_enum,
                    time_in_force=tif_enum,
                    client_order_id=coid,
                )
            else:
                if limit_price is None:
                    log.error("limit_price required for limit orders")
                    return None
                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side_enum,
                    limit_price=limit_price,
                    time_in_force=tif_enum,
                    client_order_id=coid,
                )

            order = self._client.submit_order(req)
            log.info("Order submitted: %s %s %s qty=%s id=%s", side, symbol, order_type, qty, order.id)
            return {
                "id": str(order.id),
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "qty": float(order.qty),
                "side": order.side.value,
                "type": order.order_type.value,
                "status": order.status.value,
                "submitted_at": str(order.submitted_at),
            }

        except Exception as e:
            # Check for duplicate client_order_id (idempotency)
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                log.info("Duplicate order detected for client_order_id=%s — skipping", coid)
                return None
            log.error("Order submission failed for %s: %s", symbol, e)
            return None

    # ------------------------------------------------------------------
    # Market data helpers
    # ------------------------------------------------------------------

    def get_latest_price(self, symbol: str) -> Optional[float]:
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = self._data_client.get_stock_latest_quote(req)
            q = quote[symbol]
            mid = (float(q.ask_price) + float(q.bid_price)) / 2
            return mid if mid > 0 else float(q.ask_price)
        except Exception as e:
            log.error("Failed to get price for %s: %s", symbol, e)
            return None

    def size_position_by_atr(
        self,
        symbol: str,
        atr: float,
        equity: Optional[float] = None,
    ) -> int:
        """
        Returns share qty sized so that 1 ATR move = max_risk_per_trade of equity.
        qty = (equity * max_risk) / (ATR * atr_stop_multiplier)
        """
        if equity is None:
            equity = self.get_equity()
        risk_dollars = equity * self._risk["max_risk_per_trade"]
        stop_dollars = atr * self.cfg["sizing"]["atr_stop_multiplier"]
        if stop_dollars <= 0:
            return 0
        qty = int(risk_dollars / stop_dollars)
        return max(qty, 0)


# ------------------------------------------------------------------
# Hello-world smoke test (run directly: python broker.py)
# ------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    broker = Broker()

    acct = broker.get_account()
    print(f"\nAccount equity : ${acct['equity']:,.2f}")
    print(f"Cash           : ${acct['cash']:,.2f}")
    print(f"Buying power   : ${acct['buying_power']:,.2f}")

    positions = broker.get_positions()
    print(f"Open positions : {len(positions)}")

    # Hello-world order: buy 1 share of a cheap, liquid ETF
    print("\nPlacing hello-world order: 1 share of SIRI (paper)...")
    order = broker.submit_order(symbol="SIRI", qty=1, side="buy")
    if order:
        print(f"Order placed: {order}")
        import time; time.sleep(2)
        broker.cancel_order(order["id"])
        print("Order cancelled (cleanup)")
    else:
        print("Order blocked by checklist (market may be closed — that is OK)")
