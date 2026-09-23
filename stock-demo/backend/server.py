"""Local UI API: every search/quote is a real OpenAI Agents API tool turn."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib.parse import urlsplit

from .agent import DEFAULT_SESSION_ID, StockAgentError, StockAgentService


class Handler(BaseHTTPRequestHandler):
    def allowed_origins(self):
        allowed = {"http://localhost:8081", "http://127.0.0.1:8081", "http://localhost:19006", "http://127.0.0.1:19006"}
        allowed.update(x.strip() for x in os.environ.get("STOCK_DEMO_ORIGINS", "").split(",") if x.strip())
        return allowed

    def send_json(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin in self.allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The browser disconnected; the report and session IDs remain saved.

    def do_OPTIONS(self):
        self.send_json(200 if self.headers.get("Origin") in self.allowed_origins() else 403, {})

    def do_GET(self):
        if urlsplit(self.path).path == "/api/health":
            self.send_json(200, {"status": "ok", "service": "stock-demo", "orchestrator": "OpenAI Agents API",
                                 "sessionId": self.server.stock_agent.session_id, "execution": "OCI sandbox"})
        else:
            self.send_json(404, {"error": "Use POST /api/search or POST /api/quote."})

    def do_POST(self):
        try:
            origin = self.headers.get("Origin")
            if origin and origin not in self.allowed_origins():
                raise StockAgentError("This browser origin is not allowed.", 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise StockAgentError("Expected application/json.", 415)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise StockAgentError("Invalid request size.", 400)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise StockAgentError("Expected a JSON object.", 400)
            route = urlsplit(self.path).path
            operation = {"/api/search": "search", "/api/quote": "quote"}.get(route)
            if not operation:
                raise StockAgentError("Endpoint not found.", 404)
            result = self.server.stock_agent.run(operation, body.get("query" if operation == "search" else "symbol"), body.get("requestId"))
            self.send_json(200, result)
        except StockAgentError as exc:
            self.send_json(exc.status, {"error": str(exc), "agent": exc.agent})
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "Invalid request body."})
        except Exception:
            self.send_json(500, {"error": "The agent request could not finish. Inspect .agent-state before retrying."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID, help="Existing OpenAI session to continue; never creates a replacement session")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.stock_agent = StockAgentService(session_id=args.session_id)
    print(f"Stock demo / OpenAI Agents API: http://{args.host}:{args.port}", flush=True)
    print(f"Using existing session: {args.session_id}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
