"""
News fetcher — Yahoo Finance RSS + Finviz headline scraper.

Usage:
    from research.news import fetch_headlines
    headlines = fetch_headlines("AAPL", max_items=10)
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 10


@dataclass
class Headline:
    source: str
    title: str
    summary: str
    url: str
    published: Optional[datetime] = None


# ------------------------------------------------------------------
# Yahoo Finance RSS
# ------------------------------------------------------------------

def _fetch_yahoo(symbol: str, max_items: int) -> list[Headline]:
    url = f"https://finance.yahoo.com/rss/headline?s={symbol}"
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_items]:
            pub = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            items.append(Headline(
                source="yahoo",
                title=entry.get("title", "").strip(),
                summary=entry.get("summary", "").strip()[:500],
                url=entry.get("link", ""),
                published=pub,
            ))
        log.debug("Yahoo: %d headlines for %s", len(items), symbol)
        return items
    except Exception as e:
        log.warning("Yahoo RSS failed for %s: %s", symbol, e)
        return []


# ------------------------------------------------------------------
# Finviz
# ------------------------------------------------------------------

def _fetch_finviz(symbol: str, max_items: int) -> list[Headline]:
    url = f"https://finviz.com/quote.ashx?t={symbol}&p=d"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Finviz news table has id="news-table"
        table = soup.find("table", id="news-table")
        if not table:
            log.warning("Finviz: news table not found for %s", symbol)
            return []

        items = []
        last_date = None
        for row in table.find_all("tr")[:max_items * 2]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            date_text = cells[0].get_text(strip=True)
            # Finviz alternates full date vs just time for consecutive same-day items
            if len(date_text) > 8:
                last_date = date_text
            time_part = date_text if len(date_text) <= 8 else date_text[-7:]

            link_tag = cells[1].find("a")
            if not link_tag:
                continue

            title = link_tag.get_text(strip=True)
            href = link_tag.get("href", "")

            items.append(Headline(
                source="finviz",
                title=title,
                summary="",
                url=href,
                published=None,
            ))
            if len(items) >= max_items:
                break

        log.debug("Finviz: %d headlines for %s", len(items), symbol)
        return items
    except Exception as e:
        log.warning("Finviz scrape failed for %s: %s", symbol, e)
        return []


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def fetch_headlines(
    symbol: str,
    max_items: int = 10,
    sources: list[str] | None = None,
) -> list[Headline]:
    """
    Fetch headlines from Yahoo Finance RSS and Finviz.
    Returns a deduplicated, merged list sorted newest-first.
    """
    if sources is None:
        sources = ["yahoo", "finviz"]

    results: list[Headline] = []

    if "yahoo" in sources:
        results.extend(_fetch_yahoo(symbol, max_items))
        time.sleep(0.3)   # polite rate limit

    if "finviz" in sources:
        results.extend(_fetch_finviz(symbol, max_items))

    # Deduplicate on title
    seen: set[str] = set()
    unique: list[Headline] = []
    for h in results:
        key = h.title.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)

    # Sort: headlines with a date first (newest), undated at end
    dated   = sorted([h for h in unique if h.published], key=lambda h: h.published, reverse=True)
    undated = [h for h in unique if not h.published]

    return (dated + undated)[:max_items]


def headlines_to_text(headlines: list[Headline], symbol: str) -> str:
    """Format headlines into a compact string for the Claude prompt."""
    if not headlines:
        return f"No recent news found for {symbol}."
    lines = [f"Recent news for {symbol} ({len(headlines)} items):"]
    for i, h in enumerate(headlines, 1):
        date_str = h.published.strftime("%Y-%m-%d") if h.published else "recent"
        lines.append(f"{i}. [{date_str}] {h.title}  ({h.source})")
        if h.summary:
            lines.append(f"   {h.summary[:200]}")
    return "\n".join(lines)
