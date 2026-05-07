"""
Bar-by-bar backtesting engine.

Mirrors the live system exactly — same indicators, scorer, and rules —
but replaces Claude API calls with the rule-based thesis fallback so
backtests are fast and free.

No look-ahead bias:
  - Indicators computed on the full historical series (each value only
    looks backward, rolling windows are causal).
  - Signal generated from close of bar T.
  - Entry executed at open of bar T+1.
  - Stop / target checked from bar T+2 onward using intraday high/low.

Position sizing (matches live config):
  - risk_per_trade = equity * 1%
  - stop = entry - ATR(14) * 2.0
  - target = entry + risk_per_share * 1.5   (R/R >= 1.5)
  - qty = risk_per_trade / risk_per_share
  - concentration cap: single position <= 30% of equity
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

from backtest.historical import fetch_history
from data.indicators import add_all_indicators
from decision.scorer import score_ticker
from research.earnings import EarningsInfo
from research.thesis import _rule_based_thesis

log = logging.getLogger(__name__)

# --- live system constants (keep in sync with config.yaml) ---
MAX_POSITIONS        = 5
MAX_RISK_PER_TRADE   = 0.01   # 1% of equity
ATR_STOP_MULT        = 2.0
MIN_RR               = 1.5
MIN_SCORE            = 6.5    # raised from 5.0 — backtest shows better Sharpe/profit factor
RSI_HARD_CAP         = 70.0   # lowered from 80 — avoid overbought entries
MIN_BARS             = 250    # raised from 220 — more reliable indicator warmup

_DUMMY_EARNINGS = EarningsInfo(symbol="BACKTEST")   # no earnings risk in backtest


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float
    qty: float
    score: float
    risk_per_share: float
    # filled on exit
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""        # "stop" | "target" | "end_of_backtest"
    pnl: float = 0.0
    r_multiple: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series      # indexed by pd.Timestamp
    start_equity: float
    end_equity: float
    symbols: list[str]
    start_date: date
    end_date: date


# ------------------------------------------------------------------
# Signal extraction (re-implements get_signal_summary for a single row)
# ------------------------------------------------------------------

def _bar_to_signal(row) -> Optional[dict]:
    """Convert an indicator-enriched bar row to a signal dict."""

    def _f(key: str) -> Optional[float]:
        v = row.get(key) if hasattr(row, "get") else getattr(row, key, None)
        try:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    close = _f("close")
    if close is None or close <= 0:
        return None

    rsi       = _f("rsi")
    sma_20    = _f("sma_20")
    sma_50    = _f("sma_50")
    sma_200   = _f("sma_200")
    macd      = _f("macd")
    macd_sig  = _f("macd_signal")
    macd_hist = _f("macd_hist")
    vol_ratio = _f("vol_ratio")
    atr       = _f("atr")

    above_sma20  = (close > sma_20)  if sma_20  is not None else None
    above_sma50  = (close > sma_50)  if sma_50  is not None else None
    above_sma200 = (close > sma_200) if sma_200 is not None else None

    macd_bullish = (
        macd_hist is not None and macd_hist > 0
        and macd is not None and macd_sig is not None
        and macd > macd_sig
    )

    rsi_zone = (
        "oversold"   if rsi is not None and rsi < 30 else
        "overbought" if rsi is not None and rsi > 70 else
        "neutral"
    )

    elevated_volume = vol_ratio is not None and vol_ratio > 1.5

    bullish_signals = sum([
        above_sma20  is True,
        above_sma50  is True,
        above_sma200 is True,
        macd_bullish,
        rsi_zone == "oversold",
        elevated_volume,
    ])
    bearish_signals = sum([
        above_sma20  is False,
        above_sma50  is False,
        above_sma200 is False,
        not macd_bullish,
        rsi_zone == "overbought",
    ])

    bias = (
        "bullish" if bullish_signals > bearish_signals else
        "bearish" if bearish_signals > bullish_signals else
        "neutral"
    )

    return {
        "close":           close,
        "atr":             atr,
        "rsi":             rsi,
        "rsi_zone":        rsi_zone,
        "macd":            macd,
        "macd_signal":     macd_sig,
        "macd_hist":       macd_hist,
        "macd_bullish":    macd_bullish,
        "sma_20":          sma_20,
        "sma_50":          sma_50,
        "sma_200":         sma_200,
        "above_sma20":     above_sma20,
        "above_sma50":     above_sma50,
        "above_sma200":    above_sma200,
        "bb_pct":          _f("bb_pct"),
        "vol_ratio":       vol_ratio,
        "elevated_volume": elevated_volume,
        "bullish_signals": bullish_signals,
        "bearish_signals": bearish_signals,
        "bias":            bias,
    }


# ------------------------------------------------------------------
# Main engine
# ------------------------------------------------------------------

def run_backtest(
    symbols: list[str],
    years: int = 20,
    start_equity: float = 100_000.0,
    force_refresh: bool = False,
) -> BacktestResult:
    """
    Run a bar-by-bar backtest on `symbols` for up to `years` years of history.
    Returns a BacktestResult containing all trades and the daily equity curve.
    """
    # 1. Fetch and indicator-enrich data for every symbol
    data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        raw = fetch_history(sym, years=years + 1, force_refresh=force_refresh)
        if raw.empty:
            log.warning("Skipping %s — no data", sym)
            continue
        enriched = add_all_indicators(raw)
        enriched = enriched.set_index("date")
        data[sym] = enriched

    if not data:
        raise ValueError("No data fetched for any symbol")

    # 2. Build unified trading calendar (union of all dates, sorted)
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    log.info("Calendar: %d trading days across %d symbols", len(all_dates), len(data))

    # 3. Simulation state
    equity: float              = start_equity
    positions: dict[str, Trade]       = {}   # currently open
    pending:   dict[str, Trade]       = {}   # entered tomorrow at open
    completed: list[Trade]            = []
    equity_by_date: dict              = {}

    for i, today in enumerate(all_dates):

        # --- Execute entries queued from yesterday ---
        for sym, trade in list(pending.items()):
            if sym not in positions and len(positions) < MAX_POSITIONS:
                positions[sym] = trade
                log.debug("ENTER  %s @ $%.2f  stop=$%.2f  target=$%.2f  qty=%.1f  score=%.1f",
                          sym, trade.entry_price, trade.stop_price,
                          trade.target_price, trade.qty, trade.score)
        pending.clear()

        # --- Process exits for open positions (skip entry day) ---
        for sym in list(positions.keys()):
            trade = positions[sym]
            if sym not in data or today not in data[sym].index:
                continue
            # No exit check on the entry bar itself
            if pd.Timestamp(trade.entry_date) >= today:
                continue

            bar  = data[sym].loc[today]
            low  = float(bar["low"])
            high = float(bar["high"])

            if low <= trade.stop_price:
                _close_trade(trade, today, trade.stop_price, "stop", equity)
                equity += trade.pnl
                completed.append(trade)
                del positions[sym]
                log.debug("STOP   %s  pnl=$%+.2f  R=%.2fR", sym, trade.pnl, trade.r_multiple)

            elif high >= trade.target_price:
                _close_trade(trade, today, trade.target_price, "target", equity)
                equity += trade.pnl
                completed.append(trade)
                del positions[sym]
                log.debug("TARGET %s  pnl=$%+.2f  R=%.2fR", sym, trade.pnl, trade.r_multiple)

        # --- Generate signals and queue entries for tomorrow ---
        if i + 1 < len(all_dates):
            next_date = all_dates[i + 1]
            slots = MAX_POSITIONS - len(positions) - len(pending)

            if slots > 0:
                candidates: list[tuple[float, str, dict]] = []

                for sym in symbols:
                    if sym in positions or sym in pending:
                        continue
                    if sym not in data or today not in data[sym].index:
                        continue
                    # Require MIN_BARS of history before the current bar
                    hist_len = int((data[sym].index <= today).sum())
                    if hist_len < MIN_BARS:
                        continue

                    signal = _bar_to_signal(data[sym].loc[today])
                    if signal is None:
                        continue

                    # Hard filters (mirror rules.py)
                    if (signal.get("rsi") or 50.0) > RSI_HARD_CAP:
                        continue
                    if not signal.get("above_sma200"):
                        continue

                    thesis = _rule_based_thesis(sym, signal, _DUMMY_EARNINGS)
                    detail = score_ticker(signal, thesis)
                    if detail.total < MIN_SCORE:
                        continue

                    # Must have next bar data for entry price
                    if next_date not in data[sym].index:
                        continue

                    candidates.append((detail.total, sym, signal))

                # Rank by score, take top `slots`
                candidates.sort(reverse=True)

                for score, sym, signal in candidates[:slots]:
                    next_bar     = data[sym].loc[next_date]
                    entry_price  = float(next_bar["open"])
                    if entry_price <= 0:
                        continue

                    atr = signal.get("atr") or (entry_price * 0.02)
                    if not atr or atr <= 0:
                        atr = entry_price * 0.02

                    stop_price     = round(entry_price - atr * ATR_STOP_MULT, 4)
                    risk_per_share = entry_price - stop_price
                    if risk_per_share <= 0:
                        continue
                    target_price = round(entry_price + risk_per_share * MIN_RR, 4)

                    # Position sizing: risk 1% of equity
                    max_loss = equity * MAX_RISK_PER_TRADE
                    qty      = max_loss / risk_per_share
                    # Concentration cap: no single position > 30% of equity
                    if qty * entry_price > equity * 0.30:
                        qty = (equity * 0.30) / entry_price
                    qty = max(1.0, round(qty, 4))

                    pending[sym] = Trade(
                        symbol=sym,
                        entry_date=next_date.date(),
                        entry_price=entry_price,
                        stop_price=stop_price,
                        target_price=target_price,
                        qty=qty,
                        score=score,
                        risk_per_share=risk_per_share,
                    )

        equity_by_date[today] = equity

    # --- Close any remaining open positions at last close ---
    last_date = all_dates[-1]
    for sym, trade in {**positions, **pending}.items():
        close_px = (
            float(data[sym].loc[last_date]["close"])
            if sym in data and last_date in data[sym].index
            else trade.entry_price
        )
        _close_trade(trade, last_date, close_px, "end_of_backtest", equity)
        equity += trade.pnl
        completed.append(trade)

    equity_curve = pd.Series(equity_by_date)

    return BacktestResult(
        trades=completed,
        equity_curve=equity_curve,
        start_equity=start_equity,
        end_equity=equity,
        symbols=list(data.keys()),
        start_date=all_dates[0].date(),
        end_date=all_dates[-1].date(),
    )


def _close_trade(trade: Trade, ts, exit_price: float, reason: str, equity: float):
    trade.exit_date    = ts.date() if hasattr(ts, "date") else ts
    trade.exit_price   = round(exit_price, 4)
    trade.exit_reason  = reason
    trade.pnl          = round((exit_price - trade.entry_price) * trade.qty, 4)
    trade.r_multiple   = round(
        (exit_price - trade.entry_price) / trade.risk_per_share, 4
    ) if trade.risk_per_share > 0 else 0.0
