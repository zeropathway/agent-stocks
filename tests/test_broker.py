"""
Phase 1 broker tests — hit the paper endpoint directly (no mocks).
Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in environment or .env.
Run: pytest tests/test_broker.py -v
"""

import pytest
from broker import Broker


@pytest.fixture(scope="module")
def broker():
    return Broker()


class TestAuth:
    def test_account_returns_equity(self, broker):
        acct = broker.get_account()
        assert acct["equity"] > 0, "Expected positive equity on paper account"
        assert acct["cash"] >= 0

    def test_equity_helper(self, broker):
        equity = broker.get_equity()
        assert isinstance(equity, float)
        assert equity > 0


class TestPositions:
    def test_get_positions_returns_list(self, broker):
        positions = broker.get_positions()
        assert isinstance(positions, list)

    def test_get_open_position_count(self, broker):
        count = broker.get_open_position_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_get_nonexistent_position_returns_none(self, broker):
        result = broker.get_position("ZZZZZ_FAKE_TICKER")
        assert result is None


class TestPriceData:
    def test_get_latest_price_spy(self, broker):
        price = broker.get_latest_price("SPY")
        assert price is not None, "Should get a price for SPY"
        assert price > 1.0

    def test_get_latest_price_invalid_returns_none(self, broker):
        price = broker.get_latest_price("ZZZZZ_FAKE")
        assert price is None


class TestPositionSizing:
    def test_size_position_by_atr(self, broker):
        # With $100k equity, 1% risk = $1000, ATR=2 stop_mult=2 → stop=$4 → qty=250
        qty = broker.size_position_by_atr("SPY", atr=2.0, equity=100_000)
        assert qty == 250

    def test_size_position_zero_atr(self, broker):
        qty = broker.size_position_by_atr("SPY", atr=0.0, equity=100_000)
        assert qty == 0


class TestChecklist:
    def test_checklist_blocks_short(self, broker):
        # No existing position, so selling > 0 qty should fail checklist
        passed, reason = broker._run_checklist("SPY", "sell", 1, 100_000, 500)
        assert not passed
        assert "short" in reason.lower()

    def test_checklist_blocks_leverage(self, broker):
        # Notional exceeding cash should fail
        equity = broker.get_equity()
        cash = broker.get_account()["cash"]
        passed, reason = broker._run_checklist("SPY", "buy", 1, equity, cash + 1_000_000)
        assert not passed
        assert "cash" in reason.lower() or "leverage" in reason.lower()


class TestOrderRoundTrip:
    """
    Submits a market order for 1 share of a cheap stock and immediately cancels it.
    Markets must be open for the order to go live; if closed, Alpaca may accept or
    queue it — we cancel either way.
    """

    def test_submit_and_cancel_order(self, broker):
        order = broker.submit_order(
            symbol="SIRI",
            qty=1,
            side="buy",
            order_type="market",
            client_order_id="pytest-roundtrip-001",
        )
        if order is None:
            pytest.skip("Order blocked (market closed or checklist) — not a test failure")

        assert order["symbol"] == "SIRI"
        assert order["qty"] == 1.0
        assert order["side"] == "buy"

        # Clean up — cancel the order
        cancelled = broker.cancel_order(order["id"])
        assert cancelled, "Should be able to cancel a just-submitted order"

    def test_duplicate_order_id_is_idempotent(self, broker):
        """Same client_order_id should not raise, returns None on duplicate."""
        order1 = broker.submit_order(
            symbol="SIRI",
            qty=1,
            side="buy",
            client_order_id="pytest-idem-001",
        )
        # Second call with same ID — should not raise
        order2 = broker.submit_order(
            symbol="SIRI",
            qty=1,
            side="buy",
            client_order_id="pytest-idem-001",
        )
        # Clean up
        if order1:
            broker.cancel_order(order1["id"])
        # Both None or order2 is None (duplicate blocked)
        assert order2 is None or order2["client_order_id"] != order1["client_order_id"] if order1 else True
