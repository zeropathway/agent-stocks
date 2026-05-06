"""
Performance metrics computed from a BacktestResult.

All percentage values are stored as percentages (e.g. 12.5 means 12.5%).
"""

import math
from dataclasses import dataclass

import pandas as pd

from backtest.engine import BacktestResult, Trade

RISK_FREE_RATE = 0.045   # 4.5% annual (US 3-month T-bill circa 2024-2025)
TRADING_DAYS   = 252


@dataclass
class Metrics:
    # Returns
    total_return: float          # %
    cagr: float                  # % annualised
    years: float

    # Risk-adjusted
    sharpe: float
    sortino: float
    calmar: float

    # Drawdown
    max_drawdown: float          # % (negative value)
    max_dd_duration_days: int

    # Trade stats
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float              # %
    profit_factor: float
    avg_r_multiple: float
    avg_win_pnl: float
    avg_loss_pnl: float
    best_trade_pnl: float
    worst_trade_pnl: float

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


def compute_metrics(result: BacktestResult) -> Metrics:
    equity = result.equity_curve

    total_days = (result.end_date - result.start_date).days
    years      = max(total_days / 365.25, 1e-6)

    total_return = (result.end_equity - result.start_equity) / result.start_equity * 100
    cagr         = ((1 + total_return / 100) ** (1 / years) - 1) * 100

    # Daily returns
    daily_ret = equity.pct_change().dropna()
    excess    = daily_ret - RISK_FREE_RATE / TRADING_DAYS

    sharpe = (
        (excess.mean() / excess.std() * math.sqrt(TRADING_DAYS))
        if excess.std() > 0 else 0.0
    )

    downside      = excess[excess < 0]
    sortino_denom = math.sqrt((downside ** 2).mean()) * math.sqrt(TRADING_DAYS) if len(downside) > 0 else 1e-9
    sortino       = (excess.mean() * TRADING_DAYS / sortino_denom) if sortino_denom > 0 else 0.0

    # Max drawdown
    roll_max  = equity.cummax()
    drawdown  = (equity - roll_max) / roll_max
    max_dd    = float(drawdown.min()) * 100   # convert to %

    # Max drawdown duration (consecutive bars in drawdown)
    in_dd    = drawdown < 0
    groups   = (in_dd != in_dd.shift()).cumsum()
    max_dur  = 0
    for _, g in drawdown.groupby(groups):
        if (g < 0).any():
            max_dur = max(max_dur, len(g))

    calmar = (cagr / abs(max_dd)) if max_dd < 0 else float("inf")

    # Only count trades that closed before end-of-backtest for performance stats
    closed = [t for t in result.trades if t.exit_reason != "end_of_backtest"]
    wins   = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]

    win_rate      = len(wins) / len(closed) * 100 if closed else 0.0
    gross_profit  = sum(t.pnl for t in wins)
    gross_loss    = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_r         = sum(t.r_multiple for t in closed) / len(closed) if closed else 0.0
    avg_win       = sum(t.pnl for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss      = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    pnls          = [t.pnl for t in closed]

    return Metrics(
        total_return=round(total_return, 2),
        cagr=round(cagr, 2),
        years=round(years, 2),
        sharpe=round(sharpe, 3),
        sortino=round(sortino, 3),
        calmar=round(calmar, 3),
        max_drawdown=round(max_dd, 2),
        max_dd_duration_days=max_dur,
        total_trades=len(closed),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(win_rate, 2),
        profit_factor=round(profit_factor, 3),
        avg_r_multiple=round(avg_r, 3),
        avg_win_pnl=round(avg_win, 2),
        avg_loss_pnl=round(avg_loss, 2),
        best_trade_pnl=round(max(pnls), 2) if pnls else 0.0,
        worst_trade_pnl=round(min(pnls), 2) if pnls else 0.0,
    )
