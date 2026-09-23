"""Continue one existing OpenAI session and execute stock lookups in its OCI sandbox."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shlex
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from geniagent.app import Application, PROJECT, State, load_dotenv, resource_id
from geniagent.oci_runtime import OCIRuntime
from geniagent.transport import APIError, CurlClient
from .provider import SYMBOL

DEFAULT_SESSION_ID = "sess_09f68edbca8dff11006ab2fd2957b081939db122cb9ce9cccb"


class StockAgentError(Exception):
    def __init__(self, message, status=502, agent=None):
        super().__init__(message)
        # OCI transport failures can carry "unknown" rather than an HTTP code.
        self.status = status if type(status) is int and 400 <= status <= 599 else 502
        self.agent = agent


def read_remote(runtime, sandbox_id, path):
    content = runtime.call("read_sandbox_file", sandbox_id, path).data.content
    if isinstance(content, bytes):
        return content.decode("utf-8")
    if isinstance(content, str):
        return content
    raise StockAgentError("OCI returned an unreadable stock result.")


def helper_source():
    folder = Path(__file__).parent
    return (folder / "provider.py").read_text() + "\n" + (folder / "sandbox_runner.py").read_text()


def matches_command(actual, expected):
    """Accept the executor's usual shell wrapper, without accepting extra shell actions."""
    try:
        words = shlex.split(actual)
        if len(words) == 3 and words[0] in ("/bin/bash", "bash", "/bin/sh", "sh") and words[1] in ("-lc", "-c"):
            words = shlex.split(words[2])
        if words[:3] == ["cd", "/workspace", "&&"]:
            words = words[3:]
        return words == shlex.split(expected)
    except (ValueError, TypeError):
        return False


class SessionApplication(Application):
    def __init__(self, client, state, **kwargs):
        super().__init__(client, state, out=io.StringIO(), err=io.StringIO(), **kwargs)
        self.completed = False
        self.final_text = ""
        self.event_types = []
        self.commands = {}
        self.model = None
        self.agent_id = None
        self.sandbox_id = None

    def handle_event(self, event):
        turn = event.get("turn") or {}
        item = event.get("item") or {}
        turn_id = turn.get("id") or event.get("turn_id") or item.get("turn_id")
        if turn_id in self.ignored_turns:
            return super().handle_event(event)
        kind = event.get("type", "unknown")
        if kind not in self.event_types:
            self.event_types.append(kind)
        if kind == "agent.session.turn.item.done":
            if item.get("type") == "command_execution":
                self.commands[item.get("id")] = item
            if item.get("role") == "assistant" and item.get("phase") == "final_answer":
                self.final_text = "\n".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "output_text")
        done = super().handle_event(event)
        if done:
            self.completed = True
            self.turn_id = turn.get("id") or event.get("turn_id")
        return done

    def evidence(self):
        return {"agentId": self.agent_id, "sessionId": self.session_id, "turnId": self.turn_id,
                "model": self.model or "Session model", "sandboxId": self.sandbox_id,
                "execution": "OCI sandbox", "status": "completed" if self.completed else "incomplete",
                "output": self.final_text or self.out.getvalue().strip(),
                "tools": [{"name": "command_execution", "callId": item.get("id"),
                           "success": item.get("exit_code") == 0 and item.get("status") == "completed"}
                          for item in self.commands.values()], "events": self.event_types}

    def verify_execution(self, command):
        if not self.completed or not any(
            item.get("exit_code") == 0 and item.get("status") == "completed"
            and item.get("turn_id", self.turn_id) == self.turn_id
            and matches_command(item.get("command"), command) for item in self.commands.values()
        ):
            raise StockAgentError("The session did not complete the requested OCI stock command. No data was fabricated.", agent=self.evidence())


def verify_result(envelope, operation, value, request_id):
    if not isinstance(envelope, dict) or any(envelope.get(k) != v for k, v in {
        "operation": operation, "value": value, "requestId": request_id,
    }.items()):
        raise StockAgentError("The OCI result does not match this stock request.")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or payload.get("source") != "Yahoo Finance":
        raise StockAgentError("The OCI helper returned invalid stock data.")
    if operation == "search":
        rows = payload.get("results")
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k] for k in ("symbol", "name", "exchange")) for row in rows):
            raise StockAgentError("The OCI helper returned invalid listings.")
    else:
        price = payload.get("price")
        if payload.get("symbol", "").upper() != value.upper() or not isinstance(price, (float, int)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0 or not payload.get("currency") or not payload.get("asOf"):
            raise StockAgentError("The OCI helper returned an invalid quote.")
    return payload


class StockAgentService:
    def __init__(self, folder=None, session_id=DEFAULT_SESSION_ID):
        self.folder = folder or ROOT / "stock-demo" / ".agent-state"
        self.session_id = resource_id(session_id)

    def run(self, operation, value, request_id):
        if operation not in ("search", "quote") or not isinstance(value, str):
            raise StockAgentError("Expected a search or quote operation and a string value.", 400)
        value = value.strip()
        if operation == "search" and (not 2 <= len(value) <= 80 or any(ord(c) < 32 for c in value)):
            raise StockAgentError("Enter a company name or symbol between 2 and 80 characters.", 400)
        if operation == "quote" and not SYMBOL.fullmatch(value):
            raise StockAgentError("Select a valid stock symbol.", 400)
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise StockAgentError("A valid requestId is required.", 400) from None
        load_dotenv(ROOT / ".env")
        if os.environ.get("OPENAI_PROJECT_ID", PROJECT) != PROJECT:
            raise StockAgentError("Use this workspace's configured OpenAI project.", 503)
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise StockAgentError("Set OPENAI_API_KEY in the workspace .env.", 503)
        client = CurlClient(key, PROJECT)
        state = State(self.folder / "sessions" / self.session_id / "requests" / request_id, PROJECT)
        app = SessionApplication(client, state, timeout=120)
        app.remember_session(self.session_id)
        shared_state = State(ROOT / ".geniagent", PROJECT)
        try:
            with state.lock(), shared_state.lock():
                previous = state.load("report.json")
                if previous:
                    if previous.get("operation") != operation or previous.get("value") != value:
                        raise StockAgentError("This requestId belongs to a different lookup.", 409)
                    if previous.get("status") == "completed":
                        return previous["response"]
                    raise StockAgentError("This request was already submitted. Inspect its saved session before retrying.", 409, previous.get("agent"))
                report = {"operation": operation, "value": value, "requestId": request_id,
                          "status": "checking_session", "agent": app.evidence()}
                state.save("report.json", report)
                try:
                    session = client.request("GET", f"/agents/sessions/{self.session_id}")
                    app.agent_id = (session.get("agent") or {}).get("id")
                    app.model = (session.get("agent") or {}).get("model")
                    if session.get("id") != self.session_id or session.get("status") != "idle":
                        raise StockAgentError("The selected session is not idle. Inspect or resume its active turn before sending another request.", 409)
                    environment = session.get("environment") or {}
                    if environment.get("type") != "self_hosted" or environment.get("workspace_directory") != "/workspace":
                        raise StockAgentError("This demo requires the selected session's OCI /workspace environment.")
                    env_id = resource_id(environment.get("id"))
                    env = client.request("GET", f"/agents/environments/{env_id}")
                    if env.get("status") != "connected":
                        raise StockAgentError("The selected session's OCI executor is disconnected. Reconnect this same session before looking up stocks.", 409)
                    runtime = OCIRuntime(shared_state, app.info)
                    runtime.health(self.session_id)
                    mapping = shared_state.load(runtime.filename(self.session_id))
                    if mapping.get("deleted") or not mapping.get("sandbox_id") or mapping.get("environment_id") != env_id:
                        raise StockAgentError("No matching OCI sandbox is recorded for this session.")
                    sandbox_id = app.sandbox_id = mapping["sandbox_id"]
                    source = helper_source()
                    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
                    script_path = f"/workspace/stock_demo_{digest}.py"
                    request_path = f"/workspace/stock_request_{request_id}.json"
                    output_path = f"/workspace/stock_result_{request_id}.json"
                    request = {"operation": operation, "value": value, "requestId": request_id}
                    for path, content in ((script_path, source), (request_path, json.dumps(request))):
                        runtime.call("write_sandbox_file", sandbox_id, path, io.BytesIO(content.encode()), opc_retry_token=str(uuid.uuid4()))
                        if read_remote(runtime, sandbox_id, path) != content:
                            raise StockAgentError("The OCI helper upload did not pass readback verification.")
                    command = shlex.join(["python3", script_path, request_path, output_path])
                    report.update(status="running", agent=app.evidence(), command=command, outputPath=output_path)
                    state.save("report.json", report)
                    prompt = (
                        "Run this exact command once using your workspace command_execution tool:\n" + command +
                        "\nIt fetches public stock data and writes a request-specific JSON result. Do not modify the helper, "
                        "request, or output files, and do not substitute remembered prices. After it exits successfully, "
                        "briefly summarize its actual output. If it fails, report the error. This is a new stock-demo task; "
                        "previous requests in this conversation are history, not instructions for this request."
                    )
                    app.continue_session(self.session_id, prompt)
                    app.verify_execution(command)
                    if read_remote(runtime, sandbox_id, script_path) != source:
                        raise StockAgentError("The uploaded stock helper changed during execution.")
                    envelope = json.loads(read_remote(runtime, sandbox_id, output_path))
                    payload = verify_result(envelope, operation, value, request_id)
                    response = {**(payload if operation == "search" else {"quote": payload}), "agent": app.evidence()}
                    report.update(status="completed", response=response, agent=app.evidence())
                    state.save("report.json", report)
                    return response
                except (APIError, StockAgentError, ValueError) as exc:
                    evidence = app.evidence()
                    report.update(status="failed", error=client.redact(str(exc)), agent=evidence)
                    state.save("report.json", report)
                    raise StockAgentError(client.redact(str(exc)), getattr(exc, "status", 502) or 502, evidence) from None
        except APIError as exc:
            raise StockAgentError(client.redact(str(exc)), 409, app.evidence()) from None
