"""
Phase 7 tests — scheduler logic and ClickUp integration.
No live ClickUp API calls (requires credentials); scheduler tested by mocking schedule.
Run: pytest tests/test_phase7.py -v
"""

import sys
import os
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.clickup import build_summary, post_daily_summary, _get_credentials


# ------------------------------------------------------------------
# Scheduler tests
# ------------------------------------------------------------------

class TestScheduler:
    def test_is_trading_day_weekday(self):
        from routines.scheduler import _is_trading_day
        # Monday 2026-05-04
        monday = datetime(2026, 5, 4, 9, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
        with patch("routines.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            assert _is_trading_day()

    def test_is_trading_day_saturday(self):
        from routines.scheduler import _is_trading_day
        saturday = datetime(2026, 5, 2, 9, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
        with patch("routines.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            assert not _is_trading_day()

    def test_is_trading_day_holiday(self):
        from routines.scheduler import _is_trading_day
        # Christmas 2026 — a Friday
        xmas = datetime(2026, 12, 25, 9, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
        with patch("routines.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = xmas
            assert not _is_trading_day()

    def test_run_guarded_skips_non_trading_day(self):
        from routines.scheduler import _run_guarded
        called = []
        saturday = datetime(2026, 5, 2, 9, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
        with patch("routines.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            _run_guarded("test", lambda: called.append(True))
        assert len(called) == 0

    def test_run_guarded_calls_fn_on_trading_day(self):
        from routines.scheduler import _run_guarded
        called = []
        monday = datetime(2026, 5, 4, 9, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
        with patch("routines.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            _run_guarded("test", lambda: called.append(True))
        assert len(called) == 1

    def test_run_guarded_catches_exceptions(self):
        from routines.scheduler import _run_guarded
        monday = datetime(2026, 5, 4, 9, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
        with patch("routines.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            # Should not raise
            _run_guarded("test", lambda: 1 / 0)

    def test_schedule_has_three_jobs(self):
        import schedule as sched
        import routines.scheduler  # ensures module-level schedule.every() calls ran
        jobs = sched.jobs
        assert len(jobs) >= 3, f"Expected at least 3 scheduled jobs, got {len(jobs)}"


# ------------------------------------------------------------------
# ClickUp integration tests
# ------------------------------------------------------------------

class TestClickUp:
    def test_build_summary_structure(self):
        summary = build_summary(session_pnl=181.70, trades_taken=1)
        assert "name" in summary
        assert "description" in summary
        assert "181.70" in summary["name"] or "181" in summary["name"]
        assert "WIN" in summary["name"]

    def test_build_summary_loss(self):
        summary = build_summary(session_pnl=-50.0, trades_taken=1)
        assert "LOSS" in summary["name"]

    def test_build_summary_flat(self):
        summary = build_summary(session_pnl=0.0, trades_taken=0)
        assert "FLAT" in summary["name"]

    def test_build_summary_has_required_sections(self):
        summary = build_summary(session_pnl=100.0, trades_taken=1)
        desc = summary["description"]
        assert "Session P&L" in desc
        assert "Market Bias" in desc
        assert "Lesson" in desc

    def test_no_credentials_returns_false(self):
        with patch.dict(os.environ, {"CLICKUP_API_TOKEN": "", "CLICKUP_LIST_ID": ""}):
            result = post_daily_summary(session_pnl=100.0)
        assert result is False

    def test_get_credentials_missing(self):
        with patch.dict(os.environ, {"CLICKUP_API_TOKEN": "", "CLICKUP_LIST_ID": ""}):
            token, list_id = _get_credentials()
        assert not token
        assert not list_id

    def test_post_daily_summary_success(self):
        """Mock a successful ClickUp POST."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "abc123", "url": "https://app.clickup.com/t/abc123"}
        mock_resp.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"CLICKUP_API_TOKEN": "pk_test", "CLICKUP_LIST_ID": "99999"}):
            with patch("integrations.clickup.requests.post", return_value=mock_resp) as mock_post:
                result = post_daily_summary(session_pnl=250.0, trades_taken=2)

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "99999" in call_kwargs[0][0]   # list_id in URL
        payload = call_kwargs[1]["json"]
        assert "250" in payload["name"]

    def test_post_daily_summary_http_error(self):
        """HTTP 401 → returns False without raising."""
        import requests as req
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError(response=mock_resp)
        mock_resp.text = "Unauthorized"

        with patch.dict(os.environ, {"CLICKUP_API_TOKEN": "bad_token", "CLICKUP_LIST_ID": "99999"}):
            with patch("integrations.clickup.requests.post", return_value=mock_resp):
                result = post_daily_summary(session_pnl=100.0)

        assert result is False

    def test_extra_notes_in_description(self):
        summary = build_summary(session_pnl=100.0, extra_notes="RSI was 82 — avoid chasing")
        assert "RSI was 82" in summary["description"]
