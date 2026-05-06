"""
Phase 4 tests — scorer, rules, and proposer.
Proposer test makes one live Claude call to validate schema end-to-end.
Run: pytest tests/test_decision.py -v
"""

import json
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from decision.scorer import score_ticker, rank_candidates, ScoreDetail
from decision.rules import apply_rules, filter_candidates, _MIN_SCORE_BUY
from decision.proposer import propose_trade, ProposedTrade, _TRADE_FILE
from research.thesis import Thesis
from research.earnings import EarningsInfo


# ------------------------------------------------------------------
# Shared fixtures
# ------------------------------------------------------------------

def _make_thesis(
    symbol="AAPL",
    sentiment="bullish",
    conviction=4,
    technical_alignment=True,
    earnings_risk=False,
) -> Thesis:
    return Thesis(
        symbol=symbol,
        sentiment=sentiment,
        conviction=conviction,
        catalyst="Strong iPhone sales and AI ecosystem momentum.",
        risk_factors=["Fed tightening", "China slowdown"],
        technical_alignment=technical_alignment,
        earnings_risk=earnings_risk,
        summary="AAPL is technically strong with improving fundamentals.",
        generated_at="2026-05-05T00:00:00+00:00",
    )


BULL_SIGNAL = {
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
    "vol_ratio": 1.6,
    "elevated_volume": True,
    "bullish_signals": 5,
    "bearish_signals": 0,
    "bias": "bullish",
}

BEAR_SIGNAL = {
    **BULL_SIGNAL,
    "above_sma20": False,
    "above_sma50": False,
    "above_sma200": False,
    "macd_bullish": False,
    "rsi": 72.0,
    "rsi_zone": "overbought",
    "vol_ratio": 0.6,
    "bias": "bearish",
}


# ------------------------------------------------------------------
# Scorer tests
# ------------------------------------------------------------------

class TestScorer:
    def test_bullish_score_positive(self):
        thesis = _make_thesis(sentiment="bullish", conviction=4)
        detail = score_ticker(BULL_SIGNAL, thesis)
        assert detail.total > 0

    def test_bearish_score_negative(self):
        thesis = _make_thesis(sentiment="bearish", conviction=4, technical_alignment=False)
        detail = score_ticker(BEAR_SIGNAL, thesis)
        assert detail.total < 0

    def test_score_clamped_to_range(self):
        thesis = _make_thesis(sentiment="bullish", conviction=5)
        detail = score_ticker(BULL_SIGNAL, thesis)
        assert -10.0 <= detail.total <= 10.0

    def test_earnings_risk_reduces_score(self):
        no_earnings  = _make_thesis(earnings_risk=False, conviction=4)
        with_earnings = _make_thesis(earnings_risk=True, conviction=4)
        d_no  = score_ticker(BULL_SIGNAL, no_earnings)
        d_yes = score_ticker(BULL_SIGNAL, with_earnings)
        assert d_no.total > d_yes.total

    def test_conviction_amplifies_score(self):
        low  = _make_thesis(sentiment="bullish", conviction=1)
        high = _make_thesis(sentiment="bullish", conviction=5)
        assert score_ticker(BULL_SIGNAL, high).total > score_ticker(BULL_SIGNAL, low).total

    def test_rsi_overbought_penalises(self):
        overbought = {**BULL_SIGNAL, "rsi": 82.0, "rsi_zone": "overbought"}
        neutral    = {**BULL_SIGNAL, "rsi": 55.0, "rsi_zone": "neutral"}
        t = _make_thesis()
        assert score_ticker(overbought, t).total < score_ticker(neutral, t).total

    def test_score_detail_has_components(self):
        detail = score_ticker(BULL_SIGNAL, _make_thesis())
        assert "above_sma200" in detail.components
        assert "macd_bullish" in detail.components
        assert "thesis_sentiment" in detail.components

    def test_rank_candidates_sorted_desc(self):
        signals = {"AAPL": BULL_SIGNAL, "META": BEAR_SIGNAL}
        theses  = {
            "AAPL": _make_thesis("AAPL", "bullish", 4),
            "META": _make_thesis("META", "bearish", 4, technical_alignment=False),
        }
        ranked = rank_candidates(signals, theses)
        assert ranked[0].symbol == "AAPL"
        assert ranked[0].total > ranked[1].total


# ------------------------------------------------------------------
# Rules tests
# ------------------------------------------------------------------

class TestRules:
    def _make_detail(self, score=7.0, symbol="AAPL") -> ScoreDetail:
        return ScoreDetail(
            symbol=symbol, total=score, technical=5.0, thesis_score=2.0, components={}
        )

    def test_high_score_passes_min_score(self):
        detail = self._make_detail(score=7.0)
        passed, results = apply_rules(detail, BULL_SIGNAL, _make_thesis(), [], 100_000)
        score_rule = next(r for r in results if r.rule == "min_score")
        assert score_rule.passed

    def test_low_score_fails_min_score(self):
        detail = self._make_detail(score=2.0)
        passed, _ = apply_rules(detail, BULL_SIGNAL, _make_thesis(), [], 100_000)
        assert not passed

    def test_below_sma200_fails_trend(self):
        no_trend = {**BULL_SIGNAL, "above_sma200": False}
        detail = self._make_detail(score=8.0)
        passed, results = apply_rules(detail, no_trend, _make_thesis(), [], 100_000)
        trend_rule = next((r for r in results if r.rule == "trend_alignment"), None)
        assert trend_rule and not trend_rule.passed

    def test_rsi_above_80_fails(self):
        overbought = {**BULL_SIGNAL, "rsi": 82.0}
        detail = self._make_detail(score=8.0)
        passed, results = apply_rules(detail, overbought, _make_thesis(), [], 100_000)
        rsi_rule = next((r for r in results if r.rule == "rsi_cap"), None)
        assert rsi_rule and not rsi_rule.passed

    def test_existing_position_fails(self):
        detail = self._make_detail(score=8.0, symbol="AAPL")
        positions = [{"symbol": "AAPL", "market_value": 5000}]
        passed, results = apply_rules(detail, BULL_SIGNAL, _make_thesis(), positions, 100_000)
        pos_rule = next((r for r in results if r.rule == "no_existing_position"), None)
        assert pos_rule and not pos_rule.passed

    def test_max_positions_fails_at_limit(self):
        detail = self._make_detail(score=8.0, symbol="NVDA")
        positions = [{"symbol": f"S{i}", "market_value": 5000} for i in range(5)]
        passed, results = apply_rules(detail, BULL_SIGNAL, _make_thesis("NVDA"), positions, 100_000)
        max_rule = next((r for r in results if r.rule == "max_positions"), None)
        assert max_rule and not max_rule.passed

    def test_sector_exposure_etf_exempt(self):
        detail = self._make_detail(score=8.0, symbol="SPY")
        passed, results = apply_rules(
            detail, BULL_SIGNAL, _make_thesis("SPY"), [], 100_000
        )
        sector_rule = next((r for r in results if r.rule == "sector_exposure"), None)
        assert sector_rule and sector_rule.passed

    def test_filter_candidates_returns_approved_only(self):
        signals = {"AAPL": BULL_SIGNAL, "META": {**BEAR_SIGNAL, "above_sma200": False}}
        theses  = {
            "AAPL": _make_thesis("AAPL", "bullish", 4),
            "META": _make_thesis("META", "bearish", 3, technical_alignment=False),
        }
        from decision.scorer import rank_candidates
        ranked = rank_candidates(signals, theses)
        approved = filter_candidates(ranked, signals, theses, [], 100_000)
        syms = [d.symbol for d, _ in approved]
        assert "META" not in syms   # below SMA200


# ------------------------------------------------------------------
# Proposer tests
# ------------------------------------------------------------------

class TestProposer:
    def test_proposed_trade_dict_roundtrip(self):
        trade = ProposedTrade(
            symbol="AAPL", action="buy", qty=10,
            entry_price=276.00, stop_price=262.00, target_price=303.00,
            risk_reward=1.93, rationale="Test.", score=7.5, conviction=4,
            thesis_sentiment="bullish", model_used="claude-sonnet-4-6",
            generated_at="2026-05-05T00:00:00+00:00",
        )
        restored = ProposedTrade.from_dict(trade.to_dict())
        assert restored.symbol == "AAPL"
        assert restored.risk_reward == 1.93

    def test_propose_trade_live_claude(self):
        """One real Claude call — validates schema, geometry, and R/R."""
        detail = ScoreDetail(
            symbol="AAPL", total=7.5, technical=5.0, thesis_score=2.5,
            components={}
        )
        thesis = _make_thesis("AAPL", "bullish", 4)

        trade = propose_trade("AAPL", BULL_SIGNAL, thesis, detail, equity=100_000)

        if trade is None:
            pytest.skip("Claude returned skip/hold or API unavailable")

        assert trade.symbol == "AAPL"
        assert trade.action == "buy"
        assert trade.qty > 0
        assert trade.stop_price < trade.entry_price,  "Stop must be below entry"
        assert trade.target_price > trade.entry_price, "Target must be above entry"
        assert trade.risk_reward >= 1.5, f"R/R {trade.risk_reward} < 1.5"
        assert len(trade.rationale) > 20
        assert trade.model_used in ("claude-sonnet-4-6", "claude-opus-4-7")

    def test_propose_trade_no_api_key_returns_none(self):
        detail = ScoreDetail(symbol="AAPL", total=7.0, technical=5.0,
                             thesis_score=2.0, components={})
        thesis = _make_thesis("AAPL")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            result = propose_trade("AAPL", BULL_SIGNAL, thesis, detail, 100_000)
        assert result is None

    def test_load_proposal_rejects_stale(self, tmp_path):
        """load_proposal() must reject proposals older than 6 hours."""
        from decision.proposer import load_proposal, _TRADE_FILE
        from datetime import datetime, timezone, timedelta

        stale_trade = {
            "symbol": "AAPL", "action": "buy", "qty": 10,
            "entry_price": 276.0, "stop_price": 262.0, "target_price": 303.0,
            "risk_reward": 1.93, "rationale": "Old trade.", "score": 7.5,
            "conviction": 4, "thesis_sentiment": "bullish",
            "model_used": "claude-sonnet-4-6",
            "generated_at": (datetime.now(tz=timezone.utc) - timedelta(hours=10)).isoformat(),
        }
        stale_file = tmp_path / "proposed_trade.json"
        stale_file.write_text(json.dumps(stale_trade))

        with patch("decision.proposer._TRADE_FILE", stale_file):
            result = load_proposal()
        assert result is None

    def test_load_proposal_accepts_fresh(self, tmp_path):
        """load_proposal() must accept proposals less than 6 hours old."""
        from decision.proposer import load_proposal
        from datetime import datetime, timezone, timedelta

        fresh_trade = {
            "symbol": "AMD", "action": "buy", "qty": 30,
            "entry_price": 355.0, "stop_price": 322.0, "target_price": 410.0,
            "risk_reward": 1.66, "rationale": "Fresh trade.", "score": 8.0,
            "conviction": 4, "thesis_sentiment": "bullish",
            "model_used": "claude-opus-4-7",
            "generated_at": (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat(),
        }
        fresh_file = tmp_path / "proposed_trade.json"
        fresh_file.write_text(json.dumps(fresh_trade))

        with patch("decision.proposer._TRADE_FILE", fresh_file):
            result = load_proposal()
        assert result is not None
        assert result.symbol == "AMD"

    def test_propose_trade_uses_opus_for_conviction_5(self):
        """Model selection: conviction 5 → Opus."""
        from decision import proposer as p_mod
        called_with = []

        class FakeClient:
            def __init__(self, api_key): pass
            class messages:
                @staticmethod
                def create(**kwargs):
                    called_with.append(kwargs["model"])
                    # Return a mock tool_use response
                    import types
                    block = types.SimpleNamespace(
                        type="tool_use",
                        name="submit_trade_proposal",
                        input={
                            "action": "buy", "qty": 5,
                            "entry_price": 276.0, "stop_price": 262.0,
                            "target_price": 306.0, "rationale": "Test opus call.",
                        },
                    )
                    return types.SimpleNamespace(content=[block])

        with patch.object(p_mod.anthropic, "Anthropic", FakeClient):
            detail = ScoreDetail("AAPL", 9.0, 6.0, 3.0, {})
            thesis = _make_thesis("AAPL", conviction=5)
            propose_trade("AAPL", BULL_SIGNAL, thesis, detail, 100_000)

        assert called_with and called_with[0] == p_mod._MODEL_OPUS
