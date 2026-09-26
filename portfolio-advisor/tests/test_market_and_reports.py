import json
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
from portfolio_advisor.market import get_quote
from portfolio_advisor.reports import export_report

class Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,*a):return json.dumps(self.data).encode()

@pytest.mark.parametrize('changed',[{'currency':'EUR'},{'instrumentType':'CRYPTOCURRENCY'},{'exchangeName':'LSE'},{'regularMarketPrice':None}])
def test_invalid_market_quote_blocks_proposal(changed):
    meta={'symbol':'AAPL','currency':'USD','instrumentType':'EQUITY','exchangeName':'NMS','regularMarketPrice':200,'regularMarketTime':1700000000,**changed}
    with patch('portfolio_advisor.market.urlopen',return_value=Response({'chart':{'result':[{'meta':meta}],'error':None}})):
        with pytest.raises(ValueError):get_quote('AAPL')


def test_html_escapes_agent_text():
    result={'mode':'rehearsal','explanation':'<script>alert(1)</script>','analysis':{'rows':[],'totalValue':'100','quotes':{},'assumptions':[]},'evidence':{}}
    body,_=export_report(result,'html')
    assert b'<script>' not in body and b'&lt;script&gt;' in body
