from decimal import Decimal
import pytest
from portfolio_advisor.analytics import parse_csv, calculate, validate_targets


def test_import_case_normalization():
    assert parse_csv(b"symbol,quantity,currency\naapl,2.5,usd\nCASH,10,USD\n")[0] == {"symbol": "AAPL", "quantity": "2.5", "currency": "USD"}


@pytest.mark.parametrize("row", ["AAPL,-1,USD", "AAPL,nan,USD", "AAPL,inf,USD", "AAPL,1,EUR", "AAPL,0,USD", "=SUM(A1),1,USD", "AAPL,1.0000001,USD", "AAPL,2,USD,extra"])
def test_invalid_rows(row):
    with pytest.raises(ValueError):
        parse_csv(("symbol,quantity,currency\n" + row + "\n").encode())


def test_duplicate_empty_and_size():
    for data in (b"", b"symbol,quantity,currency\n", b"symbol,quantity,currency\nAAPL,1,USD\naapl,2,USD\n", b"x"*1_048_577):
        with pytest.raises(ValueError):
            parse_csv(data)


def test_known_rebalance_and_cash_rounding():
    holdings = parse_csv(b"symbol,quantity,currency\nAAPL,10,USD\nCASH,100,USD\n")
    quotes = {s: {"symbol": s, "price": p, "currency": "USD", "asOf": "2026-01-01"} for s,p in [("AAPL","100"),("BND","73")]}
    result = calculate(holdings,{"AAPL":50,"BND":45,"CASH":5},quotes)
    assert result["totalValue"] == "1100"
    assert result["largestPositionWeight"] == pytest.approx(90.9090909)
    assert result["rows"][0]["proposedQuantity"] == "5.500000"
    assert sum(Decimal(r["proposedValue"]) for r in result["rows"]) == Decimal(1100)
    cash = next(r for r in result["rows"] if r["symbol"]=="CASH")
    assert Decimal(cash["proposedValue"]) >= 55


def test_all_cash_and_liquidation():
    assert calculate([{"symbol":"CASH","quantity":"99"}],{"CASH":100},{})["totalValue"] == "99"
    result=calculate([{"symbol":"AAPL","quantity":"2"}],{"CASH":100},{"AAPL":{"symbol":"AAPL","price":"10","currency":"USD","asOf":"2026-01-01"}})
    assert result["rows"][0]["quantityChange"] == "-2.000000"


@pytest.mark.parametrize("weights",[{"AAPL":99},{"AAPL":101,"CASH":-1},{"AAPL":"NaN"},{"AAPL":True},{"bad symbol":100}])
def test_invalid_targets(weights):
    with pytest.raises(ValueError): validate_targets(weights)


def test_missing_quote_cannot_make_proposal():
    with pytest.raises(ValueError,match="Missing"):
        calculate([{"symbol":"AAPL","quantity":"2"}],{"CASH":100},{})


def test_missing_quote_for_new_target_blocks_proposal():
    with pytest.raises(ValueError, match='AMZN'):
        calculate([{'symbol': 'CASH', 'quantity': '1000'}], {'AMZN': 90, 'CASH': 10}, {})


def test_new_target_purchase_has_independently_known_amounts():
    holdings = [{'symbol': 'AAPL', 'quantity': '10'}, {'symbol': 'CASH', 'quantity': '100'}]
    quotes = {s: {'symbol': s, 'price': p, 'currency': 'USD', 'asOf': '2026-01-01'}
              for s, p in [('AAPL', '100'), ('AMZN', '50')]}
    rows = {r['symbol']: r for r in calculate(holdings, {'AAPL': 50, 'AMZN': 45, 'CASH': 5}, quotes)['rows']}
    assert rows['AMZN']['quantity'] == '0'
    assert rows['AMZN']['proposedQuantity'] == '9.900000'
    assert Decimal(rows['AMZN']['valueChange']) == 495
    assert Decimal(rows['AAPL']['quantityChange']) == Decimal('-4.5')
    assert Decimal(rows['CASH']['proposedValue']) == 55
    assert holdings == [{'symbol': 'AAPL', 'quantity': '10'}, {'symbol': 'CASH', 'quantity': '100'}]


def test_one_hundred_securities_plus_cash_and_zero_weight_legacy_positions():
    content='symbol,quantity,currency\n'+''.join(f'A{i},1,USD\n' for i in range(100))+'CASH,100,USD\n'
    holdings=parse_csv(content.encode())
    assert len(holdings)==101
    weights={h['symbol']:0 for h in holdings};weights.update(VTI=60,BND=35,CASH=5)
    assert len(validate_targets(weights))==103
