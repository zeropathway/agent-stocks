"""
Technical indicators computed on a single-symbol OHLCV DataFrame.

All functions accept and return a DataFrame. Indicators are appended as new columns.
Requires: open, high, low, close, volume columns.

Usage:
    from data.fetcher import fetch_bars_single
    from data.indicators import add_all_indicators, get_signal_summary

    df = fetch_bars_single("SPY", days=250)
    df = add_all_indicators(df)
    signal = get_signal_summary(df)
"""

import logging
from pathlib import Path

import pandas as pd
import yaml
import ta
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

log = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).parent.parent / "config.yaml"
with open(_CFG_PATH) as _f:
    _CFG = yaml.safe_load(_f)

_IND = _CFG["indicators"]
_SIZ = _CFG["sizing"]


# ------------------------------------------------------------------
# Individual indicator adders
# ------------------------------------------------------------------

def add_rsi(df: pd.DataFrame, window: int | None = None) -> pd.DataFrame:
    w = window or _IND["rsi_period"]
    df = df.copy()
    df["rsi"] = RSIIndicator(close=df["close"], window=w).rsi()
    return df


def add_sma(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sma_20"]  = SMAIndicator(close=df["close"], window=_IND["sma_fast"]).sma_indicator()
    df["sma_50"]  = SMAIndicator(close=df["close"], window=_IND["sma_mid"]).sma_indicator()
    df["sma_200"] = SMAIndicator(close=df["close"], window=_IND["sma_slow"]).sma_indicator()
    return df


def add_ema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_9"]  = EMAIndicator(close=df["close"], window=9).ema_indicator()
    df["ema_21"] = EMAIndicator(close=df["close"], window=21).ema_indicator()
    return df


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    macd = MACD(
        close=df["close"],
        window_fast=_IND["macd_fast"],
        window_slow=_IND["macd_slow"],
        window_sign=_IND["macd_signal"],
    )
    df["macd"]        = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"]   = macd.macd_diff()
    return df


def add_atr(df: pd.DataFrame, window: int | None = None) -> pd.DataFrame:
    w = window or _SIZ["atr_period"]
    df = df.copy()
    df["atr"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=w
    ).average_true_range()
    return df


def add_bollinger(df: pd.DataFrame, window: int = 20, std: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    bb = BollingerBands(close=df["close"], window=window, window_dev=std)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_mid"]   = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_pct"]   = bb.bollinger_pband()   # 0=lower, 1=upper
    return df


def add_volume_sma(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df = df.copy()
    df["vol_sma_20"] = df["volume"].rolling(window=window).mean()
    df["vol_ratio"]  = df["volume"] / df["vol_sma_20"]  # >1.5 = elevated volume
    return df


# ------------------------------------------------------------------
# All-in-one
# ------------------------------------------------------------------

def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Apply every indicator. Requires at least 200 bars for SMA-200."""
    if len(df) < 30:
        log.warning("Only %d bars — some indicators will be NaN", len(df))
    df = add_rsi(df)
    df = add_sma(df)
    df = add_ema(df)
    df = add_macd(df)
    df = add_atr(df)
    df = add_bollinger(df)
    df = add_volume_sma(df)
    return df


# ------------------------------------------------------------------
# Signal summary — last bar snapshot
# ------------------------------------------------------------------

def get_signal_summary(df: pd.DataFrame) -> dict:
    """
    Derive a structured signal summary from the most recent bar.
    Returns a dict ready to feed into the decision layer (Phase 4).
    """
    if df.empty or "rsi" not in df.columns:
        return {"error": "indicators not computed"}

    row = df.iloc[-1]
    close = float(row["close"])

    def _safe(key: str) -> float | None:
        v = row.get(key)
        return float(v) if pd.notna(v) else None

    # Trend: price vs key SMAs
    above_sma20  = close > _safe("sma_20")  if _safe("sma_20")  else None
    above_sma50  = close > _safe("sma_50")  if _safe("sma_50")  else None
    above_sma200 = close > _safe("sma_200") if _safe("sma_200") else None

    # MACD momentum
    macd_bullish = (
        _safe("macd_hist") is not None
        and _safe("macd_hist") > 0
        and _safe("macd") is not None
        and _safe("macd") > _safe("macd_signal")
    )

    # RSI
    rsi = _safe("rsi")
    rsi_zone = (
        "oversold"  if rsi is not None and rsi < 30 else
        "overbought" if rsi is not None and rsi > 70 else
        "neutral"
    )

    # Volume
    vol_ratio = _safe("vol_ratio")
    elevated_volume = vol_ratio is not None and vol_ratio > 1.5

    # Composite bias
    bullish_signals = sum([
        above_sma20 is True,
        above_sma50 is True,
        above_sma200 is True,
        macd_bullish,
        rsi_zone == "oversold",   # mean-reversion buy signal
        elevated_volume,
    ])
    bearish_signals = sum([
        above_sma20 is False,
        above_sma50 is False,
        above_sma200 is False,
        not macd_bullish,
        rsi_zone == "overbought",
    ])

    bias = "bullish" if bullish_signals > bearish_signals else "bearish" if bearish_signals > bullish_signals else "neutral"

    return {
        "close":          close,
        "atr":            _safe("atr"),
        "rsi":            rsi,
        "rsi_zone":       rsi_zone,
        "macd":           _safe("macd"),
        "macd_signal":    _safe("macd_signal"),
        "macd_hist":      _safe("macd_hist"),
        "macd_bullish":   macd_bullish,
        "sma_20":         _safe("sma_20"),
        "sma_50":         _safe("sma_50"),
        "sma_200":        _safe("sma_200"),
        "above_sma20":    above_sma20,
        "above_sma50":    above_sma50,
        "above_sma200":   above_sma200,
        "bb_pct":         _safe("bb_pct"),
        "vol_ratio":      vol_ratio,
        "elevated_volume":elevated_volume,
        "bullish_signals":bullish_signals,
        "bearish_signals":bearish_signals,
        "bias":           bias,
    }
