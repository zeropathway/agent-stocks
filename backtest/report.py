"""
Generate a Markdown + JSON report from a BacktestResult + Metrics.
Saves to backtest/results/backtest_{timestamp}.{md,json}.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from backtest.engine import BacktestResult, Trade
from backtest.metrics import Metrics

log = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent / "results"


def generate_report(result: BacktestResult, metrics: Metrics) -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    md_path   = _RESULTS_DIR / f"backtest_{ts}.md"
    json_path = _RESULTS_DIR / f"backtest_{ts}.json"

    # --- JSON ---
    report_data = {
        "generated_at": datetime.now().isoformat(),
        "symbols":      result.symbols,
        "start_date":   str(result.start_date),
        "end_date":     str(result.end_date),
        "start_equity": result.start_equity,
        "end_equity":   round(result.end_equity, 2),
        "metrics":      metrics.to_dict(),
        "trades": [
            {
                "symbol":       t.symbol,
                "entry_date":   str(t.entry_date),
                "exit_date":    str(t.exit_date),
                "entry_price":  round(t.entry_price, 2),
                "exit_price":   round(t.exit_price, 2) if t.exit_price else None,
                "stop_price":   round(t.stop_price, 2),
                "target_price": round(t.target_price, 2),
                "qty":          round(t.qty, 2),
                "pnl":          round(t.pnl, 2),
                "r_multiple":   round(t.r_multiple, 3),
                "exit_reason":  t.exit_reason,
                "score":        round(t.score, 2),
            }
            for t in result.trades
        ],
    }
    json_path.write_text(json.dumps(report_data, indent=2))

    # --- Markdown ---
    m           = metrics
    symbols_str = ", ".join(result.symbols)
    closed      = [t for t in result.trades if t.exit_reason != "end_of_backtest"]
    by_pnl      = sorted(closed, key=lambda t: t.pnl, reverse=True)
    top5        = by_pnl[:5]
    bot5        = by_pnl[-5:]

    header = "| Symbol | Entry | Exit | Entry $ | Exit $ | P&L | R | Reason |"
    sep    = "|--------|-------|------|---------|--------|-----|---|--------|"

    def row(t: Trade) -> str:
        return (
            f"| {t.symbol} | {t.entry_date} | {t.exit_date} | "
            f"${t.entry_price:.2f} | ${t.exit_price:.2f} | "
            f"${t.pnl:+,.2f} | {t.r_multiple:.2f}R | {t.exit_reason} |"
        )

    calmar_str = f"{m.calmar:.3f}" if m.calmar != float("inf") else "∞"
    pf_str     = f"{m.profit_factor:.3f}" if m.profit_factor != float("inf") else "∞"

    md = f"""# Backtest Report

**Symbols**: {symbols_str}
**Period**: {result.start_date} → {result.end_date} ({m.years:.1f} years)
**Start equity**: ${result.start_equity:,.0f} | **End equity**: ${result.end_equity:,.0f}

---

## Summary Metrics

| Metric | Value |
|--------|-------|
| Total Return | {m.total_return:+.2f}% |
| CAGR | {m.cagr:+.2f}% |
| Sharpe Ratio | {m.sharpe:.3f} |
| Sortino Ratio | {m.sortino:.3f} |
| Calmar Ratio | {calmar_str} |
| Max Drawdown | {m.max_drawdown:.2f}% |
| Max DD Duration | {m.max_dd_duration_days} days |

## Trade Statistics

| Metric | Value |
|--------|-------|
| Total Trades | {m.total_trades} |
| Win Rate | {m.win_rate:.1f}% |
| Winning / Losing | {m.winning_trades} / {m.losing_trades} |
| Profit Factor | {pf_str} |
| Avg R Multiple | {m.avg_r_multiple:.3f}R |
| Avg Win | ${m.avg_win_pnl:+,.2f} |
| Avg Loss | ${m.avg_loss_pnl:+,.2f} |
| Best Trade | ${m.best_trade_pnl:+,.2f} |
| Worst Trade | ${m.worst_trade_pnl:+,.2f} |

---

## Top 5 Wins

{header}
{sep}
{chr(10).join(row(t) for t in top5)}

## Top 5 Losses

{header}
{sep}
{chr(10).join(row(t) for t in bot5)}

---
*Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} — Trading Agent backtest harness*
"""

    md_path.write_text(md, encoding="utf-8")
    log.info("Markdown report → %s", md_path.name)
    log.info("JSON report     → %s", json_path.name)
    return md_path
