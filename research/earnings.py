"""
Earnings calendar — next earnings date + EPS estimates via yfinance.

Usage:
    from research.earnings import fetch_earnings_info
    info = fetch_earnings_info("AAPL")
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class EarningsInfo:
    symbol: str
    next_earnings_date: Optional[date] = None
    days_until_earnings: Optional[int] = None
    eps_estimate: Optional[float] = None
    eps_actual_last: Optional[float] = None
    revenue_estimate: Optional[float] = None
    earnings_within_days: int = 0   # 0 = no upcoming; >0 = days away
    is_earnings_week: bool = False   # True if earnings within 7 days


def fetch_earnings_info(symbol: str) -> EarningsInfo:
    """
    Pull next earnings date and EPS estimates from yfinance.
    Returns an EarningsInfo with all fields None on failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        info = EarningsInfo(symbol=symbol)

        # Next earnings date
        cal = ticker.calendar
        if cal is not None:
            # yfinance returns a DataFrame or a dict depending on version/ticker type
            if isinstance(cal, dict):
                # dict form: {"Earnings Date": [datetime, ...], ...}
                raw_dates = cal.get("Earnings Date", [])
                if raw_dates:
                    first = raw_dates[0]
                    info.next_earnings_date = first.date() if hasattr(first, "date") else first
            elif hasattr(cal, "empty") and not cal.empty and "Earnings Date" in cal.index:
                raw = cal.loc["Earnings Date"]
                first = raw.iloc[0] if hasattr(raw, "iloc") else raw
                if hasattr(first, "date"):
                    info.next_earnings_date = first.date()
                elif isinstance(first, date):
                    info.next_earnings_date = first

        if info.next_earnings_date:
            delta = (info.next_earnings_date - date.today()).days
            info.days_until_earnings = delta
            info.earnings_within_days = max(delta, 0)
            info.is_earnings_week = 0 <= delta <= 7

        # EPS estimates — from ticker.earnings_dates or ticker.info
        try:
            ticker_info = ticker.info
            info.eps_estimate    = ticker_info.get("epsForwardAnnual") or ticker_info.get("forwardEps")
            info.eps_actual_last = ticker_info.get("trailingEps")
            info.revenue_estimate = ticker_info.get("revenueEstimate") or ticker_info.get("totalRevenue")
        except Exception:
            pass

        log.debug(
            "%s: next earnings %s (%s days), EPS est=%.2f",
            symbol,
            info.next_earnings_date,
            info.days_until_earnings,
            info.eps_estimate or 0,
        )
        return info

    except Exception as e:
        log.warning("Earnings fetch failed for %s: %s", symbol, e)
        return EarningsInfo(symbol=symbol)


def earnings_to_text(info: EarningsInfo) -> str:
    """Format earnings info into a compact string for the Claude prompt."""
    parts = [f"Earnings info for {info.symbol}:"]
    if info.next_earnings_date:
        parts.append(f"  Next earnings: {info.next_earnings_date} ({info.days_until_earnings} days away)")
        if info.is_earnings_week:
            parts.append("  *** EARNINGS THIS WEEK — elevated volatility risk ***")
    else:
        parts.append("  Next earnings: unknown")
    if info.eps_estimate is not None:
        parts.append(f"  Forward EPS estimate: ${info.eps_estimate:.2f}")
    if info.eps_actual_last is not None:
        parts.append(f"  Trailing EPS (actual): ${info.eps_actual_last:.2f}")
    return "\n".join(parts)
