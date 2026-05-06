"""
Historical OHLCV data via yfinance — up to 25 years of free daily bars.
Caches to data/cache/bt_{symbol}.parquet (refreshed once per day).

yfinance is the best free source: Alpaca free tier gives ~5 years,
Polygon free tier gives ~2 years. yfinance gives ~25 years.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
_CACHE_TTL = timedelta(hours=20)  # re-fetch once per trading day


def fetch_history(
    symbol: str,
    years: int = 20,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch up to `years` years of daily adjusted OHLCV for `symbol` via yfinance.

    Returns DataFrame with columns: date, open, high, low, close, volume.
    Dates are tz-naive (local market time). Split + dividend adjusted.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"bt_{symbol}.parquet"

    if not force_refresh and cache_path.exists():
        age = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(
            cache_path.stat().st_mtime, tz=timezone.utc
        )
        if age < _CACHE_TTL:
            df = pd.read_parquet(cache_path)
            log.debug("Historical cache hit: %s (%d bars)", symbol, len(df))
            return df

    start = (datetime.now() - timedelta(days=years * 365 + 30)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    log.info("Downloading %s from yfinance (%d years) ...", symbol, years)
    try:
        raw = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)
    except Exception as e:
        log.error("yfinance download failed for %s: %s", symbol, e)
        return pd.DataFrame()

    if raw.empty:
        log.warning("No data returned for %s", symbol)
        return pd.DataFrame()

    df = raw.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)

    df.to_parquet(cache_path, index=False)
    log.info("Fetched %d bars for %s (%.1f years)", len(df), symbol, len(df) / 252)
    return df
