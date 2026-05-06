"""
Market data fetcher with parquet caching.

Usage:
    from data.fetcher import fetch_bars
    df = fetch_bars(["SPY", "AAPL"], days=60)          # equity daily bars
    df = fetch_bars(["SPY"], days=5, timeframe="1Hour") # intraday
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger(__name__)

_CFG_PATH = Path(__file__).parent.parent / "config.yaml"
with open(_CFG_PATH) as _f:
    _CFG = yaml.safe_load(_f)

_CACHE_DIR = Path(__file__).parent.parent / _CFG["data"]["cache_dir"]
_CACHE_TTL = timedelta(minutes=_CFG["data"]["cache_ttl_minutes"])

_TIMEFRAME_MAP = {
    "1Day":   TimeFrame.Day,
    "1Hour":  TimeFrame.Hour,
    "1Min":   TimeFrame.Minute,
    "5Min":   TimeFrame(5, TimeFrameUnit.Minute),
    "15Min":  TimeFrame(15, TimeFrameUnit.Minute),
}


def _get_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )


def _cache_path(symbol: str, timeframe: str, days: int) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{symbol}_{timeframe}_{days}d.parquet"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age < _CACHE_TTL


def _fetch_from_api(
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    client = _get_client()
    tf = _TIMEFRAME_MAP.get(timeframe, TimeFrame.Day)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=tf,
        start=start,
        end=end,
        adjustment="all",   # split + dividend adjusted
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return df
    # Flatten MultiIndex → symbol column, reset index
    df = df.reset_index()
    df = df.rename(columns={"timestamp": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_convert("America/New_York")
    return df


def fetch_bars(
    symbols: str | list[str],
    days: int = 300,
    timeframe: Literal["1Day", "1Hour", "5Min", "15Min", "1Min"] = "1Day",
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV bars for one or more symbols.

    Returns a dict {symbol: DataFrame} where each DataFrame has columns:
        date, open, high, low, close, volume, vwap, trade_count

    Caches per-symbol to parquet. Cache is invalidated after config cache_ttl_minutes
    or when force_refresh=True.
    """
    if isinstance(symbols, str):
        symbols = [symbols]

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days + 5)  # pad for weekends/holidays

    result: dict[str, pd.DataFrame] = {}
    to_fetch: list[str] = []

    for sym in symbols:
        path = _cache_path(sym, timeframe, days)
        if not force_refresh and _is_cache_fresh(path):
            log.debug("Cache hit: %s", path.name)
            result[sym] = pd.read_parquet(path)
        else:
            to_fetch.append(sym)

    if to_fetch:
        log.info("Fetching %d symbol(s) from Alpaca: %s", len(to_fetch), to_fetch)
        raw = _fetch_from_api(to_fetch, timeframe, start, end)

        if raw.empty:
            log.warning("No data returned for %s", to_fetch)
            for sym in to_fetch:
                result[sym] = pd.DataFrame()
        else:
            for sym in to_fetch:
                sym_df = raw[raw["symbol"] == sym].drop(columns=["symbol"]).copy()
                sym_df = sym_df.sort_values("date").reset_index(drop=True)
                # Trim to requested day count
                sym_df = sym_df.tail(days).reset_index(drop=True)

                path = _cache_path(sym, timeframe, days)
                sym_df.to_parquet(path, index=False)
                log.debug("Cached %d bars for %s → %s", len(sym_df), sym, path.name)
                result[sym] = sym_df

    return result


def fetch_bars_single(
    symbol: str,
    days: int = 300,
    timeframe: str = "1Day",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Convenience wrapper returning a single DataFrame."""
    result = fetch_bars(symbol, days=days, timeframe=timeframe, force_refresh=force_refresh)
    return result.get(symbol, pd.DataFrame())


def invalidate_cache(symbol: str | None = None, timeframe: str = "1Day", days: int = 60):
    """Delete cached parquet files. Pass symbol=None to clear all."""
    if symbol:
        path = _cache_path(symbol, timeframe, days)
        if path.exists():
            path.unlink()
            log.info("Invalidated cache for %s", symbol)
    else:
        for f in _CACHE_DIR.glob("*.parquet"):
            f.unlink()
        log.info("Cleared all parquet cache files")
