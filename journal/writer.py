"""
Trade journal writer.

Lifecycle:
  1. write_entry()  — called immediately after fill; creates the journal file
  2. write_exit()   — called when position closes; appends exit section and P&L analysis

Journal files live at: journal/trades/{date}_{symbol}.md
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_JOURNAL_DIR = Path(__file__).parent / "trades"
_RETENTION_DAYS = 90


@dataclass
class EntryEvent:
    symbol: str
    fill_price: float
    fill_qty: int
    stop_price: float
    target_price: float
    risk_reward: float
    conviction: int
    score: float
    thesis_sentiment: str
    catalyst: str
    risk_factors: list[str]
    rationale: str
    atr: float
    rsi: float
    above_sma200: bool
    macd_bullish: bool
    model_used: str
    order_id: str
    stop_order_id: Optional[str]
    entry_date: str = ""        # ISO date

    def risk_dollars(self) -> float:
        return (self.fill_price - self.stop_price) * self.fill_qty

    def notional(self) -> float:
        return self.fill_price * self.fill_qty


@dataclass
class ExitEvent:
    symbol: str
    exit_price: float
    exit_qty: int
    exit_reason: str            # "stop_hit" | "target_hit" | "eod_close" | "manual"
    exit_date: str              # ISO date
    pnl_dollars: float
    pnl_pct: float


def _journal_path(symbol: str, entry_date: str, journal_dir: Path | None = None) -> Path:
    d = journal_dir or _JOURNAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{entry_date}_{symbol}.md"


def write_entry(event: EntryEvent, journal_dir: Path | None = None) -> Path:
    """Write the full entry section of a trade journal. Returns the file path."""
    if not event.entry_date:
        event.entry_date = date.today().isoformat()

    path = _journal_path(event.symbol, event.entry_date, journal_dir)
    risk_d = event.risk_dollars()
    risk_pct = (risk_d / event.notional() * 100) if event.notional() else 0

    content = f"""# Trade: {event.symbol} — {event.entry_date}

## Thesis
| Field | Value |
|-------|-------|
| Sentiment | {event.thesis_sentiment} |
| Conviction | {event.conviction}/5 |
| Score | {event.score}/10 |
| Model | {event.model_used} |

**Catalyst:** {event.catalyst}

**Risk factors:**
{chr(10).join(f'- {r}' for r in event.risk_factors)}

## Entry
| Field | Value |
|-------|-------|
| Fill price | ${event.fill_price:.2f} |
| Qty | {event.fill_qty} shares |
| Notional | ${event.notional():,.0f} |
| Stop | ${event.stop_price:.2f} |
| Target | ${event.target_price:.2f} |
| Risk/trade | ${risk_d:.0f} ({risk_pct:.1f}% of notional) |
| R/R | {event.risk_reward:.2f} |
| Order ID | {event.order_id} |
| Stop order ID | {event.stop_order_id or 'pending'} |

**Entry rationale:** {event.rationale}

## Technical Snapshot at Entry
| Indicator | Value |
|-----------|-------|
| ATR(14) | ${event.atr:.2f} |
| RSI(14) | {event.rsi:.1f} |
| Above SMA200 | {event.above_sma200} |
| MACD bullish | {event.macd_bullish} |

## Exit
*Pending — position open.*

## P&L
*Pending — position open.*

## Lessons
*To be written by lessons_updater.py after position closes.*
"""
    path.write_text(content, encoding="utf-8")
    log.info("Journal entry written: %s", path)
    return path


def write_exit(event: ExitEvent, journal_dir: Path | None = None) -> Optional[Path]:
    """
    Append exit section and P&L to an existing journal entry.
    Searches for the most recent journal file for the symbol.
    """
    search_dir = journal_dir or _JOURNAL_DIR
    matches = sorted(
        search_dir.glob(f"*_{event.symbol}.md"),
        reverse=True,
    )
    if not matches:
        log.warning("No journal entry found for %s — cannot write exit", event.symbol)
        return None

    path = matches[0]
    existing = path.read_text(encoding="utf-8")

    outcome = "WIN" if event.pnl_dollars > 0 else "LOSS" if event.pnl_dollars < 0 else "SCRATCH"
    r_multiple = 0.0
    # Try to extract risk-per-trade from existing text to compute R multiple
    try:
        for line in existing.splitlines():
            if "Risk/trade" in line and "$" in line:
                risk_str = line.split("$")[1].split(" ")[0].replace(",", "")
                risk_per_trade = float(risk_str)
                if risk_per_trade > 0:
                    r_multiple = round(event.pnl_dollars / risk_per_trade, 2)
                break
    except Exception:
        pass

    exit_section = f"""
## Exit *(updated {event.exit_date})*
| Field | Value |
|-------|-------|
| Exit price | ${event.exit_price:.2f} |
| Qty | {event.exit_qty} |
| Exit reason | {event.exit_reason} |
| Exit date | {event.exit_date} |

## P&L *(updated {event.exit_date})*
| Metric | Value |
|--------|-------|
| P&L ($) | ${event.pnl_dollars:+,.2f} |
| P&L (%) | {event.pnl_pct:+.2f}% |
| R multiple | {r_multiple:+.2f}R |
| Outcome | **{outcome}** |
"""

    # Replace *Pending* sections with real data
    updated = existing.replace(
        "## Exit\n*Pending — position open.*",
        exit_section.strip(),
    ).replace(
        "## P&L\n*Pending — position open.*",
        "",  # removed — now part of exit_section
    )

    path.write_text(updated, encoding="utf-8")
    log.info("Journal exit written: %s (%s %+.2f)", path, outcome, event.pnl_dollars)
    return path


def get_recent_journals(n: int = 5, journal_dir: Path | None = None) -> list[str]:
    """Return the text of the n most recent completed trade journals."""
    search_dir = journal_dir or _JOURNAL_DIR
    files = sorted(search_dir.glob("*.md"), reverse=True)
    results = []
    for f in files:
        if len(results) >= n:
            break
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue   # skip files with encoding issues (legacy or corrupt)
        if "Exit *(updated" in text:   # only completed trades
            results.append(text)
    return results


def purge_old_journals():
    """Delete journal files older than _RETENTION_DAYS."""
    cutoff = date.today().toordinal() - _RETENTION_DAYS
    for f in _JOURNAL_DIR.glob("*.md"):
        try:
            file_date = date.fromisoformat(f.stem.split("_")[0])
            if file_date.toordinal() < cutoff:
                f.unlink()
                log.info("Purged old journal: %s", f.name)
        except Exception:
            pass
