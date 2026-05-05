"""
Watchlist definition. Edit EQUITIES and CRYPTO freely.
Each equity entry carries a GICS sector tag used by the sector-exposure risk rule.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticker:
    symbol: str
    sector: str          # GICS sector string
    asset_class: str     # "equity" | "crypto"
    description: str = ""


# ------------------------------------------------------------------
# Equity watchlist — liquid, options-available names
# ------------------------------------------------------------------
EQUITIES: list[Ticker] = [
    # ETFs / broad market
    Ticker("SPY",  "ETF",          "equity", "S&P 500 ETF"),
    Ticker("QQQ",  "ETF",          "equity", "Nasdaq 100 ETF"),
    Ticker("IWM",  "ETF",          "equity", "Russell 2000 ETF"),
    # Technology
    Ticker("AAPL", "Technology",   "equity", "Apple"),
    Ticker("MSFT", "Technology",   "equity", "Microsoft"),
    Ticker("NVDA", "Technology",   "equity", "Nvidia"),
    Ticker("AMD",  "Technology",   "equity", "Advanced Micro Devices"),
    Ticker("META", "Technology",   "equity", "Meta Platforms"),
    Ticker("GOOGL","Technology",   "equity", "Alphabet"),
    Ticker("AMZN", "ConsumerDisc", "equity", "Amazon"),
    Ticker("TSLA", "ConsumerDisc", "equity", "Tesla"),
    # Financials
    Ticker("JPM",  "Financials",   "equity", "JPMorgan Chase"),
    Ticker("GS",   "Financials",   "equity", "Goldman Sachs"),
    # Energy
    Ticker("XOM",  "Energy",       "equity", "ExxonMobil"),
    # Healthcare
    Ticker("UNH",  "Healthcare",   "equity", "UnitedHealth"),
    Ticker("LLY",  "Healthcare",   "equity", "Eli Lilly"),
]

# ------------------------------------------------------------------
# Crypto watchlist — traded via ccxt (Phase 3+) or Alpaca crypto
# ------------------------------------------------------------------
CRYPTO: list[Ticker] = [
    Ticker("BTC/USD", "Crypto", "crypto", "Bitcoin"),
    Ticker("ETH/USD", "Crypto", "crypto", "Ethereum"),
    Ticker("SOL/USD", "Crypto", "crypto", "Solana"),
]

# ------------------------------------------------------------------
# Convenience accessors
# ------------------------------------------------------------------
ALL_TICKERS: list[Ticker] = EQUITIES + CRYPTO

EQUITY_SYMBOLS: list[str] = [t.symbol for t in EQUITIES]
CRYPTO_SYMBOLS: list[str] = [t.symbol for t in CRYPTO]

SECTOR_MAP: dict[str, str] = {t.symbol: t.sector for t in ALL_TICKERS}


def get_ticker(symbol: str) -> Ticker | None:
    for t in ALL_TICKERS:
        if t.symbol == symbol.upper():
            return t
    return None


def symbols_by_sector(sector: str) -> list[str]:
    return [t.symbol for t in ALL_TICKERS if t.sector == sector]
