"""
Market context updater — Claude Sonnet rewrites context/market_context.md each EOD.

Input: signals dict (all tickers), theses dict (researched subset), session P&L summary.
Output: structured market regime document the agent reads at the start of each session.
"""

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger(__name__)

_MODEL        = "claude-opus-4-7"
_CONTEXT_FILE = Path(__file__).parent / "market_context.md"

_SYSTEM_PROMPT = """\
You are a senior macro analyst writing a daily market context briefing for an algorithmic trader.
The briefing will be read at the start of tomorrow's pre-market session to calibrate the agent's bias.

Write a structured markdown document with these exact sections:
1. **Regime** — risk-on / risk-off / mixed, and why (2 sentences)
2. **Leading sectors** — which sectors are strongest technically (bullet list)
3. **Lagging sectors** — which sectors are weakest (bullet list)
4. **Key risks** — 2–4 specific near-term risks (earnings, macro events, technicals)
5. **Watchlist notes** — 3–5 specific ticker observations from today's scan
6. **Bias for tomorrow** — bullish / bearish / neutral with a one-sentence rationale

Be specific. Cite ticker names, price levels, and indicator values where relevant.
No filler text. This document must fit in one screen.
"""


def _build_prompt(
    signals: dict,
    theses: dict,
    session_pnl: float = 0.0,
    lessons_snippet: str = "",
) -> str:
    today = date.today().isoformat()

    # Market breadth
    bullish = [s for s, sig in signals.items() if sig.get("bias") == "bullish"]
    bearish = [s for s, sig in signals.items() if sig.get("bias") == "bearish"]
    neutral = [s for s, sig in signals.items() if sig.get("bias") == "neutral"]

    breadth = (
        f"Market breadth ({today}): "
        f"{len(bullish)} bullish, {len(bearish)} bearish, {len(neutral)} neutral "
        f"out of {len(signals)} scanned.\n"
        f"Bullish: {', '.join(bullish)}\n"
        f"Bearish: {', '.join(bearish)}\n"
    )

    # Thesis summaries
    thesis_lines = []
    for sym, t in theses.items():
        thesis_lines.append(
            f"- {sym}: {t.sentiment} conviction={t.conviction} | {t.catalyst[:100]}"
        )
    thesis_text = "Researched tickers:\n" + "\n".join(thesis_lines) if thesis_lines else ""

    # Signal snapshots
    snap_lines = []
    for sym, sig in signals.items():
        snap_lines.append(
            f"- {sym}: close=${sig.get('close', 0):.2f} RSI={sig.get('rsi', 0):.0f} "
            f"above200={sig.get('above_sma200')} bias={sig.get('bias')}"
        )
    snap_text = "Signal snapshot:\n" + "\n".join(snap_lines)

    pnl_text = f"Session P&L: ${session_pnl:+,.2f}" if session_pnl != 0 else "Session P&L: no trades today"

    lessons_text = f"\nRecent lessons:\n{lessons_snippet}" if lessons_snippet else ""

    return (
        f"{breadth}\n{thesis_text}\n\n{snap_text}\n\n{pnl_text}{lessons_text}\n\n"
        "Please write tomorrow's market context briefing."
    )


def update_context(
    signals: dict,
    theses: dict,
    session_pnl: float = 0.0,
    lessons_snippet: str = "",
) -> str:
    """
    Rewrite market_context.md using Claude Sonnet.
    Returns the new context text, or empty string on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping context update")
        return ""

    if not signals:
        log.warning("No signals provided — skipping context update")
        return ""

    prompt = _build_prompt(signals, theses, session_pnl, lessons_snippet)
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=1200,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        context_text = response.content[0].text.strip()
    except Exception as e:
        log.error("Claude context update failed: %s", e)
        return ""

    header = (
        f"# Market Context\n\n"
        f"> Last updated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"by context/updater.py\n\n"
    )
    full = header + context_text + "\n"

    _CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONTEXT_FILE.write_text(full, encoding="utf-8")
    log.info("Market context updated: %s (%d chars)", _CONTEXT_FILE, len(full))
    return context_text
