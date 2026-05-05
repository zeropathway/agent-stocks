"""
Phase 3 tests — news, earnings, and thesis generation.
- News/earnings: live network calls (fast, no LLM cost)
- Thesis: one live Claude call + schema validation; cached fixture for fast re-runs
Run: pytest tests/test_research.py -v
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research.news import fetch_headlines, headlines_to_text, Headline
from research.earnings import fetch_earnings_info, earnings_to_text, EarningsInfo
from research.thesis import generate_thesis, Thesis, _rule_based_thesis, _cache_path


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

MOCK_SIGNAL = {
    "close": 276.83,
    "atr": 6.54,
    "rsi": 61.8,
    "rsi_zone": "neutral",
    "macd": 1.2,
    "macd_signal": 0.9,
    "macd_hist": 0.3,
    "macd_bullish": True,
    "sma_20": 268.0,
    "sma_50": 255.0,
    "sma_200": 245.0,
    "above_sma20": True,
    "above_sma50": True,
    "above_sma200": True,
    "bb_pct": 0.72,
    "vol_ratio": 1.3,
    "elevated_volume": False,
    "bullish_signals": 4,
    "bearish_signals": 1,
    "bias": "bullish",
}

MOCK_EARNINGS = EarningsInfo(
    symbol="AAPL",
    next_earnings_date=date(2026, 7, 31),
    days_until_earnings=87,
    eps_estimate=7.20,
    eps_actual_last=6.43,
    is_earnings_week=False,
)

MOCK_HEADLINES = [
    Headline(
        source="yahoo",
        title="Apple reports record iPhone sales in emerging markets",
        summary="Strong demand from India and Southeast Asia lifted Q2 results.",
        url="https://finance.yahoo.com/news/apple-iphone-sales",
        published=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
    ),
    Headline(
        source="finviz",
        title="Apple Vision Pro gains enterprise traction",
        summary="Fortune 500 companies begin deploying Vision Pro for remote work.",
        url="https://finviz.com/news/aapl",
        published=datetime(2026, 5, 3, 9, 30, tzinfo=timezone.utc),
    ),
]


# ------------------------------------------------------------------
# News tests
# ------------------------------------------------------------------

class TestNews:
    def test_fetch_headlines_returns_list(self):
        headlines = fetch_headlines("AAPL", max_items=5)
        assert isinstance(headlines, list)

    def test_fetch_headlines_has_title(self):
        headlines = fetch_headlines("AAPL", max_items=5)
        if headlines:   # may be empty if rate-limited
            assert all(isinstance(h.title, str) for h in headlines)
            assert all(len(h.title) > 0 for h in headlines)

    def test_fetch_headlines_no_duplicates(self):
        headlines = fetch_headlines("AAPL", max_items=10)
        titles = [h.title.lower()[:80] for h in headlines]
        assert len(titles) == len(set(titles)), "Duplicate headlines returned"

    def test_fetch_yahoo_only(self):
        headlines = fetch_headlines("SPY", max_items=5, sources=["yahoo"])
        assert all(h.source == "yahoo" for h in headlines)

    def test_headlines_to_text_empty(self):
        text = headlines_to_text([], "AAPL")
        assert "No recent news" in text

    def test_headlines_to_text_format(self):
        text = headlines_to_text(MOCK_HEADLINES, "AAPL")
        assert "AAPL" in text
        assert "Apple reports record" in text
        assert "2026-05-04" in text

    def test_invalid_symbol_returns_empty_or_list(self):
        headlines = fetch_headlines("ZZZZZZFAKE", max_items=3)
        assert isinstance(headlines, list)   # should not raise


# ------------------------------------------------------------------
# Earnings tests
# ------------------------------------------------------------------

class TestEarnings:
    def test_fetch_earnings_aapl(self):
        info = fetch_earnings_info("AAPL")
        assert info.symbol == "AAPL"

    def test_fetch_earnings_returns_dataclass(self):
        info = fetch_earnings_info("MSFT")
        assert isinstance(info, EarningsInfo)

    def test_fetch_earnings_invalid_symbol(self):
        info = fetch_earnings_info("ZZZZFAKE")
        assert info.symbol == "ZZZZFAKE"
        # Should not raise — all fields can be None

    def test_days_until_earnings_positive(self):
        info = fetch_earnings_info("AAPL")
        if info.days_until_earnings is not None:
            assert info.days_until_earnings >= -5   # allow slight past leeway

    def test_earnings_to_text_no_date(self):
        info = EarningsInfo(symbol="TEST")
        text = earnings_to_text(info)
        assert "unknown" in text.lower()

    def test_earnings_to_text_with_date(self):
        text = earnings_to_text(MOCK_EARNINGS)
        assert "2026-07-31" in text
        assert "87 days" in text
        assert "$7.20" in text

    def test_earnings_week_flag(self):
        info = EarningsInfo(
            symbol="TEST",
            next_earnings_date=date.today(),
            days_until_earnings=0,
            is_earnings_week=True,
        )
        assert info.is_earnings_week is True
        text = earnings_to_text(info)
        assert "EARNINGS THIS WEEK" in text


# ------------------------------------------------------------------
# Thesis tests
# ------------------------------------------------------------------

class TestThesis:
    def test_rule_based_thesis_bullish(self):
        thesis = _rule_based_thesis("AAPL", MOCK_SIGNAL, MOCK_EARNINGS)
        assert thesis.symbol == "AAPL"
        assert thesis.sentiment == "bullish"
        assert 1 <= thesis.conviction <= 5
        assert thesis.technical_alignment is True
        assert isinstance(thesis.risk_factors, list)
        assert len(thesis.risk_factors) >= 1

    def test_rule_based_thesis_bearish(self):
        bearish_signal = {**MOCK_SIGNAL, "bias": "bearish",
                          "bullish_signals": 1, "bearish_signals": 4}
        thesis = _rule_based_thesis("AAPL", bearish_signal, MOCK_EARNINGS)
        assert thesis.sentiment == "bearish"

    def test_rule_based_thesis_earnings_risk(self):
        earnings_soon = EarningsInfo(
            symbol="AAPL",
            next_earnings_date=date.today(),
            days_until_earnings=0,
            is_earnings_week=True,
        )
        thesis = _rule_based_thesis("AAPL", MOCK_SIGNAL, earnings_soon)
        assert thesis.earnings_risk is True

    def test_thesis_to_dict_and_back(self):
        thesis = _rule_based_thesis("AAPL", MOCK_SIGNAL, MOCK_EARNINGS)
        d = thesis.to_dict()
        restored = Thesis.from_dict(d)
        assert restored.symbol == thesis.symbol
        assert restored.sentiment == thesis.sentiment
        assert restored.conviction == thesis.conviction

    def test_generate_thesis_live_claude(self):
        """One real Claude call — validates schema and content quality."""
        # Invalidate cache to force fresh generation
        cache = _cache_path("AAPL")
        if cache.exists():
            cache.unlink()

        thesis = generate_thesis("AAPL", MOCK_SIGNAL, MOCK_HEADLINES, MOCK_EARNINGS)

        assert thesis.symbol == "AAPL"
        assert thesis.sentiment in ("bullish", "bearish", "neutral")
        assert 1 <= thesis.conviction <= 5
        assert len(thesis.catalyst) > 20
        assert len(thesis.risk_factors) >= 1
        assert isinstance(thesis.technical_alignment, bool)
        assert isinstance(thesis.earnings_risk, bool)
        assert len(thesis.summary) > 50

    def test_generate_thesis_uses_cache(self):
        """Second call within TTL should return cached thesis without calling Claude."""
        # Ensure cache exists from prior test
        thesis1 = generate_thesis("AAPL", MOCK_SIGNAL, MOCK_HEADLINES, MOCK_EARNINGS)
        thesis2 = generate_thesis("AAPL", MOCK_SIGNAL, MOCK_HEADLINES, MOCK_EARNINGS)
        assert thesis1.generated_at == thesis2.generated_at

    def test_generate_thesis_no_api_key_falls_back(self):
        """With no API key the function should return a rule-based thesis, not raise."""
        cache = _cache_path("TSLA")
        if cache.exists():
            cache.unlink()

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            tsla_signal = {**MOCK_SIGNAL, "close": 392.0}
            earnings = EarningsInfo(symbol="TSLA")
            thesis = generate_thesis("TSLA", tsla_signal, [], earnings, force_refresh=True)

        assert thesis.symbol == "TSLA"
        assert thesis.sentiment in ("bullish", "bearish", "neutral")
