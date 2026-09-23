"""Small, read-only JSON service for Yahoo Finance's public search/chart feeds."""
from datetime import datetime, timezone
import json
import math
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote as encode_path, urlencode
from urllib.request import Request, urlopen

PROVIDER = "https://query1.finance.yahoo.com"
SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\^=-]{0,29}\Z")


class QuoteError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def timestamp(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise QuoteError("The provider did not supply a valid price timestamp.")
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        raise QuoteError("The provider returned an invalid price timestamp.") from None


def number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


def fetch_json(path, params):
    # The host and paths are application-owned; no arbitrary URL proxying.
    request = Request(f"{PROVIDER}{path}?{urlencode(params)}", headers={
        "User-Agent": "Mozilla/5.0 StockLookupDemo/1.0",
        "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=12) as response:
            content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise QuoteError("The price provider returned too much data.")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise QuoteError("The price provider returned an unexpected response.")
        return result
    except HTTPError as exc:
        if exc.code == 404:
            raise QuoteError("No quote is available for that symbol.", 404) from None
        if exc.code == 429:
            raise QuoteError("The price provider is busy. Please try again shortly.", 503) from None
        raise QuoteError("The price provider is unavailable. Please try again later.") from None
    except (TimeoutError, socket.timeout):
        raise QuoteError("The price provider timed out. Please try again.", 504) from None
    except (URLError, UnicodeDecodeError, json.JSONDecodeError):
        raise QuoteError("Could not reach or read the price provider. Please try again.") from None


def search_stocks(query):
    query = query.strip()
    if not 2 <= len(query) <= 80 or any(ord(c) < 32 for c in query):
        raise QuoteError("Enter a company name or symbol between 2 and 80 characters.", 400)
    payload = fetch_json("/v1/finance/search", {"q": query, "quotesCount": 12, "newsCount": 0})
    rows = payload.get("quotes")
    if not isinstance(rows, list):
        raise QuoteError("The search provider returned an unexpected response.")
    results, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol", "")
        if row.get("quoteType") != "EQUITY" or not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        results.append({"symbol": symbol, "name": row.get("longname") or row.get("shortname") or symbol,
                        "exchange": row.get("exchDisp") or row.get("exchange") or "Unknown exchange"})
    return {"results": results, "source": "Yahoo Finance"}


def get_quote(symbol):
    if not SYMBOL.fullmatch(symbol):
        raise QuoteError("Select a valid stock symbol.", 400)
    # A one-day chart's previous close is the last session close, not the start of a five-day range.
    payload = fetch_json(f"/v8/finance/chart/{encode_path(symbol, safe='')}", {"interval": "1m", "range": "1d"})
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise QuoteError("The quote provider returned an unexpected response.")
    results = chart.get("result")
    if chart.get("error") or not isinstance(results, list) or not results:
        raise QuoteError("No quote is available for that symbol.", 404)
    meta = results[0].get("meta") if isinstance(results[0], dict) else None
    if not isinstance(meta, dict):
        raise QuoteError("The quote provider returned an unexpected response.")
    if meta.get("instrumentType") != "EQUITY":
        raise QuoteError("This demo supports company stocks only.", 400)
    returned_symbol = meta.get("symbol")
    if not isinstance(returned_symbol, str) or returned_symbol.upper() != symbol.upper():
        raise QuoteError("The provider returned a different symbol. Search again.")
    price, previous = number(meta.get("regularMarketPrice")), number(meta.get("previousClose"))
    if previous is None:
        previous = number(meta.get("chartPreviousClose"))
    currency = meta.get("currency")
    if price is None or price <= 0 or not isinstance(currency, str) or not currency:
        raise QuoteError("The provider has no usable price or currency for this stock.", 404)
    as_of = timestamp(meta.get("regularMarketTime"))
    change = price - previous if previous is not None and previous > 0 else None
    now = datetime.now(timezone.utc)
    return {
        "symbol": returned_symbol,
        "name": meta.get("longName") or meta.get("shortName") or returned_symbol,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "Unknown exchange",
        "currency": currency, "price": price, "previousClose": previous,
        "change": change, "changePercent": change / previous * 100 if change is not None else None,
        "dayHigh": number(meta.get("regularMarketDayHigh")), "dayLow": number(meta.get("regularMarketDayLow")),
        "asOf": as_of, "fetchedAt": now.isoformat(), "source": "Yahoo Finance",
        "priceType": "Latest regular-session price",
        "notice": "Prices may be delayed. When the market is closed, the last regular-session price is shown.",
    }

