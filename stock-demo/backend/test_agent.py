from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from backend.agent import (State, StockAgentError, StockAgentService,
                           SessionApplication, matches_command, verify_result)
from geniagent.transport import APIError


PROJECT = "proj_stock_test"
SESSION_ID = "sess_stock_fixture"


class FakeRuntime:
    def __init__(self):
        self.files = {}
        self.calls = []

    def filename(self, sid):
        return f"{sid}-oci.json"

    def health(self, sid):
        self.calls.append(("health", sid))

    def call(self, method, sandbox_id, path, content=None, **kwargs):
        self.calls.append((method, sandbox_id, path))
        if method == "write_sandbox_file":
            self.files[path] = content.getvalue().decode()
            return SimpleNamespace(data=None)
        if method == "read_sandbox_file":
            return SimpleNamespace(data=SimpleNamespace(content=self.files[path]))
        raise AssertionError(method)


class FakeClient:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []
        self.connected = True
        self.idle = True
        self.command = None

    def redact(self, value):
        return str(value).replace("sk-test", "[REDACTED]")

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == f"/agents/sessions/{SESSION_ID}":
            return {"id": SESSION_ID, "status": "idle" if self.idle else "in_progress",
                    "agent": {"id": "agent_original", "model": "gpt-6-luna"},
                    "environment": {"id": "env_original", "type": "self_hosted", "workspace_directory": "/workspace"}}
        if method == "GET" and path == "/agents/environments/env_original":
            return {"status": "connected" if self.connected else "disconnected"}
        if "/items?" in path:
            return {"data": [], "has_more": False}
        if "/turns?" in path:
            return {"data": [{"id": "turn_old", "status": "completed"}], "has_more": False}
        if method == "POST" and path == f"/agents/sessions/{SESSION_ID}/events":
            event = body["events"][0]
            assert event["type"] == "agent.session.input.message"
            self.command = event["input"][0]["content"][0]["text"].splitlines()[1]
            return {}
        # In particular, creation of agents or sessions is forbidden by this fake.
        raise AssertionError((method, path))

    @contextmanager
    def stream(self, method, path, timeout):
        self.calls.append((method, path, None))
        assert method == "GET" and path == f"/agents/sessions/{SESSION_ID}/events?stream=true"

        def events():
            request_path, output_path = shlex.split(self.command)[-2:]
            request = json.loads(self.runtime.files[request_path])
            self.runtime.files[output_path] = json.dumps({**request, "payload": {
                "source": "Yahoo Finance", "results": [{"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ"}]}})
            yield {"type": "agent.session.turn.item.done", "turn_id": "turn_new", "item": {
                "id": "call_command", "type": "command_execution", "turn_id": "turn_new", "status": "completed",
                "command": "/bin/bash -lc " + shlex.quote(self.command), "exit_code": 0}}
            yield {"type": "agent.session.turn.item.done", "item": {"role": "assistant", "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Found Apple in OCI."}]}}
            yield {"type": "agent.session.turn.completed", "turn": {"id": "turn_new", "status": "completed"}}
        yield events()


class ExistingSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.runtime = FakeRuntime()
        self.client = FakeClient(self.runtime)
        State(self.folder / ".geniagent", PROJECT).save(f"{SESSION_ID}-oci.json", {
            "sandbox_id": "sbx_fixture", "environment_id": "env_original"})
        for patcher in (patch("backend.agent.ROOT", self.folder), patch("backend.agent.load_dotenv"),
                        patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "OPENAI_PROJECT_ID": PROJECT}),
                        patch("backend.agent.CurlClient", return_value=self.client),
                        patch("backend.agent.OCIRuntime", return_value=self.runtime)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_uses_exact_existing_session_and_reads_oci_artifact(self):
        request_id = str(uuid.uuid4())
        service = StockAgentService(self.folder / "demo", session_id=SESSION_ID)
        result = service.run("search", "Apple", request_id)
        self.assertEqual(result["agent"]["sessionId"], SESSION_ID)
        self.assertEqual(result["agent"]["agentId"], "agent_original")
        self.assertEqual(result["agent"]["execution"], "OCI sandbox")
        self.assertEqual(result["agent"]["turnId"], "turn_new")
        self.assertEqual(result["results"][0]["symbol"], "AAPL")
        count = len(self.client.calls)
        self.assertEqual(result, service.run("search", "Apple", request_id))
        self.assertEqual(len(self.client.calls), count)
        opened = next(i for i,c in enumerate(self.client.calls) if "stream=true" in c[1])
        submitted = next(i for i,c in enumerate(self.client.calls) if c[0] == "POST")
        self.assertLess(opened, submitted)
        self.assertTrue(any(c[0] == "read_sandbox_file" and "stock_result" in c[2] for c in self.runtime.calls))

    def test_disconnected_session_does_not_send_input_or_create_resources(self):
        self.client.connected = False
        with self.assertRaisesRegex(StockAgentError, "disconnected"):
            StockAgentService(self.folder / "demo", session_id=SESSION_ID).run("search", "Apple", str(uuid.uuid4()))
        self.assertFalse(any(c[0] == "POST" for c in self.client.calls))
        self.assertEqual(self.runtime.calls, [])

    def test_missing_project_does_not_reach_remote_services(self):
        for project in ("", " "):
            with patch.dict(os.environ, {"OPENAI_PROJECT_ID": project}):
                with self.assertRaisesRegex(StockAgentError, "OPENAI_PROJECT_ID"):
                    StockAgentService(self.folder / "demo", session_id=SESSION_ID).run(
                        "search", "Apple", str(uuid.uuid4()))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.runtime.calls, [])

    def test_busy_session_does_not_submit_another_turn(self):
        self.client.idle = False
        with self.assertRaisesRegex(StockAgentError, "not idle"):
            StockAgentService(self.folder / "demo", session_id=SESSION_ID).run("search", "Apple", str(uuid.uuid4()))
        self.assertFalse(any(c[0] == "POST" for c in self.client.calls))

    def test_oci_connection_failure_returns_valid_http_error_and_retains_evidence(self):
        request_id = str(uuid.uuid4())
        service = StockAgentService(self.folder / "demo", session_id=SESSION_ID)
        failure = APIError("OCI connection failed: sk-test", status="unknown")
        with patch.object(self.runtime, "health", side_effect=failure):
            with self.assertRaises(StockAgentError) as caught:
                service.run("search", "Apple", request_id)
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn("sk-test", str(caught.exception))
        self.assertEqual(caught.exception.agent["sessionId"], SESSION_ID)
        report = json.loads((self.folder / "demo" / "sessions" / SESSION_ID /
                             "requests" / request_id / "report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("sk-test", report["error"])
        self.assertFalse(any(c[0] == "POST" for c in self.client.calls))

    def test_old_turn_or_model_text_cannot_prove_execution(self):
        app = SessionApplication(self.client, State(self.folder / "app", PROJECT))
        app.remember_session(SESSION_ID)
        app.ignored_turns = {"turn_old"}
        app.handle_event({"type": "agent.session.turn.item.done", "turn_id": "turn_old", "item": {
            "id": "old", "type": "command_execution", "command": "python3 helper.py", "status": "completed", "exit_code": 0}})
        app.handle_event({"type": "agent.session.turn.completed", "turn": {"id": "turn_old"}})
        app.final_text = "The price is 123."
        with self.assertRaises(StockAgentError):
            app.verify_execution("python3 helper.py")
        self.assertFalse(app.commands)

    def test_exact_command_matching_rejects_shell_substitutions(self):
        expected = "python3 /workspace/helper.py /workspace/request.json /workspace/result.json"
        self.assertTrue(matches_command("/bin/bash -lc " + shlex.quote(expected), expected))
        self.assertFalse(matches_command("echo " + shlex.quote(expected), expected))
        self.assertFalse(matches_command(expected + "; echo fabricated", expected))

    def test_result_from_another_request_is_rejected(self):
        with self.assertRaises(StockAgentError):
            verify_result({"requestId": "old", "operation": "search", "value": "Apple"}, "search", "Apple", "new")

    def test_unknown_request_id_cannot_call_openai(self):
        with self.assertRaises(StockAgentError):
            StockAgentService(self.folder / "demo", session_id=SESSION_ID).run("search", "Apple", "../../escape")
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
