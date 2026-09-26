"""Standard-library-only engine. This exact source executes inside OCI."""
import csv
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
import io
import math
import re

SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,14}\Z")
TEMPLATES = [
    {"id": "equity-30", "name": "30% equity", "description": "30% VTI · 60% BND · 10% cash", "weights": {"VTI": 30, "BND": 60, "CASH": 10}},
    {"id": "equity-60", "name": "60% equity", "description": "60% VTI · 35% BND · 5% cash", "weights": {"VTI": 60, "BND": 35, "CASH": 5}},
    {"id": "equity-80", "name": "80% equity", "description": "80% VTI · 15% BND · 5% cash", "weights": {"VTI": 80, "BND": 15, "CASH": 5}},
]


def decimal(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError("Expected a finite decimal number.") from None


def parse_csv(content):
    if not content or len(content) > 1_048_576:
        raise ValueError("Upload a nonempty CSV no larger than 1 MiB.")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        if reader.fieldnames != ["symbol", "quantity", "currency"]:
            raise ValueError("CSV columns must be symbol,quantity,currency in that order.")
        positions, seen = [], set()
        for number, row in enumerate(reader, 2):
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("Each row needs exactly three columns.")
                symbol = row["symbol"].strip().upper()
                if not SYMBOL.fullmatch(symbol):
                    raise ValueError("Invalid ticker symbol.")
                if symbol in seen:
                    raise ValueError("Duplicate symbol; consolidate positions first.")
                quantity = decimal(row["quantity"].strip())
                if quantity <= 0 or quantity > Decimal("1000000000000") or quantity.as_tuple().exponent < -6:
                    raise ValueError("Quantity must be positive, at most 1 trillion, with up to six decimals.")
                if row["currency"].strip().upper() != "USD":
                    raise ValueError("Only USD holdings are supported.")
                positions.append({"symbol": symbol, "quantity": str(quantity), "currency": "USD"})
                seen.add(symbol)
            except ValueError as exc:
                raise ValueError(f"Row {number}: {exc}") from None
            if len(seen - {"CASH"}) > 100:
                raise ValueError("A portfolio can contain at most 100 securities plus USD cash.")
        if not positions:
            raise ValueError("The file contains no holdings.")
        return positions
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("Upload a valid UTF-8 CSV file.") from None


def validate_targets(weights):
    if not isinstance(weights, dict) or not weights or len(weights) > 201:
        raise ValueError("Supply target weights for at most 100 securities plus cash; zero-weight existing holdings may also be included.")
    result = {}
    for symbol, value in weights.items():
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise ValueError("Invalid target symbol.")
        number = decimal(value)
        if number < 0 or number > 100:
            raise ValueError("Target weights must be between 0 and 100.")
        result[symbol] = number
    if sum(1 for symbol, weight in result.items() if symbol != "CASH" and weight > 0) > 100:
        raise ValueError("At most 100 securities may have positive target weights.")
    if sum(result.values()) != 100:
        raise ValueError("Target weights must total exactly 100%.")
    return result


def calculate(holdings, weights, quotes):
    targets = validate_targets(weights)
    quantities = {p["symbol"]: decimal(p["quantity"]) for p in holdings}
    symbols = sorted(set(quantities) | {s for s, w in targets.items() if w > 0} | {"CASH"})
    prices = {"CASH": Decimal(1)}
    for symbol in symbols:
        if symbol == "CASH":
            continue
        quote = quotes.get(symbol, {})
        if quote.get("symbol") != symbol or quote.get("currency") != "USD" or not quote.get("asOf"):
            raise ValueError(f"Missing or invalid USD quote for {symbol}; no proposal generated.")
        prices[symbol] = decimal(quote.get("price"))
        if prices[symbol] <= 0:
            raise ValueError(f"Invalid price for {symbol}.")
    values = {s: quantities.get(s, Decimal(0)) * prices[s] for s in symbols}
    total = sum(values.values())
    if total <= 0:
        raise ValueError("Portfolio value must be positive.")
    # Round target share counts down. Residual value belongs to cash, so no borrowing occurs.
    proposed = {s: (total * targets.get(s, Decimal(0)) / 100 / prices[s]).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
                for s in symbols if s != "CASH"}
    proposed["CASH"] = total - sum(proposed[s] * prices[s] for s in proposed)
    rows = []
    for symbol in symbols:
        current = quantities.get(symbol, Decimal(0))
        after = proposed[symbol] * prices[symbol]
        rows.append({"symbol": symbol, "quantity": str(current), "price": str(prices[symbol]),
                     "value": str(values[symbol]), "weight": float(values[symbol] / total * 100),
                     "targetWeight": float(targets.get(symbol, 0)),
                     "drift": float(values[symbol] / total * 100 - targets.get(symbol, 0)),
                     "proposedQuantity": str(proposed[symbol]), "proposedValue": str(after),
                     "proposedWeight": float(after / total * 100),
                     "quantityChange": str(proposed[symbol] - current), "valueChange": str(after - values[symbol])})
    if sum(decimal(r["proposedValue"]) for r in rows) != total or proposed["CASH"] < 0:
        raise ValueError("Proposal did not conserve portfolio value.")
    return {"totalValue": str(total), "cashWeight": float(values["CASH"] / total * 100),
            "largestPositionWeight": max((r["weight"] for r in rows if r["symbol"] != "CASH"), default=0),
            "allocationDrift": sum(abs(r["drift"]) for r in rows) / 2,
            "rows": rows, "quotes": quotes,
            "assumptions": ["Illustrative allocation; not a suitability assessment.",
                            "Fractional shares are indicative; taxes, fees and execution prices are excluded.",
                            "ETF weights measure positions, not underlying issuer diversification."]}
