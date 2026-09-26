"""Public Yahoo chart adapter; called only by the OCI sandbox helper in live mode."""
from datetime import datetime, timezone
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
if __package__:
    from .analytics import SYMBOL, decimal


def get_quote(symbol):
    if not SYMBOL.fullmatch(symbol) or symbol == "CASH":
        raise ValueError("Invalid market symbol.")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?interval=1d&range=5d"
    try:
        with urlopen(Request(url, headers={"User-Agent": "PortfolioAdvisorDemo/1.0", "Accept": "application/json"}), timeout=15) as response:
            content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError()
        chart = json.loads(content)["chart"]
        if chart.get("error"):
            raise ValueError()
        meta = chart["result"][0]["meta"]
        if meta.get("symbol", "").upper() != symbol or meta.get("currency") != "USD":
            raise ValueError()
        if meta.get("instrumentType") not in ("EQUITY", "ETF"):
            raise ValueError()
        if meta.get("exchangeName") not in ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "BATS", "ARCA"):
            raise ValueError()
        price = decimal(meta.get("regularMarketPrice"))
        timestamp = meta.get("regularMarketTime")
        if price <= 0 or not isinstance(timestamp, (float, int)) or isinstance(timestamp, bool) or timestamp <= 0:
            raise ValueError()
        as_of = datetime.fromtimestamp(timestamp, timezone.utc)
        now = datetime.now(timezone.utc)
        if as_of.timestamp() > now.timestamp() + 300:
            raise ValueError()
        return {"symbol": symbol, "price": str(price), "currency": "USD", "asOf": as_of.isoformat(),
                "retrievedAt": now.isoformat(), "source": "Yahoo Finance", "sourceUrl": url,
                "instrumentType": meta["instrumentType"], "exchange": meta["exchangeName"],
                "olderThan72Hours": now.timestamp() - as_of.timestamp() > 72 * 3600}
    except HTTPError as exc:
        raise ValueError(f"Price provider returned HTTP {exc.code} for {symbol}; no proposal generated.") from None
    except (URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError, OverflowError):
        raise ValueError(f"No verified US-listed stock/ETF USD price for {symbol}; no proposal generated.") from None
