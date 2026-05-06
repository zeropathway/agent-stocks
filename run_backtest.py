"""
Backtesting CLI.

Usage:
    python run_backtest.py                           # default: 10 symbols, 20 years
    python run_backtest.py --years 10
    python run_backtest.py --symbols AAPL MSFT NVDA --years 15 --equity 50000
    python run_backtest.py --force-refresh           # re-download all data
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest.engine import run_backtest
from backtest.metrics import compute_metrics
from backtest.report import generate_report

_DEFAULT_SYMBOLS = [
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA", "AMD",
    "META", "GOOGL", "AMZN", "TSLA",
    "JPM", "GS", "XOM", "UNH", "LLY",
]


def main():
    parser = argparse.ArgumentParser(description="Run trading strategy backtest")
    parser.add_argument("--symbols", nargs="+", default=_DEFAULT_SYMBOLS,
                        help="Symbols to include (default: full universe)")
    parser.add_argument("--years", type=int, default=20,
                        help="Years of history to use (default: 20)")
    parser.add_argument("--equity", type=float, default=100_000.0,
                        help="Starting equity in USD (default: 100000)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Force re-download of all historical data")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show DEBUG-level logs (individual trades)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    log.info("=== BACKTEST START ===")
    log.info("Symbols  : %s", ", ".join(args.symbols))
    log.info("Years    : %d  |  Start equity: $%.0f", args.years, args.equity)
    log.info("Data src : yfinance (up to 25 years free daily OHLCV)")

    result  = run_backtest(
        symbols=args.symbols,
        years=args.years,
        start_equity=args.equity,
        force_refresh=args.force_refresh,
    )
    metrics = compute_metrics(result)
    report  = generate_report(result, metrics)

    log.info("=== BACKTEST COMPLETE ===")
    log.info("Period       : %s → %s  (%.1f years)", result.start_date, result.end_date, metrics.years)
    log.info("Total Return : %+.2f%%  |  CAGR: %+.2f%%", metrics.total_return, metrics.cagr)
    log.info("Sharpe       : %.3f    |  Sortino: %.3f  |  Max DD: %.2f%%",
             metrics.sharpe, metrics.sortino, metrics.max_drawdown)
    log.info("Trades       : %d  |  Win Rate: %.1f%%  |  Profit Factor: %.3f",
             metrics.total_trades, metrics.win_rate, metrics.profit_factor)
    log.info("Avg R        : %.3fR  |  Best: $%.0f  |  Worst: $%.0f",
             metrics.avg_r_multiple, metrics.best_trade_pnl, metrics.worst_trade_pnl)
    log.info("Report       : %s", report)
    return metrics


if __name__ == "__main__":
    main()
