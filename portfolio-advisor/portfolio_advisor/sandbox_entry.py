"""Appended to analytics.py and market.py for a single auditable sandbox helper."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sys


def main():
    input_path, output_path = map(Path, sys.argv[1:3])
    raw = input_path.read_bytes()
    request = json.loads(raw)
    symbols = sorted(({p["symbol"] for p in request["holdings"]} |
                      {s for s, w in request["weights"].items() if decimal(w) > 0}) - {"CASH"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        quotes = dict(zip(symbols, pool.map(get_quote, symbols)))
    analysis = calculate(request["holdings"], request["weights"], quotes)
    result = {"requestId": request["requestId"], "inputSha256": hashlib.sha256(raw).hexdigest(), "analysis": analysis}
    output_path.write_text(json.dumps(result, allow_nan=False))
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
