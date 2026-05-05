"""
Phase 6 tests — journal writer, lessons updater, context updater.
Lessons and context tests make one real Claude call each.
Run: pytest tests/test_journal.py -v
"""

import sys
import os
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from journal.writer import (
    write_entry, write_exit, get_recent_journals, purge_old_journals,
    EntryEvent, ExitEvent, _journal_path,
)
from journal.lessons_updater import update_lessons
from context.updater import update_context


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_entry_event(**kwargs) -> EntryEvent:
    defaults = dict(
        symbol="AAPL",
        fill_price=276.83,
        fill_qty=10,
        stop_price=262.00,
        target_price=303.00,
        risk_reward=1.93,
        conviction=4,
        score=7.5,
        thesis_sentiment="bullish",
        catalyst="Record iPhone sales in emerging markets drive Q2 beat.",
        risk_factors=["Fed tightening", "China slowdown"],
        rationale="AAPL above all SMAs with bullish MACD and RSI at 62.",
        atr=6.54,
        rsi=61.8,
        above_sma200=True,
        macd_bullish=True,
        model_used="claude-opus-4-7",
        order_id="ord-abc123",
        stop_order_id="ord-stop456",
        entry_date="2024-01-15",   # fixed historical date — never conflicts with live files
    )
    defaults.update(kwargs)
    return EntryEvent(**defaults)


def _make_exit_event(**kwargs) -> ExitEvent:
    defaults = dict(
        symbol="AAPL",
        exit_price=295.00,
        exit_qty=10,
        exit_reason="target_hit",
        exit_date="2026-05-08",
        pnl_dollars=181.70,
        pnl_pct=6.56,
    )
    defaults.update(kwargs)
    return ExitEvent(**defaults)


MOCK_SIGNALS = {
    "SPY":  {"close": 718.0, "rsi": 68.0, "bias": "bullish", "above_sma200": True,
             "bullish_signals": 4, "bearish_signals": 0, "above_sma50": True, "above_sma20": True},
    "AAPL": {"close": 276.8, "rsi": 62.0, "bias": "bullish", "above_sma200": True,
             "bullish_signals": 4, "bearish_signals": 1, "above_sma50": True, "above_sma20": True},
    "META": {"close": 610.0, "rsi": 41.0, "bias": "bearish", "above_sma200": False,
             "bullish_signals": 1, "bearish_signals": 4, "above_sma50": False, "above_sma20": False},
}

from research.thesis import Thesis

MOCK_THESES = {
    "AAPL": Thesis(
        symbol="AAPL", sentiment="bullish", conviction=4,
        catalyst="Strong iPhone momentum and Vision Pro enterprise.", risk_factors=["Fed risk"],
        technical_alignment=True, earnings_risk=False,
        summary="AAPL technically strong with improving fundamentals.",
        generated_at="2026-05-05T00:00:00+00:00",
    ),
}


# ------------------------------------------------------------------
# Journal writer tests
# ------------------------------------------------------------------

class TestJournalWriter:
    def test_write_entry_creates_file(self, tmp_path):
        event = _make_entry_event()
        path = write_entry(event, journal_dir=tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "AAPL" in content
        assert "$276.83" in content
        assert "Record iPhone" in content

    def test_write_entry_has_all_sections(self, tmp_path):
        path = write_entry(_make_entry_event(), journal_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        for section in ("## Thesis", "## Entry", "## Technical Snapshot", "## Exit", "## P&L"):
            assert section in content, f"Missing section: {section}"

    def test_write_entry_risk_math(self, tmp_path):
        event = _make_entry_event(fill_price=276.83, stop_price=262.00, fill_qty=10)
        path = write_entry(event, journal_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        # Risk = (276.83 - 262.00) * 10 = $148.30
        assert "148" in content

    def test_write_exit_appends_to_entry(self, tmp_path):
        write_entry(_make_entry_event(), journal_dir=tmp_path)
        path = write_exit(_make_exit_event(), journal_dir=tmp_path)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert "$295.00" in content
        assert "target_hit" in content
        assert "+181.70" in content or "181.70" in content

    def test_write_exit_marks_win(self, tmp_path):
        write_entry(_make_entry_event(), journal_dir=tmp_path)
        write_exit(_make_exit_event(pnl_dollars=181.70), journal_dir=tmp_path)
        path = list(tmp_path.glob("*.md"))[0]
        assert "WIN" in path.read_text(encoding="utf-8")

    def test_write_exit_marks_loss(self, tmp_path):
        write_entry(_make_entry_event(), journal_dir=tmp_path)
        write_exit(_make_exit_event(pnl_dollars=-80.00, exit_reason="stop_hit"), journal_dir=tmp_path)
        path = list(tmp_path.glob("*.md"))[0]
        assert "LOSS" in path.read_text(encoding="utf-8")

    def test_write_exit_no_entry_returns_none(self, tmp_path):
        result = write_exit(_make_exit_event(symbol="ZZZZ"), journal_dir=tmp_path)
        assert result is None

    def test_get_recent_journals_only_completed(self, tmp_path):
        write_entry(_make_entry_event(symbol="AAPL", entry_date="2026-05-01"), journal_dir=tmp_path)
        write_exit(_make_exit_event(symbol="AAPL"), journal_dir=tmp_path)
        write_entry(_make_entry_event(symbol="NVDA", entry_date="2026-05-02"), journal_dir=tmp_path)
        journals = get_recent_journals(n=5, journal_dir=tmp_path)
        assert len(journals) == 1
        assert "AAPL" in journals[0]

    def test_purge_old_journals(self, tmp_path):
        import journal.writer as jw
        old = tmp_path / "2020-01-01_AAPL.md"
        old.write_text("old trade", encoding="utf-8")
        new = tmp_path / f"{date.today().isoformat()}_AAPL.md"
        new.write_text("new trade", encoding="utf-8")
        with patch.object(jw, "_JOURNAL_DIR", tmp_path):
            with patch.object(jw, "_RETENTION_DAYS", 1):
                purge_old_journals()
        assert not old.exists()
        assert new.exists()


# ------------------------------------------------------------------
# Lessons updater tests
# ------------------------------------------------------------------

class TestLessonsUpdater:
    def test_no_journals_returns_empty(self, tmp_path):
        import journal.lessons_updater as lu
        with patch.object(lu, "_LESSONS_FILE", tmp_path / "lessons.md"):
            result = update_lessons(journals=[])
        assert result == ""

    def test_live_claude_generates_lessons(self, tmp_path):
        """Real Claude call — validates that lessons are appended correctly."""
        import journal.lessons_updater as lu

        # Use real journal text from our fixtures
        journal_text = write_entry(_make_entry_event()).read_text()
        # Simulate a completed trade by adding an exit
        journal_text += "\n## Exit *(updated 2026-05-08)*\n| Exit price | $295.00 |\n"
        journal_text += "\n## P&L *(updated 2026-05-08)*\n| P&L ($) | +$181.70 |\n| Outcome | **WIN** |"

        lessons_file = tmp_path / "lessons.md"
        with patch.object(lu, "_LESSONS_FILE", lessons_file):
            result = update_lessons(journals=[journal_text])

        assert len(result) > 50, "Expected non-trivial lessons text from Claude"
        assert lessons_file.exists()
        content = lessons_file.read_text()
        assert "## Session:" in content

    def test_no_api_key_returns_empty(self, tmp_path):
        import journal.lessons_updater as lu
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with patch.object(lu, "_LESSONS_FILE", tmp_path / "lessons.md"):
                result = update_lessons(journals=["some journal"])
        assert result == ""


# ------------------------------------------------------------------
# Context updater tests
# ------------------------------------------------------------------

class TestContextUpdater:
    def test_empty_signals_returns_empty(self):
        result = update_context(signals={}, theses={})
        assert result == ""

    def test_no_api_key_returns_empty(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            result = update_context(signals=MOCK_SIGNALS, theses=MOCK_THESES)
        assert result == ""

    def test_live_claude_writes_context_file(self, tmp_path):
        """Real Claude call — validates market_context.md is rewritten."""
        import context.updater as cu
        ctx_file = tmp_path / "market_context.md"
        with patch.object(cu, "_CONTEXT_FILE", ctx_file):
            result = update_context(
                signals=MOCK_SIGNALS,
                theses=MOCK_THESES,
                session_pnl=181.70,
                lessons_snippet="RSI > 75 at entry led to a loss — add RSI ≤ 70 filter.",
            )

        assert len(result) > 100
        assert ctx_file.exists()
        content = ctx_file.read_text(encoding="utf-8")
        assert "# Market Context" in content
        assert "Last updated:" in content
        # Should mention at least one ticker
        assert any(sym in content for sym in ("SPY", "AAPL", "META"))

    def test_context_file_has_required_sections(self, tmp_path):
        import context.updater as cu
        ctx_file = tmp_path / "market_context.md"
        with patch.object(cu, "_CONTEXT_FILE", ctx_file):
            update_context(signals=MOCK_SIGNALS, theses=MOCK_THESES)
        content = ctx_file.read_text(encoding="utf-8")
        for section in ("Regime", "Bias"):
            assert section in content, f"Missing section: {section}"
