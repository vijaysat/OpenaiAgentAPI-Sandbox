import csv
import html
import io
import json


def export_report(result, format):
    if format == "json":
        return json.dumps(result, indent=2, allow_nan=False).encode(), "application/json"
    rows = result["analysis"]["rows"]
    columns = ["symbol", "quantity", "price", "value", "weight", "targetWeight", "proposedQuantity", "quantityChange", "valueChange"]
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            # Only the first column is free text; negative numeric changes remain numeric.
            symbol = str(row["symbol"])
            if symbol.startswith(("=", "+", "-", "@", "\t", "\r")):
                symbol = "'" + symbol
            writer.writerow([symbol] + [row[c] for c in columns[1:]])
        return output.getvalue().encode(), "text/csv; charset=utf-8"
    if format != "html":
        raise ValueError("Use html, csv or json.")
    esc = lambda value: html.escape(str(value))
    table = "".join("<tr>" + "".join(f"<td>{esc(row[c])}</td>" for c in columns) + "</tr>" for row in rows)
    evidence = esc(json.dumps(result.get("evidence", {}), indent=2))
    quotes = "".join(f"<li>{esc(s)}: {esc(q['price'])} USD · as of {esc(q['asOf'])} · retrieved {esc(q['retrievedAt'])}</li>"
                     for s, q in result["analysis"]["quotes"].items())
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Portfolio review</title>
<style>body{{font:14px system-ui;margin:40px;color:#17352e}}h1{{font-size:32px}}table{{border-collapse:collapse;width:100%;font-size:11px}}td,th{{padding:8px;border-bottom:1px solid #ccc;text-align:left}}pre{{white-space:pre-wrap}}@media print{{body{{margin:15px}}}}</style>
<h1>Portfolio review</h1><p>{esc(result.get('mode', 'live'))} · Total value: ${esc(result['analysis']['totalValue'])}</p>
<p>Illustrative comparison only. Taxes, fees and execution prices excluded. No trades are placed.</p>
<h2>Advisor explanation</h2><pre>{esc(result.get('explanation', ''))}</pre>
<table><thead><tr>{''.join(f'<th>{esc(c)}</th>' for c in columns)}</tr></thead><tbody>{table}</tbody></table>
<h2>Price sources</h2><ul>{quotes}</ul><h2>Execution evidence</h2><pre>{evidence}</pre></html>'''
    return page.encode(), "text/html; charset=utf-8"
