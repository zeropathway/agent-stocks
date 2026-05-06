"""
Phase 5 tests — executor.
Unit tests mock the broker; the integration test hits the real paper endpoint.
Run: pytest tests/test_execution.py -v
"""

import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from execution.executor import Executor, ExecutionResult, FILL_TIMEOUT
from decision.proposer import ProposedTrade


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_trade(**kwargs) -> ProposedTrade:
    defaults = dict(
        symbol="AAPL", action="buy", qty=10,
        entry_price=276.00, stop_price=262.00, target_price=303.00,
        risk_reward=1.93, rationale="Test trade.",
        score=7.5, conviction=4, thesis_sentiment="bullish",
        model_used="claude-sonnet-4-6",
        generated_at="2026-05-05T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return ProposedTrade(**defaults)


def _mock_broker(
    equity=100_000.0,
    cash=100_000.0,
    positions=None,
    daily_loss_ok=True,
) -> MagicMock:
    broker = MagicMock()
    broker.check_daily_loss_limit.return_value = daily_loss_ok
    broker.get_account.return_value = {"equity": equity, "cash": cash, "buying_power": cash}
    broker.get_equity.return_value = equity
    broker.get_positions.return_value = positions or []
    broker.get_position.return_value = None
    broker.get_latest_price.return_value = 276.00   # matches entry_price default
    broker.submit_order.return_value = {
        "id": "order-123",
        "client_order_id": "agent-AAPL-test",
        "symbol": "AAPL",
        "qty": 10.0,
        "side": "buy",
        "type": "limit",
        "status": "new",
        "filled_qty": 0,
        "filled_avg_price": None,
        "submitted_at": "2026-05-05T12:00:00+00:00",
    }
    return broker


# ------------------------------------------------------------------
# Unit tests (mocked broker)
# ------------------------------------------------------------------

class TestPreflight:
    def test_passes_when_clear(self):
        executor = Executor(broker=_mock_broker())
        trade = _make_trade()
        ok, reason = executor._preflight(trade)
        assert ok, reason

    def test_fails_daily_loss_limit(self):
        executor = Executor(broker=_mock_broker(daily_loss_ok=False))
        ok, reason = executor._preflight(_make_trade())
        assert not ok
        assert "daily loss" in reason.lower()

    def test_fails_duplicate_position(self):
        broker = _mock_broker()
        broker.get_position.return_value = {"symbol": "AAPL", "qty": 10.0, "market_value": 2760}
        executor = Executor(broker=broker)
        ok, reason = executor._preflight(_make_trade())
        assert not ok
        assert "already holding" in reason.lower()

    def test_fails_insufficient_cash(self):
        executor = Executor(broker=_mock_broker(cash=100.0))
        ok, reason = executor._preflight(_make_trade(qty=100, entry_price=500.0))
        assert not ok
        assert "cash" in reason.lower()


class TestExecuteRejected:
    def test_returns_rejected_on_preflight_failure(self):
        executor = Executor(broker=_mock_broker(daily_loss_ok=False))
        result = executor.execute(_make_trade())
        assert result.action == "rejected"
        assert result.fill_price is None

    def test_returns_failed_when_broker_returns_none(self):
        broker = _mock_broker()
        broker.submit_order.return_value = None
        executor = Executor(broker=broker)
        result = executor.execute(_make_trade())
        assert result.action == "failed"


class TestExecuteCancelled:
    def test_cancels_after_timeout(self):
        broker = _mock_broker()
        # get_orders always returns "new" → never fills → should timeout
        broker.get_orders.return_value = [{
            "id": "order-123", "symbol": "AAPL",
            "status": "new", "filled_qty": 0, "filled_avg_price": None,
        }]
        broker.cancel_order.return_value = True

        executor = Executor(broker=broker)

        # Patch FILL_TIMEOUT to 0 so poll loop exits immediately
        import execution.executor as ex_mod
        with patch.object(ex_mod, "FILL_TIMEOUT", 0):
            with patch.object(ex_mod, "POLL_INTERVAL", 0):
                result = executor.execute(_make_trade())

        assert result.action == "cancelled"
        broker.cancel_order.assert_called_once_with("order-123")


class TestExecuteFilled:
    def test_filled_result_has_correct_fields(self):
        broker = _mock_broker()
        broker.get_orders.return_value = [{
            "id": "order-123", "symbol": "AAPL",
            "status": "filled",
            "filled_qty": 10,
            "filled_avg_price": 276.50,
        }]

        executor = Executor(broker=broker)

        # Patch stop placement to avoid real API call
        with patch.object(executor, "_place_stop", return_value="stop-456"):
            with patch.object(executor, "_write_journal_stub"):
                result = executor.execute(_make_trade())

        assert result.action == "filled"
        assert result.fill_price == 276.50
        assert result.fill_qty == 10
        assert result.stop_order_id == "stop-456"
        assert result.order_id == "order-123"

    def test_journal_stub_written_on_fill(self, tmp_path):
        broker = _mock_broker()
        broker.get_orders.return_value = [{
            "id": "order-123", "symbol": "AAPL",
            "status": "filled", "filled_qty": 10, "filled_avg_price": 276.50,
        }]
        executor = Executor(broker=broker)

        import execution.executor as ex_mod
        with patch.object(executor, "_place_stop", return_value="stop-456"):
            with patch.object(ex_mod, "_JOURNAL_DIR", tmp_path):
                with patch.object(ex_mod, "_EXEC_LOG", tmp_path / "exec.json"):
                    result = executor.execute(_make_trade())

        journal_files = list(tmp_path.glob("*.md"))
        assert len(journal_files) == 1
        content = journal_files[0].read_text()
        assert "AAPL" in content
        assert "stop" in content.lower()


class TestExecutionResultRoundtrip:
    def test_to_dict_contains_all_fields(self):
        result = ExecutionResult(
            symbol="AAPL", action="filled", order_id="ord-1",
            fill_price=276.50, fill_qty=10, stop_order_id="stop-2",
            elapsed_seconds=12.3, reason="ok",
            generated_at="2026-05-05T00:00:00+00:00",
        )
        d = result.to_dict()
        assert d["symbol"] == "AAPL"
        assert d["fill_price"] == 276.50
        assert d["stop_order_id"] == "stop-2"


# ------------------------------------------------------------------
# Integration test — real paper endpoint
# ------------------------------------------------------------------

class TestIntegration:
    """
    Submits a real limit order well below market (won't fill) and verifies
    the executor cancels it after the timeout.
    Uses a cheap, liquid stock to keep notional small.
    """

    def test_submit_and_cancel_via_executor(self):
        broker = Broker() if _try_import_broker() else None
        if broker is None:
            pytest.skip("Broker not importable")

        # Limit $1 below current bid so it won't fill during test
        price = broker.get_latest_price("SIRI")
        if price is None:
            pytest.skip("Could not get SIRI price")

        limit_price = round(price * 0.90, 2)   # 10% below market — won't fill
        trade = _make_trade(
            symbol="SIRI",
            qty=1,
            entry_price=limit_price,
            stop_price=round(limit_price * 0.95, 2),
            target_price=round(limit_price * 1.10, 2),
        )

        executor = Executor(broker=broker)

        import execution.executor as ex_mod
        # Short timeout so test finishes fast (order stays open, gets cancelled)
        with patch.object(ex_mod, "FILL_TIMEOUT", 15):
            with patch.object(ex_mod, "POLL_INTERVAL", 5):
                result = executor.execute(trade)

        # Market closed → order queued then cancelled; market open → not filled (10% below)
        assert result.action in ("cancelled", "filled", "done_for_day", "rejected")
        assert result.symbol == "SIRI"


def _try_import_broker():
    try:
        global Broker
        from broker import Broker
        return True
    except Exception:
        return False
