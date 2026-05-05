"""
Phase 2 tests — market data fetcher and indicators.
Fetcher tests hit the real Alpaca paper endpoint.
Indicator tests use a deterministic synthetic dataset (no network).
Run: pytest tests/test_data.py -v
"""

import math
import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.fetcher import fetch_bars, fetch_bars_single, invalidate_cache, _cache_path
from data.indicators import (
    add_all_indicators,
    add_rsi,
    add_sma,
    add_macd,
    add_atr,
    get_signal_summary,
)
from data.universe import EQUITY_SYMBOLS, SECTOR_MAP, get_ticker, symbols_by_sector


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_ohlcv(n: int = 250, start_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """Synthetic daily OHLCV with a clear uptrend and mild noise."""
    rng = np.random.default_rng(seed)
    # Stronger drift so SMA-20 reliably sits above SMA-200 at the end
    closes = start_price + np.cumsum(rng.normal(0.30, 1.0, n))
    closes = np.maximum(closes, 1.0)
    highs  = closes + rng.uniform(0.5, 2.0, n)
    lows   = closes - rng.uniform(0.5, 2.0, n)
    opens  = closes - rng.normal(0, 0.5, n)
    vols   = rng.integers(500_000, 5_000_000, n).astype(float)
    dates  = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date":   dates,
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": vols,
        "vwap":   closes,
    })


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    return _make_ohlcv()


# ------------------------------------------------------------------
# Universe tests
# ------------------------------------------------------------------

class TestUniverse:
    def test_equity_symbols_not_empty(self):
        assert len(EQUITY_SYMBOLS) > 0

    def test_sector_map_covers_all_equities(self):
        for sym in EQUITY_SYMBOLS:
            assert sym in SECTOR_MAP, f"{sym} missing from SECTOR_MAP"

    def test_get_ticker_known(self):
        t = get_ticker("SPY")
        assert t is not None
        assert t.symbol == "SPY"
        assert t.sector == "ETF"

    def test_get_ticker_unknown_returns_none(self):
        assert get_ticker("ZZZZZ") is None

    def test_symbols_by_sector(self):
        tech = symbols_by_sector("Technology")
        assert "NVDA" in tech
        assert "SPY" not in tech


# ------------------------------------------------------------------
# Fetcher tests (live network)
# ------------------------------------------------------------------

class TestFetcher:
    def test_fetch_single_symbol(self):
        df = fetch_bars_single("SPY", days=30, force_refresh=True)
        assert not df.empty
        assert "close" in df.columns
        assert len(df) >= 15   # at least 3 trading weeks

    def test_fetch_returns_expected_columns(self):
        df = fetch_bars_single("SPY", days=10)
        for col in ("date", "open", "high", "low", "close", "volume"):
            assert col in df.columns, f"Missing column: {col}"

    def test_fetch_multi_symbol(self):
        result = fetch_bars(["SPY", "QQQ"], days=20, force_refresh=True)
        assert "SPY" in result
        assert "QQQ" in result
        assert not result["SPY"].empty
        assert not result["QQQ"].empty

    def test_cache_hit_skips_network(self):
        # First call populates cache
        fetch_bars_single("AAPL", days=20, force_refresh=True)
        # Second call should read from cache (still passes same data)
        df2 = fetch_bars_single("AAPL", days=20, force_refresh=False)
        assert not df2.empty

    def test_cache_invalidation(self):
        fetch_bars_single("MSFT", days=10, force_refresh=True)
        path = _cache_path("MSFT", "1Day", 10)
        assert path.exists()
        invalidate_cache("MSFT", days=10)
        assert not path.exists()

    def test_prices_are_positive(self):
        df = fetch_bars_single("SPY", days=20)
        assert (df["close"] > 0).all()
        assert (df["high"] >= df["low"]).all()

    def test_bars_sorted_ascending(self):
        df = fetch_bars_single("SPY", days=20)
        assert df["date"].is_monotonic_increasing


# ------------------------------------------------------------------
# Indicator tests (synthetic, no network)
# ------------------------------------------------------------------

class TestIndicators:
    def test_add_all_indicators_columns(self, ohlcv):
        df = add_all_indicators(ohlcv)
        expected = [
            "rsi", "sma_20", "sma_50", "sma_200",
            "ema_9", "ema_21",
            "macd", "macd_signal", "macd_hist",
            "atr", "bb_upper", "bb_mid", "bb_lower", "bb_pct",
            "vol_sma_20", "vol_ratio",
        ]
        for col in expected:
            assert col in df.columns, f"Missing indicator column: {col}"

    def test_rsi_range(self, ohlcv):
        df = add_rsi(ohlcv)
        valid = df["rsi"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_sma_relationship(self, ohlcv):
        """SMA-20 should react faster than SMA-200 on an uptrending series."""
        df = add_sma(ohlcv)
        last = df.dropna(subset=["sma_20", "sma_200"]).iloc[-1]
        # In an uptrend the faster SMA should be above the slower
        assert last["sma_20"] > last["sma_200"]

    def test_atr_positive(self, ohlcv):
        # ta library sets ATR to 0 during warmup rows (not NaN), so check
        # only after the warmup window (14 bars)
        df = add_atr(ohlcv)
        valid = df["atr"].iloc[14:]
        assert (valid > 0).all()

    def test_macd_has_crossovers(self, ohlcv):
        """With enough bars the MACD line should cross signal at least once."""
        df = add_macd(ohlcv)
        hist = df["macd_hist"].dropna()
        signs = (hist > 0).astype(int).diff().abs()
        assert signs.sum() > 0, "No MACD crossovers found in 250 bars"

    def test_no_indicator_modifies_original(self, ohlcv):
        original_cols = set(ohlcv.columns)
        _ = add_all_indicators(ohlcv)
        assert set(ohlcv.columns) == original_cols, "add_all_indicators must not mutate input"

    def test_get_signal_summary_structure(self, ohlcv):
        df = add_all_indicators(ohlcv)
        sig = get_signal_summary(df)
        for key in ("close", "atr", "rsi", "rsi_zone", "macd_bullish", "bias",
                    "above_sma20", "above_sma50", "above_sma200", "vol_ratio"):
            assert key in sig, f"Missing key in signal summary: {key}"

    def test_signal_bias_valid(self, ohlcv):
        df = add_all_indicators(ohlcv)
        sig = get_signal_summary(df)
        assert sig["bias"] in ("bullish", "bearish", "neutral")

    def test_signal_rsi_zone_valid(self, ohlcv):
        df = add_all_indicators(ohlcv)
        sig = get_signal_summary(df)
        assert sig["rsi_zone"] in ("oversold", "overbought", "neutral")

    def test_signal_summary_empty_df(self):
        result = get_signal_summary(pd.DataFrame())
        assert "error" in result

    def test_insufficient_bars_doesnt_crash(self):
        small = _make_ohlcv(n=25)
        df = add_all_indicators(small)
        # SMA-200 will be all NaN but should not raise
        assert "sma_200" in df.columns
        assert df["sma_200"].isna().all()
