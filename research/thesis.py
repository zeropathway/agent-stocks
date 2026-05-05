"""
Thesis generator — Claude Sonnet reads headlines + earnings + technicals
and returns a structured Thesis per ticker.

Uses tool_use (structured output) so the schema is always enforced.
Caches each thesis to data/cache/thesis_{symbol}_{date}.json for 30 min.

Usage:
    from research.thesis import generate_thesis
    thesis = generate_thesis("AAPL", signal, headlines, earnings_info)
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Optional, Literal

import anthropic
from dotenv import load_dotenv

from research.news import Headline, headlines_to_text
from research.earnings import EarningsInfo, earnings_to_text

load_dotenv()

log = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
_CACHE_TTL = timedelta(minutes=30)
_MODEL = "claude-opus-4-7"


# ------------------------------------------------------------------
# Thesis schema (Pydantic-free — plain dataclass + JSON tool schema)
# ------------------------------------------------------------------

@dataclass
class Thesis:
    symbol: str
    sentiment: Literal["bullish", "bearish", "neutral"]
    conviction: int                     # 1 (low) – 5 (high)
    catalyst: str                       # primary driver (1–2 sentences)
    risk_factors: list[str]             # 2–4 key risks
    technical_alignment: bool           # does chart support the thesis?
    earnings_risk: bool                 # earnings within 7 days?
    summary: str                        # 3–4 sentence executive summary
    generated_at: str = ""             # ISO timestamp

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Thesis":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_THESIS_TOOL = {
    "name": "submit_thesis",
    "description": "Submit a structured investment thesis for a given ticker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment": {
                "type": "string",
                "enum": ["bullish", "bearish", "neutral"],
                "description": "Overall directional bias based on news, earnings, and technicals.",
            },
            "conviction": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Confidence level: 1=very low, 5=very high.",
            },
            "catalyst": {
                "type": "string",
                "description": "Primary catalyst or thesis driver in 1–2 sentences.",
            },
            "risk_factors": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": "Key risks that could invalidate the thesis.",
            },
            "technical_alignment": {
                "type": "boolean",
                "description": "True if chart technicals support the thesis direction.",
            },
            "earnings_risk": {
                "type": "boolean",
                "description": "True if earnings are within 7 calendar days.",
            },
            "summary": {
                "type": "string",
                "description": "3–4 sentence executive summary integrating news, earnings, and technicals.",
            },
        },
        "required": [
            "sentiment", "conviction", "catalyst",
            "risk_factors", "technical_alignment", "earnings_risk", "summary",
        ],
    },
}

_SYSTEM_PROMPT = """\
You are a senior equity analyst at a quantitative hedge fund.
You receive real-time market data, recent news headlines, and earnings information for a stock.
Your job is to produce a concise, structured investment thesis using the submit_thesis tool.

Guidelines:
- Be direct and specific. Avoid vague phrases like "could potentially".
- Conviction 4–5 requires a clear near-term catalyst with technical confirmation.
- Conviction 1–2 for conflicting signals, overbought conditions, or no catalyst.
- Flag earnings_risk=true whenever earnings are within 7 days.
- technical_alignment=true only if price is above key SMAs AND MACD is bullish.
- Keep risk_factors actionable (e.g., "Fed meeting in 3 days could reverse momentum").
"""


# ------------------------------------------------------------------
# Cache helpers
# ------------------------------------------------------------------

def _cache_path(symbol: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"thesis_{symbol}_{date.today().isoformat()}.json"


def _load_cached(symbol: str) -> Optional[Thesis]:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    age = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    if age > _CACHE_TTL:
        return None
    try:
        return Thesis.from_dict(json.loads(path.read_text()))
    except Exception:
        return None


def _save_cached(thesis: Thesis):
    _cache_path(thesis.symbol).write_text(json.dumps(thesis.to_dict(), indent=2))


# ------------------------------------------------------------------
# Thesis generation
# ------------------------------------------------------------------

def generate_thesis(
    symbol: str,
    signal: dict,
    headlines: list[Headline],
    earnings: EarningsInfo,
    force_refresh: bool = False,
) -> Thesis:
    """
    Generate a Thesis for `symbol` using Claude Sonnet.
    Returns cached thesis if available and fresh.
    Falls back to a rule-based thesis if ANTHROPIC_API_KEY is missing.
    """
    if not force_refresh:
        cached = _load_cached(symbol)
        if cached:
            log.debug("Thesis cache hit for %s", symbol)
            return cached

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — using rule-based fallback thesis for %s", symbol)
        return _rule_based_thesis(symbol, signal, earnings)

    user_prompt = _build_prompt(symbol, signal, headlines, earnings)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # cache system prompt
                }
            ],
            tools=[_THESIS_TOOL],
            tool_choice={"type": "tool", "name": "submit_thesis"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract tool call result
        tool_use = next(
            (b for b in response.content if b.type == "tool_use" and b.name == "submit_thesis"),
            None,
        )
        if not tool_use:
            log.error("Claude did not call submit_thesis for %s", symbol)
            return _rule_based_thesis(symbol, signal, earnings)

        data = tool_use.input
        thesis = Thesis(
            symbol=symbol,
            sentiment=data["sentiment"],
            conviction=data["conviction"],
            catalyst=data["catalyst"],
            risk_factors=data["risk_factors"],
            technical_alignment=data["technical_alignment"],
            earnings_risk=data["earnings_risk"],
            summary=data["summary"],
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        _save_cached(thesis)
        log.info(
            "Thesis for %s: %s conviction=%d — %s",
            symbol, thesis.sentiment, thesis.conviction, thesis.catalyst[:80],
        )
        return thesis

    except Exception as e:
        log.error("Claude thesis generation failed for %s: %s", symbol, e)
        return _rule_based_thesis(symbol, signal, earnings)


def _build_prompt(
    symbol: str,
    signal: dict,
    headlines: list[Headline],
    earnings: EarningsInfo,
) -> str:
    tech = f"""Technical snapshot for {symbol}:
- Price: ${signal.get('close', 'N/A')}
- RSI(14): {signal.get('rsi', 'N/A'):.1f} [{signal.get('rsi_zone', 'N/A')}]
- Above SMA20: {signal.get('above_sma20')} | SMA50: {signal.get('above_sma50')} | SMA200: {signal.get('above_sma200')}
- MACD bullish: {signal.get('macd_bullish')} | MACD histogram: {signal.get('macd_hist', 0):.3f}
- ATR(14): ${signal.get('atr', 0):.2f}
- Volume ratio vs 20d avg: {signal.get('vol_ratio', 1):.2f}x
- Bollinger %B: {signal.get('bb_pct', 'N/A')}
- Composite bias: {signal.get('bias')} ({signal.get('bullish_signals', 0)} bull / {signal.get('bearish_signals', 0)} bear signals)"""

    return f"{tech}\n\n{earnings_to_text(earnings)}\n\n{headlines_to_text(headlines, symbol)}"


def _rule_based_thesis(symbol: str, signal: dict, earnings: EarningsInfo) -> Thesis:
    """Fallback: derive thesis from signal dict alone (no LLM)."""
    bias = signal.get("bias", "neutral")
    sentiment: Literal["bullish", "bearish", "neutral"] = (
        "bullish" if bias == "bullish" else
        "bearish" if bias == "bearish" else
        "neutral"
    )
    bull = signal.get("bullish_signals", 0)
    bear = signal.get("bearish_signals", 0)
    conviction = min(5, max(1, round((bull - bear) / 2 + 3) if sentiment != "neutral" else 2))

    tech_ok = bool(signal.get("above_sma50") and signal.get("macd_bullish"))
    rsi = signal.get("rsi", 50) or 50

    risk_factors = ["No LLM analysis — rule-based fallback only"]
    if signal.get("rsi_zone") == "overbought":
        risk_factors.append(f"RSI overbought at {rsi:.0f}")
    if earnings.is_earnings_week:
        risk_factors.append("Earnings within 7 days — elevated vol risk")

    return Thesis(
        symbol=symbol,
        sentiment=sentiment,
        conviction=conviction,
        catalyst=f"Technical bias is {bias} with {bull} bullish vs {bear} bearish signals.",
        risk_factors=risk_factors,
        technical_alignment=tech_ok,
        earnings_risk=earnings.is_earnings_week,
        summary=(
            f"{symbol} shows a {bias} technical setup. "
            f"RSI is {rsi:.0f} ({signal.get('rsi_zone', 'neutral')}). "
            f"{'Above' if signal.get('above_sma200') else 'Below'} the 200-day SMA. "
            f"Rule-based thesis — ANTHROPIC_API_KEY not configured."
        ),
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
    )
