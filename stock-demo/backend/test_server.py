import io
import os
import unittest
from unittest.mock import patch
from backend.provider import QuoteError, get_quote, search_stocks
from backend.server import main


class StartupTests(unittest.TestCase):
    def test_missing_or_invalid_session_is_rejected_before_binding(self):
        for args in ([], ["--session-id", "<SESSION_ID>"], ["--session-id", "../session"]):
            with self.subTest(args=args), patch("backend.server.ThreadingHTTPServer") as server, \
                 patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)
                server.assert_not_called()

    def test_missing_project_is_rejected_before_binding(self):
        with patch.dict(os.environ, {}, clear=True), patch("backend.server.load_dotenv"), \
             patch("backend.server.ThreadingHTTPServer") as server, patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--session-id", "sess_explicit"])
            server.assert_not_called()

    def test_explicit_session_is_used_by_server(self):
        with patch.dict(os.environ, {"OPENAI_PROJECT_ID": "proj_selected"}), \
             patch("backend.server.load_dotenv"), patch("backend.server.ThreadingHTTPServer") as server, \
             patch("sys.stdout", io.StringIO()):
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            main(["--session-id", "sess_explicit"])
            self.assertEqual(server.return_value.stock_agent.session_id, "sess_explicit")
            server.return_value.server_close.assert_called_once()


class ProviderTests(unittest.TestCase):
    def quote(self, **changes):
        meta = {"symbol": "AAPL", "instrumentType": "EQUITY", "currency": "USD",
                "regularMarketPrice": 105, "previousClose": 100, "chartPreviousClose": 90,
                "regularMarketTime": 1790107201, "longName": "Apple Inc."}
        meta.update(changes)
        return {"chart": {"result": [{"meta": meta}], "error": None}}

    @patch("backend.provider.fetch_json")
    def test_search_filters_futures_and_duplicates_and_keeps_exchange(self, fetch):
        fetch.return_value = {"quotes": [
            {"symbol": "AAPL", "quoteType": "EQUITY", "longname": "Apple Inc.", "exchDisp": "NASDAQ"},
            {"symbol": "AAPL", "quoteType": "EQUITY"},
            {"symbol": "AAPL=F", "quoteType": "FUTURE"},
            {"symbol": "AAPL.DE", "quoteType": "EQUITY", "exchDisp": "XETRA"}]}
        result = search_stocks(" Apple ")["results"]
        self.assertEqual([x["symbol"] for x in result], ["AAPL", "AAPL.DE"])
        self.assertEqual(result[0]["exchange"], "NASDAQ")

    @patch("backend.provider.fetch_json")
    def test_change_uses_previous_close_and_has_provider_timestamp(self, fetch):
        fetch.return_value = self.quote()
        q = get_quote("AAPL")
        self.assertEqual((q["price"], q["change"], q["changePercent"]), (105, 5, 5))
        self.assertEqual(q["asOf"], "2026-09-22T20:00:01+00:00")
        self.assertEqual(fetch.call_args.args[1]["range"], "1d")

    @patch("backend.provider.fetch_json")
    def test_missing_previous_close_is_not_invented(self, fetch):
        fetch.return_value = self.quote(previousClose=None, chartPreviousClose=None)
        self.assertIsNone(get_quote("AAPL")["changePercent"])

    @patch("backend.provider.fetch_json")
    def test_currency_units_are_preserved(self, fetch):
        fetch.return_value = self.quote(symbol="VOD.L", currency="GBp")
        self.assertEqual(get_quote("VOD.L")["currency"], "GBp")

    @patch("backend.provider.fetch_json")
    def test_invalid_or_missing_data_cannot_look_like_a_live_quote(self, fetch):
        for changes in ({"regularMarketPrice": None}, {"regularMarketPrice": float("nan")},
                        {"regularMarketTime": None}, {"currency": None}, {"symbol": "MSFT"}):
            with self.subTest(changes=changes):
                fetch.return_value = self.quote(**changes)
                with self.assertRaises(QuoteError):
                    get_quote("AAPL")

    @patch("backend.provider.fetch_json")
    def test_empty_search_is_a_real_empty_result(self, fetch):
        fetch.return_value = {"quotes": []}
        self.assertEqual(search_stocks("nonsuchcompany")["results"], [])

    @patch("backend.provider.fetch_json")
    def test_bad_inputs_do_not_reach_provider(self, fetch):
        for bad in ("../../secret", "https://example.com", "", "A\n"):
            with self.assertRaises(QuoteError):
                get_quote(bad)
        with self.assertRaises(QuoteError):
            search_stocks("a")
        fetch.assert_not_called()

    @patch("backend.provider.fetch_json")
    def test_provider_error_and_malformed_payload_are_explicit(self, fetch):
        for payload in ({}, {"chart": {"result": None, "error": {"code": "Not Found"}}}):
            fetch.return_value = payload
            with self.assertRaises(QuoteError):
                get_quote("AAPL")


if __name__ == "__main__":
    unittest.main()
