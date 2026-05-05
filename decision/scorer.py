"""
Scorer — combines technical signal dict + Thesis into a numeric score [-10, +10].

Higher = stronger buy candidate. Negative = bearish / avoid.
The score feeds the rules filter and the proposer's ranking.

Scoring breakdown (max raw points before clamp):
  Technical : +7 / -7
  Thesis    : +5 / -5  (sentiment × conviction weight + alignment bonus)
  Total     : clamped to [-10, +10]
"""

from dataclasses import dataclass
from research.thesis import Thesis


@dataclass
class ScoreDetail:
    symbol: str
    total: float            # clamped [-10, +10]
    technical: float        # raw technical sub-score
    thesis_score: float     # raw thesis sub-score
    components: dict        # breakdown for logging / audit


def score_ticker(signal: dict, thesis: Thesis) -> ScoreDetail:
    """
    Score a single ticker given its latest signal summary and thesis.
    Returns a ScoreDetail with the total score and a component breakdown.
    """
    components: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Technical sub-score  (range roughly -7 to +7)
    # ------------------------------------------------------------------
    # Trend alignment — weighted more heavily because it determines direction
    components["above_sma200"]  = 2.0  if signal.get("above_sma200") else -2.0
    components["above_sma50"]   = 1.5  if signal.get("above_sma50")  else -1.5
    components["above_sma20"]   = 1.0  if signal.get("above_sma20")  else -1.0

    # Momentum
    components["macd_bullish"]  = 1.0  if signal.get("macd_bullish") else -1.0

    # RSI zone
    rsi = signal.get("rsi") or 50.0
    if rsi < 30:
        components["rsi"] =  1.5   # oversold = mean-reversion opportunity
    elif rsi > 75:
        components["rsi"] = -2.0   # overbought = chasing risk
    elif rsi > 65:
        components["rsi"] = -0.5   # getting stretched
    else:
        components["rsi"] =  0.0   # neutral zone

    # Volume confirmation
    vol_ratio = signal.get("vol_ratio") or 1.0
    if vol_ratio >= 2.0:
        components["volume"] =  1.0
    elif vol_ratio >= 1.5:
        components["volume"] =  0.5
    elif vol_ratio < 0.7:
        components["volume"] = -0.5
    else:
        components["volume"] =  0.0

    technical = sum(v for k, v in components.items())

    # ------------------------------------------------------------------
    # Thesis sub-score  (range roughly -5 to +5)
    # ------------------------------------------------------------------
    # Conviction weight: 1→0.5, 2→1.0, 3→1.5, 4→2.0, 5→2.5
    conviction_weight = thesis.conviction * 0.5

    if thesis.sentiment == "bullish":
        components["thesis_sentiment"] = conviction_weight
    elif thesis.sentiment == "bearish":
        components["thesis_sentiment"] = -conviction_weight
    else:
        components["thesis_sentiment"] = 0.0

    # Technical alignment bonus
    components["technical_alignment"] = 0.5 if thesis.technical_alignment else -0.5

    # Earnings risk penalty — elevated uncertainty
    components["earnings_risk"] = -1.5 if thesis.earnings_risk else 0.0

    thesis_score = sum(
        v for k, v in components.items() if k in (
            "thesis_sentiment", "technical_alignment", "earnings_risk"
        )
    )

    total = max(-10.0, min(10.0, technical + thesis_score))

    return ScoreDetail(
        symbol=thesis.symbol,
        total=round(total, 2),
        technical=round(technical, 2),
        thesis_score=round(thesis_score, 2),
        components=components,
    )


def rank_candidates(
    signals: dict[str, dict],
    theses: dict[str, Thesis],
) -> list[ScoreDetail]:
    """
    Score all tickers that have both a signal and a thesis.
    Returns list sorted by score descending.
    """
    results = []
    for sym in signals:
        if sym not in theses:
            continue
        detail = score_ticker(signals[sym], theses[sym])
        results.append(detail)
    return sorted(results, key=lambda d: d.total, reverse=True)
