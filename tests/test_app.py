"""Offline contract tests use real bash/curl against a local fake HTTP service."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from geniagent.app import (Application, INITIAL_MESSAGE, PROJECT, ROOT, State,
                           execute_tool, load_dotenv, main, resource_id)
from geniagent.transport import APIError, CurlClient, decode_sse


class FakeAPI(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls = []
        self.tool_results = []
        self.stream_open = threading.Event()
        self.tool_ready = threading.Event()
        self.followups = queue.Queue()
        self.status = "idle"
        self.turns = []
        self.mode = "success"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def record(self, body=None):
        self.server.calls.append((self.command, self.path, body, dict(self.headers)))

    def json_response(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", "req_fixture")
        self.end_headers()
        self.wfile.write(body)

    def stream_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        self.server.stream_open.set()

    def event(self, kind, **kwargs):
        payload = json.dumps(dict(type=kind, **kwargs))
        self.wfile.write(f"event: {kind}\r\ndata: {payload}\r\n\r\n".encode())
        self.wfile.flush()

    def do_GET(self):
        self.record()
        parsed = urlparse(self.path)
        if parsed.path == "/v1/agents":
            if self.server.mode == "unauthorized":
                return self.json_response({"error": {"message": "Bad key sk-fixture-secret"}}, 401)
            return self.json_response({"data": []})
        if parsed.path == "/v1/agents/agent_fixture":
            return self.json_response({"id": "agent_fixture"})
        if parsed.path.endswith("/events"):
            self.stream_headers()
            try:
                kind = self.server.followups.get(timeout=3)
                if kind == "done":
                    self.event("agent.session.turn.output_text.done", item_id="msg_followup", text="Follow-up works.")
                    self.event("agent.session.turn.completed", turn={"id": "turn_2", "subagent_id": None})
            except (queue.Empty, BrokenPipeError, ConnectionResetError):
                pass
            return
        if parsed.path.endswith("/items"):
            if self.server.mode == "paginated" and not parse_qs(parsed.query).get("after"):
                return self.json_response({"data": [{"id": "item_1"}], "has_more": True, "last_id": "item_1"})
            return self.json_response({"data": [{"id": "msg_saved", "status": "completed", "role": "assistant",
                                                  "content": [{"type": "output_text", "text": "Saved answer."}]}], "has_more": False})
        if parsed.path.endswith("/turns"):
            return self.json_response({"data": self.server.turns, "has_more": False})
        if parsed.path == "/v1/agents/sessions/sess_fixture":
            actions = []
            if self.server.status == "requires_action":
                actions = [action_fixture()]
            return self.json_response({"id": "sess_fixture", "status": self.server.status, "required_actions": actions})
        self.json_response({"error": "unknown path"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.record(body)
        if self.path == "/v1/agents":
            return self.json_response({"id": "agent_fixture"})
        if self.path == "/v1/agents/sessions":
            self.stream_headers()
            self.event("agent.session.created", session={"id": "sess_fixture"})
            self.event("agent.session.idle")
            if self.server.mode == "disconnect":
                return
            if self.server.mode == "failure":
                self.event("agent.session.environment.failed", environment={"error": "setup failed"})
                return
            self.event("agent.session.turn.started", turn={"id": "turn_1", "subagent_id": None})
            self.server.status = "requires_action"
            self.event("agent.session.requires_action", session={"id": "sess_fixture"})
            if not self.server.tool_ready.wait(3):
                return
            self.event("agent.session.turn.completed", turn={"id": "child_turn", "subagent_id": "sub_1"})
            self.event("agent.session.turn.output_text.delta", item_id="msg_1", content_index=0, delta="Hello ")
            self.event("agent.session.turn.output_text.done", item_id="msg_1", content_index=0, text="Hello world.")
            self.server.status = "idle"
            self.server.turns = [{"id": "turn_1", "status": "completed", "subagent_id": None}]
            self.event("agent.session.turn.completed", turn=self.server.turns[0])
            return
        if self.path.endswith("/events"):
            event = body["events"][0]
            if event["type"] == "agent.session.input.tool_result":
                self.server.tool_results.append(event)
                self.server.status = "in_progress"
                self.json_response({})
                self.server.tool_ready.set()
                return
            if event["type"] == "agent.session.input.message":
                if not self.server.stream_open.is_set():
                    return self.json_response({"error": "stream not open"}, 409)
                if self.server.mode == "input_rejected":
                    return self.json_response({"error": "Rejected sk-fixture-secret"}, 401)
                self.json_response({})
                self.server.followups.put("done")
                return
            return self.json_response({})
        self.json_response({"error": "unknown path"}, 404)


def action_fixture(**kwargs):
    return dict({"type": "function_call", "name": "get_current_time", "turn_id": "turn_1",
                 "call_id": "call_1", "arguments": {"timezone": "UTC"}}, **kwargs)


@contextmanager
def running_api():
    server = FakeAPI()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.output, self.errors = io.StringIO(), io.StringIO()

    def app(self, server):
        client = CurlClient("sk-fixture-secret", PROJECT, f"http://127.0.0.1:{server.server_port}/v1")
        return Application(client, State(self.folder, PROJECT), timeout=5, out=self.output, err=self.errors)

    def test_full_create_stream_and_function_result(self):
        with running_api() as server:
            app = self.app(server)
            definition = json.loads((ROOT / "config/agent.json").read_text())
            environment = json.loads((ROOT / "config/environment.json").read_text())
            app.start(definition, environment, INITIAL_MESSAGE)
            self.assertEqual(self.output.getvalue(), "Hello world.\n")
            self.assertEqual(app.state.session(), "sess_fixture")
            self.assertEqual(app.state.load("agent.json")["agent_id"], "agent_fixture")
            create_session = next(c[2] for c in server.calls if c[0] == "POST" and c[1] == "/v1/agents/sessions")
            self.assertEqual(create_session["agent_id"], "agent_fixture")
            self.assertNotIn("agent", create_session)
            self.assertTrue(server.tool_results[0]["success"])
            self.assertEqual(server.tool_results[0]["call_id"], "call_1")
            self.assertEqual(server.tool_results[0]["turn_id"], "turn_1")
            for call in server.calls:
                headers = {k.lower(): v for k, v in call[3].items()}
                self.assertEqual(headers["openai-project"], PROJECT)
                self.assertEqual(headers["openai-beta"], "agents=v1")
            self.assertNotIn("sk-fixture-secret", (self.folder / "sess_fixture-events.jsonl").read_text())
            self.assertEqual((self.folder / "agent.json").stat().st_mode & 0o777, 0o600)
            app.agent(definition)
            self.assertEqual(sum(c[0] == "POST" and c[1] == "/v1/agents" for c in server.calls), 1)

    def test_followup_subscribes_before_post(self):
        with running_api() as server:
            server.turns = [{"id": "turn_1", "status": "completed"}]
            app = self.app(server)
            app.continue_session("sess_fixture", "Continue")
            paths = [(x[0], x[1]) for x in server.calls]
            self.assertLess(paths.index(("GET", "/v1/agents/sessions/sess_fixture/events?stream=true")),
                            paths.index(("POST", "/v1/agents/sessions/sess_fixture/events")))
            self.assertEqual(self.output.getvalue(), "Follow-up works.\n")
            log = (self.folder / "sess_fixture-client.jsonl").read_text()
            self.assertIn("Message request accepted", log)
            self.assertIn("Turn completed", log)

    def test_rejected_input_is_logged_without_fabricated_server_events(self):
        with running_api() as server:
            server.mode = "input_rejected"
            client = self.app(server).client
            with patch("geniagent.app.CurlClient", return_value=client), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fixture-secret", "OPENAI_PROJECT_ID": PROJECT}), \
                    patch("sys.stderr", self.errors):
                result = main(["--env-file", str(self.folder / "absent.env"),
                               "--state-dir", str(self.folder), "--timeout", "5",
                               "send", "--session-id", "sess_fixture", "Hi"])
            self.assertEqual(result, 1)
            path = self.folder / "sess_fixture-client.jsonl"
            log = path.read_text()
            self.assertIn("Submitting user message", log)
            self.assertIn("HTTP 401", log)
            self.assertIn("req_fixture", log)
            self.assertNotIn("Message request accepted", log)
            self.assertNotIn("sk-fixture-secret", log)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse((self.folder / "sess_fixture-events.jsonl").exists())

    def test_oci_preflight_error_is_logged_before_streaming(self):
        State(self.folder, PROJECT).save("sess_fixture-oci.json", {"sandbox_id": "sbx_fixture"})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fixture-secret", "OPENAI_PROJECT_ID": PROJECT}), \
                patch("geniagent.oci_runtime.OCIRuntime") as runtime, patch("sys.stderr", self.errors):
            runtime.return_value.health.side_effect = APIError("OCI executor expired")
            result = main(["--env-file", str(self.folder / "absent.env"),
                           "--state-dir", str(self.folder), "send", "--session-id", "sess_fixture", "Hi"])
        self.assertEqual(result, 1)
        log = (self.folder / "sess_fixture-client.jsonl").read_text()
        self.assertIn("Checking OCI", log)
        self.assertIn("OCI executor expired", log)
        self.assertNotIn("Submitting user message", log)

    def test_resume_reads_completed_history_without_resending(self):
        with running_api() as server:
            server.turns = [{"id": "turn_1", "status": "completed"}]
            app = self.app(server)
            app.continue_session("sess_fixture")
            self.assertIn("Saved answer.", self.output.getvalue())
            self.assertFalse(any(call[0] == "POST" for call in server.calls))

    def test_http_error_is_redacted_with_request_id(self):
        with running_api() as server:
            server.mode = "unauthorized"
            with self.assertRaises(APIError) as context:
                self.app(server).client.request("GET", "/agents?limit=1")
            message = str(context.exception)
            self.assertIn("401", message)
            self.assertIn("req_fixture", message)
            self.assertNotIn("sk-fixture-secret", message)

    def test_stream_http_error(self):
        with running_api() as server:
            server.mode = "unauthorized"
            with self.assertRaisesRegex(APIError, "HTTP 401"):
                with self.app(server).client.stream("GET", "/agents"):
                    self.fail("HTTP error must not open stream")

    def test_disconnect_retains_session_without_retry(self):
        with running_api() as server:
            server.mode = "disconnect"
            app = self.app(server)
            with self.assertRaisesRegex(APIError, "before the root turn completed"):
                app.start({"model": "gpt-6-astra"}, {"type": "none"}, "Hi")
            self.assertEqual(app.state.session(), "sess_fixture")
            self.assertEqual(sum(c[0] == "POST" and c[1] == "/v1/agents/sessions" for c in server.calls), 1)

    def test_environment_failure_does_not_report_success(self):
        with running_api() as server:
            server.mode = "failure"
            with self.assertRaisesRegex(APIError, "setup failed"):
                self.app(server).start({"model": "gpt-6-astra"}, {"type": "openai_hosted"}, "Hi")

    def test_pagination_reads_every_page(self):
        with running_api() as server:
            server.mode = "paginated"
            items = list(self.app(server).paginated("/agents/sessions/sess_fixture/items"))
            self.assertEqual([item["id"] for item in items], ["item_1", "msg_saved"])

    def test_tool_result_is_durable_and_reused(self):
        with running_api() as server:
            app = self.app(server)
            app.remember_session("sess_fixture")
            session = {"required_actions": [action_fixture()]}
            app.handle_actions(session)
            with patch("geniagent.app.execute_tool", side_effect=AssertionError("reran tool")):
                app.handle_actions(session)
            self.assertEqual(server.tool_results[0], server.tool_results[1])

    def test_busy_session_does_not_accept_followup(self):
        with running_api() as server:
            server.status = "in_progress"
            with self.assertRaisesRegex(APIError, "not idle"):
                self.app(server).continue_session("sess_fixture", "Hi")
            self.assertFalse(any(call[0] == "POST" for call in server.calls))


class UnitTests(unittest.TestCase):
    def test_recovery_does_not_duplicate_overlapping_partial_deltas(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            app = Application(CurlClient("fake", PROJECT), State(Path(folder), PROJECT), out=output, err=io.StringIO())
            app.display_items([{"id": "msg", "role": "assistant", "status": "in_progress",
                                "content": [{"type": "output_text", "text": "Hello"}]}])
            app.handle_event({"type": "agent.session.turn.output_text.delta", "item_id": "msg", "delta": "Hello"})
            app.handle_event({"type": "agent.session.turn.output_text.done", "item_id": "msg", "text": "Hello world"})
            self.assertEqual(output.getvalue(), "Hello\n world\n")

    def test_sse_comments_crlf_multiline_and_done(self):
        lines = ': ping\r\nevent: demo\r\ndata: {"value":\r\ndata: 3}\r\n\r\ndata: [DONE]\n\n'.splitlines(True)
        self.assertEqual(list(decode_sse(lines)), [{"type": "demo", "value": 3}])

    def test_partial_frame_is_error(self):
        with self.assertRaisesRegex(APIError, "incomplete"):
            list(decode_sse(['data: {"type":"demo"}\n']))

    def test_invalid_json_is_error(self):
        with self.assertRaisesRegex(APIError, "Invalid JSON"):
            list(decode_sse(["data: not-json\n", "\n"]))

    def test_tool_validation(self):
        for action in (action_fixture(name="shell"), action_fixture(arguments={"timezone": "invalid/zone"}),
                       action_fixture(arguments={"timezone": "UTC", "command": "bad"}),
                       action_fixture(arguments="invalid")):
            with self.subTest(action=action):
                self.assertFalse(execute_tool(action)["success"])
        self.assertTrue(execute_tool(action_fixture())["success"])

    def test_dotenv_is_literal_and_env_takes_precedence(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder) / ".env"
            path.write_text("OPENAI_API_KEY='$(do_not_execute)'\nOCI_OTHER='unclosed\n")
            load_dotenv(path)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "$(do_not_execute)")
            os.environ["OPENAI_API_KEY"] = "exported"
            load_dotenv(path)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "exported")

    def test_resource_path_injection_rejected(self):
        for value in ("../other", "sess?x=1", "", None):
            with self.assertRaises(APIError):
                resource_id(value)

    def test_project_state_cannot_be_reused_across_projects(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            State(path, PROJECT).save("agent.json", {"agent_id": "agent_test"})
            with self.assertRaisesRegex(APIError, "another OpenAI project"):
                State(path, "another_project").load("agent.json")

    def test_idle_and_subagent_completion_are_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Application(CurlClient("fake", PROJECT), State(Path(folder), PROJECT), err=io.StringIO())
            self.assertFalse(app.handle_event({"type": "agent.session.idle"}))
            self.assertFalse(app.handle_event({"type": "agent.session.turn.completed", "turn": {"subagent_id": "sub_1"}}))
            with self.assertRaises(APIError):
                app.handle_event({"type": "agent.session.turn.cancelled", "turn": {"subagent_id": None}})


if __name__ == "__main__":
    unittest.main()
