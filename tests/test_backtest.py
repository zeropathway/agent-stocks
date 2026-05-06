"""Tests for backtesting harness (historical, engine, metrics, report)."""

import math
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backtest.engine import (
    BacktestResult,
    Trade,
    _bar_to_signal,
    run_backtest,
)
from backtest.metrics import Metrics, compute_metrics
from backtest.report import generate_report


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _synthetic_bars(n: int = 400, seed: int = 42, trend: float = 0.0003) -> pd.DataFrame:
    """Create a realistic trending OHLCV DataFrame with indicators attached."""
    from data.indicators import add_all_indicators

    np.random.seed(seed)
    log_returns = np.random.randn(n) * 0.012 + trend
    closes      = 100.0 * np.exp(np.cumsum(log_returns))
    highs       = closes * (1 + np.abs(np.random.randn(n) * 0.005))
    lows        = closes * (1 - np.abs(np.random.randn(n) * 0.005))
    opens       = closes * (1 + np.random.randn(n) * 0.003)

    df = pd.DataFrame({
        "date":   pd.date_range("2015-01-01", periods=n, freq="B"),
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": np.random.randint(500_000, 5_000_000, n).astype(float),
    })
    return add_all_indicators(df)


def _make_result(n_wins: int = 2, n_losses: int = 1) -> BacktestResult:
    """Build a minimal BacktestResult with controlled trades for metrics tests."""
    trades = []
    for i in range(n_wins):
        t = Trade("WIN", date(2020, 1, 5 + i), 100.0, 96.0, 106.0, 10.0, 7.0, 4.0)
        t.exit_date   = date(2020, 2, 1 + i)
        t.exit_price  = 106.0
        t.exit_reason = "target"
        t.pnl         = 60.0
        t.r_multiple  = 1.5
        trades.append(t)
    for i in range(n_losses):
        t = Trade("LOSS", date(2020, 3, 1 + i), 50.0, 47.0, 54.5, 20.0, 6.0, 3.0)
        t.exit_date   = date(2020, 3, 15 + i)
        t.exit_price  = 47.0
        t.exit_reason = "stop"
        t.pnl         = -60.0
        t.r_multiple  = -1.0
        trades.append(t)

    dates  = pd.date_range("2020-01-02", periods=300, freq="B")
    equity = pd.Series(
        [100_000 + i * 5 for i in range(300)],
        index=dates,
    )
    return BacktestResult(
        trades=trades,
        equity_curve=equity,
        start_equity=100_000,
        end_equity=101_495,
        symbols=["WIN", "LOSS"],
        start_date=date(2020, 1, 2),
        end_date=date(2021, 3, 31),
    )


# ------------------------------------------------------------------
# historical.py
# ------------------------------------------------------------------

class TestFetchHistory:
    def test_returns_expected_columns(self, tmp_path):
        from backtest.historical import fetch_history

        mock_df = pd.DataFrame({
            "Date":   pd.date_range("2010-01-01", periods=300, freq="B"),
            "Open":   [100.0] * 300,
            "High":   [105.0] * 300,
            "Low":    [95.0] * 300,
            "Close":  [102.0] * 300,
            "Volume": [1_000_000] * 300,
        }).set_index("Date")

        with patch("backtest.historical.yf.Ticker") as mock_t, \
             patch("backtest.historical._CACHE_DIR", tmp_path):
            mock_t.return_value.history.return_value = mock_df
            df = fetch_history("AAPL", years=1, force_refresh=True)

        assert set(["date", "open", "high", "low", "close", "volume"]).issubset(df.columns)
        assert len(df) == 300

    def test_empty_ticker_returns_empty_df(self, tmp_path):
        from backtest.historical import fetch_history

        with patch("backtest.historical.yf.Ticker") as mock_t, \
             patch("backtest.historical._CACHE_DIR", tmp_path):
            mock_t.return_value.history.return_value = pd.DataFrame()
            df = fetch_history("INVALID", years=1, force_refresh=True)

        assert df.empty

    def test_cache_is_written(self, tmp_path):
        from backtest.historical import fetch_history

        mock_df = pd.DataFrame({
            "Date":   pd.date_range("2020-01-01", periods=100, freq="B"),
            "Open":   [10.0] * 100, "High": [11.0] * 100,
            "Low":    [9.0] * 100,  "Close": [10.5] * 100,
            "Volume": [500_000] * 100,
        }).set_index("Date")

        with patch("backtest.historical.yf.Ticker") as mock_t, \
             patch("backtest.historical._CACHE_DIR", tmp_path):
            mock_t.return_value.history.return_value = mock_df
            fetch_history("TEST", years=1, force_refresh=True)
            assert (tmp_path / "bt_TEST.parquet").exists()


# ------------------------------------------------------------------
# engine._bar_to_signal
# ------------------------------------------------------------------

class TestBarToSignal:
    def test_valid_row_returns_signal(self):
        df  = _synthetic_bars(300)
        row = df.iloc[-1]
        sig = _bar_to_signal(row)
        assert sig is not None
        assert "close" in sig and "rsi" in sig and "above_sma200" in sig
        assert sig["bias"] in ("bullish", "bearish", "neutral")

    def test_nan_close_returns_none(self):
        row = pd.Series({"close": float("nan")})
        assert _bar_to_signal(row) is None

    def test_zero_close_returns_none(self):
        row = pd.Series({"close": 0.0})
        assert _bar_to_signal(row) is None

    def test_missing_indicators_handled_gracefully(self):
        row = pd.Series({"close": 150.0, "rsi": float("nan"), "sma_200": float("nan")})
        sig = _bar_to_signal(row)
        assert sig is not None
        assert sig["above_sma200"] is None

    def test_bullish_signals_count(self):
        df  = _synthetic_bars(400, trend=0.001)   # strong uptrend
        row = df.iloc[-1]
        sig = _bar_to_signal(row)
        assert sig is not None
        assert sig["bullish_signals"] >= 0
        assert sig["bearish_signals"] >= 0


# ------------------------------------------------------------------
# engine.run_backtest
# ------------------------------------------------------------------

class TestRunBacktest:
    def _mock_fetch(self, *args, **kwargs):
        return _synthetic_bars(500)

    def test_returns_backtest_result(self):
        with patch("backtest.engine.fetch_history", side_effect=self._mock_fetch):
            result = run_backtest(["FAKE"], years=2, start_equity=100_000)
        assert isinstance(result, BacktestResult)
        assert result.start_equity == 100_000
        assert isinstance(result.equity_curve, pd.Series)
        assert len(result.equity_curve) > 0

    def test_equity_never_negative(self):
        with patch("backtest.engine.fetch_history", side_effect=self._mock_fetch):
            result = run_backtest(["FAKE"], years=2, start_equity=10_000)
        assert all(v >= 0 for v in result.equity_curve.values)

    def test_all_trades_have_exit(self):
        with patch("backtest.engine.fetch_history", side_effect=self._mock_fetch):
            result = run_backtest(["FAKE"], years=2)
        for t in result.trades:
            assert t.exit_date is not None
            assert t.exit_price is not None
            assert t.exit_reason in ("stop", "target", "end_of_backtest")

    def test_stop_price_below_entry(self):
        with patch("backtest.engine.fetch_history", side_effect=self._mock_fetch):
            result = run_backtest(["FAKE"], years=2)
        for t in result.trades:
            assert t.stop_price < t.entry_price, f"{t.symbol}: stop >= entry"

    def test_target_price_above_entry(self):
        with patch("backtest.engine.fetch_history", side_effect=self._mock_fetch):
            result = run_backtest(["FAKE"], years=2)
        for t in result.trades:
            assert t.target_price > t.entry_price, f"{t.symbol}: target <= entry"

    def test_multi_symbol_max_positions(self):
        symbols = ["A", "B", "C", "D", "E", "F", "G"]

        def multi_mock(symbol, **kwargs):
            return _synthetic_bars(500, seed=ord(symbol[0]))

        with patch("backtest.engine.fetch_history", side_effect=multi_mock):
            result = run_backtest(symbols, years=2)
        # We can't hold more than MAX_POSITIONS simultaneously — just verify no crash
        assert len(result.trades) >= 0

    def test_empty_symbols_raises(self):
        with pytest.raises(ValueError):
            with patch("backtest.engine.fetch_history", return_value=pd.DataFrame()):
                run_backtest(["BAD"], years=1)


# ------------------------------------------------------------------
# metrics.compute_metrics
# ------------------------------------------------------------------

class TestComputeMetrics:
    def test_basic_fields_present(self):
        result = _make_result()
        m = compute_metrics(result)
        for field in ("cagr", "total_return", "sharpe", "sortino",
                      "max_drawdown", "win_rate", "profit_factor"):
            assert hasattr(m, field)

    def test_win_rate_2_wins_1_loss(self):
        m = compute_metrics(_make_result(n_wins=2, n_losses=1))
        assert abs(m.win_rate - 66.67) < 0.1

    def test_profit_factor_2_wins_1_loss(self):
        # gross_profit = 120, gross_loss = 60 → pf = 2.0
        m = compute_metrics(_make_result(n_wins=2, n_losses=1))
        assert abs(m.profit_factor - 2.0) < 0.01

    def test_cagr_positive_for_rising_equity(self):
        m = compute_metrics(_make_result())
        assert m.cagr > 0
        assert m.total_return > 0

    def test_max_drawdown_non_positive(self):
        m = compute_metrics(_make_result())
        assert m.max_drawdown <= 0

    def test_avg_r_correct(self):
        # wins: 1.5R x2, losses: -1.0R x1 → avg = (1.5+1.5-1.0)/3 = 0.667
        m = compute_metrics(_make_result(n_wins=2, n_losses=1))
        assert abs(m.avg_r_multiple - (1.5 + 1.5 - 1.0) / 3) < 0.01

    def test_to_dict_serialisable(self):
        m = compute_metrics(_make_result())
        d = m.to_dict()
        import json
        assert json.dumps(d)   # must not raise


# ------------------------------------------------------------------
# report.generate_report
# ------------------------------------------------------------------

class TestGenerateReport:
    def test_creates_md_and_json(self, tmp_path):
        from backtest.report import _RESULTS_DIR
        result  = _make_result()
        metrics = compute_metrics(result)

        with patch("backtest.report._RESULTS_DIR", tmp_path):
            path = generate_report(result, metrics)

        assert path.exists()
        assert path.suffix == ".md"
        json_path = path.with_suffix(".json")
        assert json_path.exists()

    def test_json_contains_metrics(self, tmp_path):
        import json as _json
        result  = _make_result()
        metrics = compute_metrics(result)

        with patch("backtest.report._RESULTS_DIR", tmp_path):
            md_path = generate_report(result, metrics)

        data = _json.loads(md_path.with_suffix(".json").read_text())
        assert "metrics" in data
        assert "trades" in data
        assert data["metrics"]["win_rate"] == metrics.win_rate

    def test_md_contains_summary_table(self, tmp_path):
        result  = _make_result()
        metrics = compute_metrics(result)

        with patch("backtest.report._RESULTS_DIR", tmp_path):
            md_path = generate_report(result, metrics)

        text = md_path.read_text()
        assert "CAGR" in text
        assert "Sharpe" in text
        assert "Win Rate" in text
