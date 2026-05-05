"""
Hard filter pipeline — all rules must pass before a trade is proposed.
Each rule returns (passed: bool, reason: str).
Rules are applied in order; first failure short-circuits.

These are CODE rules, not prompt rules. The proposer never sees a ticker
that fails here.
"""

from dataclasses import dataclass
from typing import Callable

from data.universe import SECTOR_MAP
from decision.scorer import ScoreDetail
from research.thesis import Thesis


@dataclass
class RuleResult:
    passed: bool
    rule: str
    reason: str


# Minimum score to even consider a trade
_MIN_SCORE_BUY  =  5.0
_MIN_SCORE_SELL = -5.0   # reserved for future short support

# RSI hard ceiling — never buy into extreme overbought
_RSI_OVERBOUGHT_HARD = 80.0

# Earnings blackout window (days)
_EARNINGS_BLACKOUT_DAYS = 3


def _rule_min_score(detail: ScoreDetail, **_) -> RuleResult:
    ok = detail.total >= _MIN_SCORE_BUY
    return RuleResult(ok, "min_score", f"score={detail.total} (need >={_MIN_SCORE_BUY})")


def _rule_trend_alignment(detail: ScoreDetail, signal: dict, **_) -> RuleResult:
    above200 = signal.get("above_sma200")
    ok = above200 is True
    return RuleResult(ok, "trend_alignment", f"above_sma200={above200} — only buy above 200d SMA")


def _rule_rsi_cap(detail: ScoreDetail, signal: dict, **_) -> RuleResult:
    rsi = signal.get("rsi") or 50.0
    ok = rsi <= _RSI_OVERBOUGHT_HARD
    return RuleResult(ok, "rsi_cap", f"RSI={rsi:.1f} (hard ceiling={_RSI_OVERBOUGHT_HARD})")


def _rule_earnings_blackout(detail: ScoreDetail, thesis: Thesis, **_) -> RuleResult:
    # thesis.earnings_risk is set when earnings within 7 days;
    # we use the stricter 3-day blackout here
    from research.earnings import fetch_earnings_info
    info = fetch_earnings_info(detail.symbol)
    days = info.days_until_earnings
    if days is not None and 0 <= days <= _EARNINGS_BLACKOUT_DAYS:
        return RuleResult(
            False, "earnings_blackout",
            f"Earnings in {days} days — {_EARNINGS_BLACKOUT_DAYS}-day blackout"
        )
    return RuleResult(True, "earnings_blackout", "No imminent earnings")


def _rule_no_existing_position(
    detail: ScoreDetail, open_positions: list[dict], **_
) -> RuleResult:
    symbols = {p["symbol"] for p in open_positions}
    ok = detail.symbol not in symbols
    return RuleResult(ok, "no_existing_position",
                      f"{detail.symbol} already in portfolio" if not ok else "no existing position")


def _rule_max_positions(detail: ScoreDetail, open_positions: list[dict], max_pos: int = 5, **_) -> RuleResult:
    ok = len(open_positions) < max_pos
    return RuleResult(ok, "max_positions",
                      f"open={len(open_positions)} max={max_pos}")


def _rule_sector_exposure(
    detail: ScoreDetail,
    open_positions: list[dict],
    equity: float,
    max_sector_pct: float = 0.20,
    **_,
) -> RuleResult:
    sector = SECTOR_MAP.get(detail.symbol, "Unknown")
    if sector in ("ETF", "Unknown"):
        return RuleResult(True, "sector_exposure", f"sector={sector} — exempt from sector cap")

    sector_value = sum(
        p["market_value"]
        for p in open_positions
        if SECTOR_MAP.get(p["symbol"], "") == sector
    )
    sector_pct = sector_value / equity if equity > 0 else 0
    ok = sector_pct < max_sector_pct
    return RuleResult(
        ok, "sector_exposure",
        f"{sector} exposure {sector_pct:.1%} (max {max_sector_pct:.0%})"
    )


# Ordered rule pipeline — evaluated top to bottom
_RULES: list[Callable] = [
    _rule_min_score,
    _rule_trend_alignment,
    _rule_rsi_cap,
    _rule_earnings_blackout,
    _rule_no_existing_position,
    _rule_max_positions,
    _rule_sector_exposure,
]


def apply_rules(
    detail: ScoreDetail,
    signal: dict,
    thesis: Thesis,
    open_positions: list[dict],
    equity: float,
) -> tuple[bool, list[RuleResult]]:
    """
    Run all hard rules for a candidate.
    Returns (all_passed, list of RuleResult).
    Short-circuits on first failure for speed.
    """
    results: list[RuleResult] = []
    for rule_fn in _RULES:
        result = rule_fn(
            detail=detail,
            signal=signal,
            thesis=thesis,
            open_positions=open_positions,
            equity=equity,
        )
        results.append(result)
        if not result.passed:
            break
    all_passed = all(r.passed for r in results)
    return all_passed, results


def filter_candidates(
    ranked: list[ScoreDetail],
    signals: dict[str, dict],
    theses: dict[str, Thesis],
    open_positions: list[dict],
    equity: float,
) -> list[tuple[ScoreDetail, list[RuleResult]]]:
    """
    Apply rules to every ranked candidate.
    Returns list of (ScoreDetail, results) for candidates that PASS all rules.
    """
    approved = []
    for detail in ranked:
        sym = detail.symbol
        if sym not in signals or sym not in theses:
            continue
        passed, results = apply_rules(
            detail, signals[sym], theses[sym], open_positions, equity
        )
        if passed:
            approved.append((detail, results))
    return approved
