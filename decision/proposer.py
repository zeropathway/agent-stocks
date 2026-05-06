"""
Proposer — generates proposed_trade.json for approved candidates.

Escalation:
  conviction == 5  →  Claude Opus 4.7  (highest stakes)
  conviction <= 4  →  Claude Sonnet 4.6 (routine)

Uses tool_use to guarantee the JSON schema. Saves to proposed_trade.json
at repo root. The execution layer (Phase 5) reads this file.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

import anthropic
from dotenv import load_dotenv

from decision.scorer import ScoreDetail
from research.thesis import Thesis

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger(__name__)

_MODEL_SONNET = "claude-sonnet-4-6"
_MODEL_OPUS   = "claude-opus-4-7"
# All trade proposals use Opus — this is the highest-stakes call in the pipeline
_OPUS_CONVICTION_THRESHOLD = 1

_TRADE_FILE = Path(__file__).parent.parent / "proposed_trade.json"


# ------------------------------------------------------------------
# Proposed trade schema
# ------------------------------------------------------------------

@dataclass
class ProposedTrade:
    symbol: str
    action: Literal["buy", "hold", "skip"]
    qty: int
    entry_price: float
    stop_price: float
    target_price: float
    risk_reward: float          # (target - entry) / (entry - stop)
    rationale: str              # 2–3 sentences
    score: float
    conviction: int
    thesis_sentiment: str
    model_used: str
    generated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProposedTrade":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_PROPOSE_TOOL = {
    "name": "submit_trade_proposal",
    "description": "Submit a structured trade proposal for execution review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["buy", "hold", "skip"],
                "description": "'buy' to enter, 'hold' if already owned, 'skip' if risk outweighs reward.",
            },
            "qty": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of shares to buy (0 if action is hold/skip).",
            },
            "entry_price": {
                "type": "number",
                "description": "Suggested limit entry price (use latest ask if market order is fine).",
            },
            "stop_price": {
                "type": "number",
                "description": "Hard stop-loss price (below key support or ATR-based).",
            },
            "target_price": {
                "type": "number",
                "description": "Profit target price (next resistance or 2× risk minimum).",
            },
            "rationale": {
                "type": "string",
                "description": "2–3 sentences explaining entry, stop, and target logic.",
            },
        },
        "required": ["action", "qty", "entry_price", "stop_price", "target_price", "rationale"],
    },
}

_SYSTEM_PROMPT = """\
You are a risk-conscious quantitative trader at a hedge fund operating with strict risk rules.
Your job is to produce a precise, executable trade proposal using the submit_trade_proposal tool.

Rules you MUST follow:
1. Stop must be below entry — never suggest a stop above the entry price.
2. Target must be above entry for buys.
3. Risk/reward must be at least 1.5:1 — if you cannot find a 1.5R setup, use action=skip.
4. Size conservatively — do not suggest qty that would exceed 1% of $100k equity per ATR move.
5. Use support/resistance, SMA levels, and ATR to anchor stop and target.
6. If earnings are within 7 days, suggest action=skip unless conviction=5.
"""


def _build_prompt(
    symbol: str,
    signal: dict,
    thesis: Thesis,
    detail: ScoreDetail,
    atr: float,
    equity: float,
) -> str:
    max_risk_dollars = equity * 0.01
    atr_stop = atr * 2.0
    max_qty = int(max_risk_dollars / atr_stop) if atr_stop > 0 else 0

    return f"""Trade proposal request for {symbol}

SCORE: {detail.total}/10  (technical={detail.technical}, thesis={detail.thesis_score})
CONVICTION: {thesis.conviction}/5
THESIS SENTIMENT: {thesis.sentiment}
CATALYST: {thesis.catalyst}
RISKS: {'; '.join(thesis.risk_factors)}
EARNINGS RISK: {thesis.earnings_risk}

TECHNICALS:
  Price: ${signal['close']:.2f}
  ATR(14): ${atr:.2f}
  RSI: {signal.get('rsi', 0):.1f} [{signal.get('rsi_zone')}]
  SMA20: ${signal.get('sma_20', 0):.2f}  SMA50: ${signal.get('sma_50', 0):.2f}  SMA200: ${signal.get('sma_200', 0):.2f}
  MACD bullish: {signal.get('macd_bullish')}  | hist: {signal.get('macd_hist', 0):.3f}
  BB %B: {signal.get('bb_pct', 0.5):.2f}
  Volume ratio: {signal.get('vol_ratio', 1):.2f}x

RISK BUDGET:
  Equity: ${equity:,.0f}
  Max risk/trade: ${max_risk_dollars:,.0f} (1%)
  ATR stop distance: ${atr_stop:.2f}
  Max qty by ATR sizing: {max_qty} shares

Propose an entry, stop, and target. Stop must be below ${signal['close']:.2f}. Target must be at least 1.5× the stop distance above entry.
"""


def propose_trade(
    symbol: str,
    signal: dict,
    thesis: Thesis,
    detail: ScoreDetail,
    equity: float,
) -> Optional[ProposedTrade]:
    """
    Generate a trade proposal via Claude.
    Uses Opus for conviction=5, Sonnet otherwise.
    Returns None if Claude suggests skip or API fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot generate trade proposal")
        return None

    atr = signal.get("atr") or 1.0
    model = _MODEL_OPUS if thesis.conviction >= _OPUS_CONVICTION_THRESHOLD else _MODEL_SONNET
    log.info("Generating trade proposal for %s using %s", symbol, model)

    prompt = _build_prompt(symbol, signal, thesis, detail, atr, equity)
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=[_PROPOSE_TOOL],
            tool_choice={"type": "tool", "name": "submit_trade_proposal"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_use = next(
            (b for b in response.content if b.type == "tool_use" and b.name == "submit_trade_proposal"),
            None,
        )
        if not tool_use:
            log.error("Claude did not call submit_trade_proposal for %s", symbol)
            return None

        data = tool_use.input
        action = data["action"]

        if action in ("hold", "skip"):
            log.info("Claude recommends %s for %s: %s", action, symbol, data["rationale"][:80])
            return None

        entry  = float(data["entry_price"])
        stop   = float(data["stop_price"])
        target = float(data["target_price"])

        # Validate geometry — reject bad proposals
        if stop >= entry:
            log.warning("Bad proposal for %s: stop $%.2f >= entry $%.2f — rejecting", symbol, stop, entry)
            return None
        if target <= entry:
            log.warning("Bad proposal for %s: target $%.2f <= entry $%.2f — rejecting", symbol, target, entry)
            return None

        risk   = entry - stop
        reward = target - entry
        rr     = round(reward / risk, 2) if risk > 0 else 0

        if rr < 1.5:
            log.warning("R/R %.2f < 1.5 for %s — rejecting proposal", rr, symbol)
            return None

        trade = ProposedTrade(
            symbol=symbol,
            action="buy",
            qty=data["qty"],
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            risk_reward=rr,
            rationale=data["rationale"],
            score=detail.total,
            conviction=thesis.conviction,
            thesis_sentiment=thesis.sentiment,
            model_used=model,
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        log.info(
            "Trade proposal: %s %s qty=%d entry=$%.2f stop=$%.2f target=$%.2f R/R=%.1f",
            action, symbol, trade.qty, entry, stop, target, rr,
        )
        return trade

    except Exception as e:
        log.error("Proposer failed for %s: %s", symbol, e)
        return None


def save_proposal(trade: ProposedTrade):
    _TRADE_FILE.write_text(json.dumps(trade.to_dict(), indent=2))
    log.info("Saved proposed_trade.json → %s", _TRADE_FILE)


def load_proposal() -> Optional[ProposedTrade]:
    if not _TRADE_FILE.exists():
        return None
    try:
        return ProposedTrade.from_dict(json.loads(_TRADE_FILE.read_text()))
    except Exception as e:
        log.error("Failed to load proposed_trade.json: %s", e)
        return None


def run_decision_pass(
    ranked: list,          # list[ScoreDetail]
    approved: list,        # list[tuple[ScoreDetail, list[RuleResult]]]
    signals: dict,
    theses: dict,
    equity: float,
) -> Optional[ProposedTrade]:
    """
    Take the top approved candidate and generate a trade proposal.
    Returns the ProposedTrade or None if nothing passes.
    """
    if not approved:
        log.info("No candidates passed all rules — no trade proposed")
        return None

    top_detail, rule_results = approved[0]
    sym = top_detail.symbol

    log.info(
        "Top candidate: %s score=%.1f conviction=%d",
        sym, top_detail.total, theses[sym].conviction,
    )

    trade = propose_trade(sym, signals[sym], theses[sym], top_detail, equity)
    if trade:
        save_proposal(trade)
    return trade
