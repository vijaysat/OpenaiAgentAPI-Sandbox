"""Durable agent/session CLI. Every HTTP call goes through scripts/api.sh."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .transport import APIError, CurlClient

ROOT = Path(__file__).resolve().parents[1]
INITIAL_MESSAGE = (
    "Introduce yourself as GeniAgent-G. Call get_current_time for America/New_York "
    "and report its result. Then use Python in your workspace to calculate sum([10, 20, 30]), "
    "write the result to /workspace/outputs/startup-check.json, and read it back. "
    "Report the actual results and any errors."
)


def load_dotenv(path):
    """Read literal dotenv values without executing shell code; exported env wins."""
    if not path.exists():
        return
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # This client does not consume unrelated OCI or self-hosted settings.
        if key not in ("OPENAI_API_KEY", "OPENAI_PROJECT_ID", "OPENAI_EXECUTOR_API_KEY",
                       "OCI_SANDBOX_PROJECT_ID", "OCI_SANDBOX_PROFILE", "OCI_SANDBOX_REGION",
                       "OCI_SANDBOX_RUNTIME", "OCI_SANDBOX_SHAPE", "OCI_GENAI_API_KEY",
                       "OCI_GENAI_PROJECT_ID", "OCI_GENAI_REGION", "OCI_GENAI_MODEL"):
            continue
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise APIError(f"Invalid .env entry at line {line_number}.")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise APIError(f"Unclosed .env quote at line {line_number}.")
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        os.environ.setdefault(key, value)


def configured_project():
    """Resolve the explicitly selected OpenAI project after loading dotenv."""
    project = os.environ.get("OPENAI_PROJECT_ID", "")
    if not project.strip():
        raise APIError("Set OPENAI_PROJECT_ID in the project .env or export it in your shell.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        raise APIError("Invalid OPENAI_PROJECT_ID; use the exact project ID without whitespace.")
    return project


def resource_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise APIError("Invalid resource ID; use the exact ID returned by the API.")
    return value


def read_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise APIError(f"Cannot read JSON in {path}.") from exc


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class State:
    def __init__(self, folder, project):
        self.folder, self.project = folder, project
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)

    @contextmanager
    def lock(self):
        # One local writer prevents overlapping tool results and agent creation.
        fd = os.open(self.folder / "client.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise APIError("Another GeniAgent client is running in this state directory.") from exc
            yield

    def load(self, name):
        value = read_json(self.folder / name, {})
        if value and value.get("project_id") != self.project:
            raise APIError("Saved state belongs to another OpenAI project.")
        return value

    def save(self, name, value):
        save_json(self.folder / name, dict(value, project_id=self.project))

    def session(self, session_id=None):
        if session_id:
            return resource_id(session_id)
        value = self.load("latest-session.json").get("session_id")
        if not value:
            raise APIError("No saved session. Run start first or supply --session-id.")
        return resource_id(value)


def execute_tool(action):
    """Only explicitly registered application functions can run on this machine."""
    result = {"type": "agent.session.input.tool_result",
              "turn_id": action["turn_id"], "call_id": action["call_id"]}
    try:
        if action.get("name") != "get_current_time":
            raise ValueError("Unknown function. Only get_current_time is registered.")
        args = action.get("arguments")
        if isinstance(args, str):
            args = json.loads(args)
        if not isinstance(args, dict) or set(args) != {"timezone"}:
            raise ValueError("Expected exactly one argument: timezone.")
        zone = args["timezone"]
        if not isinstance(zone, str) or not 1 <= len(zone) <= 100:
            raise ValueError("timezone must be an IANA timezone string.")
        output = {"timezone": zone, "current_time": datetime.now(ZoneInfo(zone)).isoformat()}
        result.update(success=True, output=json.dumps(output))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        result.update(success=False, error="Unknown function or invalid arguments. Use get_current_time with a valid IANA timezone.")
    return result


class Application:
    def __init__(self, client, state, timeout=900, out=None, err=None):
        self.client, self.state, self.timeout = client, state, timeout
        self.out, self.err = out or sys.stdout, err or sys.stderr
        self.session_id = None
        self.turn_id = None
        self.parts = {}
        self.final_items = set()
        self.recovered_parts = set()
        self.seen_events = set()
        self.ignored_turns = set()

    def info(self, message):
        message = self.client.redact(message)
        print(message, file=self.err, flush=True)
        if self.session_id:
            self.append_log("client", {"timestamp": datetime.now(timezone.utc).isoformat(),
                                       "message": message})

    def remember_session(self, session_id):
        self.session_id = resource_id(session_id)
        self.state.save("latest-session.json", {"session_id": self.session_id})

    def agent(self, definition, new=False, agent_id=None):
        if agent_id:
            agent_id = resource_id(agent_id)
            self.client.request("GET", f"/agents/{agent_id}")
            return agent_id
        fingerprint = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        cached = self.state.load("agent.json")
        if cached and not new:
            if cached.get("fingerprint") != fingerprint:
                raise APIError("Agent configuration changed. Run start --new-agent to save a new definition.")
            agent_id = resource_id(cached["agent_id"])
            self.client.request("GET", f"/agents/{agent_id}")
            self.info(f"Reusing agent {agent_id}")
            return agent_id
        agent = self.client.request("POST", "/agents", definition)
        agent_id = resource_id(agent.get("id"))
        self.state.save("agent.json", {"agent_id": agent_id, "fingerprint": fingerprint})
        self.info(f"Created reusable agent {agent_id}")
        return agent_id

    def start(self, definition, environment, message, new=False, agent_id=None):
        agent_id = self.agent(definition, new, agent_id)
        payload = {"agent_id": agent_id, "environment": environment,
                   "input": message, "stream": True}
        self.info("Starting session and streaming the initial message…")
        with self.client.stream("POST", "/agents/sessions", payload, self.timeout) as stream:
            self.consume(stream)

    def start_oci(self, runtime, definition, message, new=False, agent_id=None):
        runtime.preflight()
        agent_id = self.agent(definition, new, agent_id)
        session = self.client.request("POST", "/agents/sessions", {
            "agent_id": agent_id,
            "environment": {"type": "self_hosted", "workspace_directory": "/workspace"},
        })
        self.remember_session(session.get("id"))
        self.info(f"Session {self.session_id}")
        with self.client.stream("GET", self.events_path() + "?stream=true", timeout=self.timeout) as stream:
            runtime.provision(session)
            deadline = time.monotonic() + 120
            environment_id = resource_id(session["environment"]["id"])
            while time.monotonic() < deadline:
                environment = self.client.request("GET", f"/agents/environments/{environment_id}")
                status = environment.get("status")
                if status == "connected":
                    break
                if status in ("failed", "expired"):
                    raise APIError("OpenAI reported an environment connection failure.")
                runtime.health(self.session_id)
                time.sleep(2)
            else:
                raise APIError("OCI executor did not connect within 120 seconds. Use inspect and delete to reconcile.")
            self.info("OpenAI confirmed the OCI environment is connected; sending initial message.")
            self.client.request("POST", self.events_path(), {"events": [{
                "type": "agent.session.input.message",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": message}]}],
            }]})
            self.consume(stream)

    def events_path(self):
        return f"/agents/sessions/{self.session_id}/events"

    def paginated(self, path):
        after, seen = None, set()
        while True:
            query = {"order": "asc", "limit": 100}
            if after:
                query["after"] = after
            page = self.client.request("GET", f"{path}?{urlencode(query)}")
            data = page.get("data", [])
            yield from data
            if not page.get("has_more"):
                return
            after = page.get("last_id") or (data[-1].get("id") if data else None)
            if not after or after in seen:
                raise APIError("Invalid pagination cursor from the API.")
            seen.add(after)

    def snapshot(self):
        session = self.client.request("GET", f"/agents/sessions/{self.session_id}")
        items = list(self.paginated(f"/agents/sessions/{self.session_id}/items"))
        turns = list(self.paginated(f"/agents/sessions/{self.session_id}/turns"))
        for name, value in (("session", session), ("items", items), ("turns", turns)):
            # Do not persist application credentials even if an upstream error echoes one.
            value = json.loads(self.client.redact(json.dumps(value)))
            save_json(self.state.folder / f"{self.session_id}-{name}.json", value)
        return session, items, turns

    def display_items(self, items):
        for item in items:
            if item.get("status") == "completed":
                self.final_items.add(item.get("id"))
            if item.get("role") == "assistant":
                for index, part in enumerate(item.get("content", [])):
                    if part.get("type") == "output_text" and part.get("text"):
                        key = (item.get("id"), index)
                        self.parts[key] = part["text"]
                        if item.get("status") != "completed":
                            # Buffered deltas can overlap a snapshot's partial text.
                            # Wait for authoritative done text to append its suffix.
                            self.recovered_parts.add(key)
                        print(self.client.redact(part["text"]), file=self.out, flush=True)

    def continue_session(self, session_id, message=None):
        self.remember_session(session_id)
        self.info(f"Session {session_id}; opening event stream…")
        # A background reader buffers new events during snapshot reconciliation.
        with self.client.stream("GET", self.events_path() + "?stream=true", timeout=self.timeout) as stream:
            self.info("Event stream opened; retrieving saved messages and turns…")
            session, items, turns = self.snapshot()
            self.info(f"Session status: {session.get('status')}; turns: {len(turns)}; items: {len(items)}.")
            if session.get("status") == "failed":
                raise APIError("Session failed: " + self.client.redact(json.dumps(session.get("error"))))
            if message is not None:
                if session.get("status") != "idle":
                    raise APIError("Session is not idle. Use resume to finish its current turn before sending a new message.")
                self.ignored_turns = {turn["id"] for turn in turns}
                self.info("Submitting user message…")
                self.client.request("POST", self.events_path(), {"events": [{
                    "type": "agent.session.input.message", "input": [{
                        "role": "user", "content": [{"type": "input_text", "text": message}]
                    }]
                }]})
                self.info("Message request accepted by the HTTP API; waiting for turn events. "
                          "This does not yet confirm a turn has started.")
            else:
                self.display_items(items)
                latest = turns[-1] if turns else None
                if latest:
                    self.turn_id = latest.get("id")
                    if latest.get("status") in ("failed", "cancelled"):
                        raise APIError("Last turn " + self.client.redact(json.dumps(latest)))
                    if latest.get("status") == "completed" and session.get("status") == "idle":
                        self.info("Recovered a completed turn from saved history.")
                        return
                if session.get("status") == "idle" and not latest:
                    raise APIError("Session is idle with no completed turn. Use send to submit a message.")
                self.handle_actions(session)
            self.consume(stream)

    def handle_actions(self, session):
        for action in session.get("required_actions") or []:
            if action.get("type") != "function_call":
                raise APIError(f"Unsupported required action: {action.get('type')}. "
                               "The execution environment must be connected before work can continue. "
                               "Inspect its connection and the retained OCI resource IDs when using --oci.")
            if not action.get("turn_id") or not action.get("call_id"):
                raise APIError("Function action is missing its turn_id or call_id.")
            identity = [self.session_id, action["turn_id"], action["call_id"]]
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            path = self.state.folder / "tool-results" / f"{digest}.json"
            saved = read_json(path)
            if saved is None:
                result = execute_tool(action)
                # Save before submitting so a reconnect reuses the same result.
                saved = {"identity": identity, "result": result}
                save_json(path, saved)
            result = saved["result"]
            self.info(f"[tool] {action.get('name')}: {'success' if result['success'] else 'error'}")
            self.client.request("POST", self.events_path(), {"events": [result]})

    def append_log(self, kind, value):
        if self.session_id:
            path = self.state.folder / f"{self.session_id}-{kind}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as handle:
                handle.write(self.client.redact(json.dumps(value, ensure_ascii=False)) + "\n")

    def record_event(self, event):
        self.append_log("events", event)

    def handle_event(self, event):
        kind = event.get("type", "unknown")
        session = event.get("session") or {}
        if not self.session_id:
            sid = session.get("id") or event.get("session_id")
            if sid:
                self.remember_session(sid)
                self.info(f"Session {self.session_id}")
        self.record_event(event)
        event_id = event.get("event_id")
        if event_id and event_id in self.seen_events:
            return False
        if event_id:
            self.seen_events.add(event_id)
        turn = event.get("turn") or {}
        turn_id = turn.get("id") or event.get("turn_id")
        subagent = turn.get("subagent_id") or event.get("subagent_id")
        if turn_id in self.ignored_turns:
            return False
        if kind == "agent.session.turn.started" and not subagent:
            self.turn_id = turn_id
        if kind.endswith("output_text.delta") or kind.endswith("output_text.done"):
            if event.get("item_id") in self.final_items:
                return False
            key = (event.get("item_id", event.get("output_index")), event.get("content_index", 0))
            previous = self.parts.get(key, "")
            if kind.endswith(".delta"):
                if key in self.recovered_parts:
                    return False
                text = event.get("delta", "")
                self.parts[key] = previous + text
                print(self.client.redact(text), end="", file=self.out, flush=True)
            else:
                text = event.get("text", "")
                suffix = text[len(previous):] if text.startswith(previous) else "\n" + text
                print(self.client.redact(suffix), file=self.out, flush=True)
                self.parts[key] = text
                self.recovered_parts.discard(key)
        else:
            self.info(f"[event] {kind}")
        if kind == "agent.session.requires_action":
            if not self.session_id:
                raise APIError("Required action arrived without a session ID.")
            current = self.client.request("GET", f"/agents/sessions/{self.session_id}")
            self.handle_actions(current)
        if kind in ("error", "agent.session.failed", "agent.session.environment.failed",
                    "agent.session.environment.disconnected"):
            raise APIError(self.client.redact(json.dumps(event)))
        if kind in ("agent.session.turn.failed", "agent.session.turn.cancelled") and not subagent:
            raise APIError(self.client.redact(json.dumps(event)))
        if kind == "agent.session.turn.completed" and not subagent:
            self.info("Turn completed. Review the reported tool results for task success.")
            return True
        return False

    def consume(self, stream):
        for event in stream:
            if self.handle_event(event):
                return
        raise APIError("Stream closed before the root turn completed. Use resume; input was not resent.")


def parser():
    result = argparse.ArgumentParser(description="GeniAgent-G — Agents HTTP API over curl")
    result.add_argument("--env-file", type=Path, default=ROOT / ".env")
    result.add_argument("--state-dir", type=Path, default=ROOT / ".geniagent")
    result.add_argument("--timeout", type=int, default=900, help="Maximum streaming seconds (default 900)")
    commands = result.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check configuration and API access without creating resources")
    check.add_argument("--oci", action="store_true", help="Also check OCI SDK, profile and executor-key configuration")
    start = commands.add_parser("start", help="Create/reuse agent and start a new session")
    start.add_argument("--message", default=INITIAL_MESSAGE)
    start.add_argument("--agent-config", type=Path, default=ROOT / "config/agent.json")
    start.add_argument("--environment-config", type=Path, default=ROOT / "config/environment.json")
    start.add_argument("--new-agent", action="store_true")
    start.add_argument("--agent-id")
    start.add_argument("--oci", action="store_true", help="Use an OCI sandbox as the self-hosted environment")
    for command in ("send", "resume", "inspect", "cancel", "delete"):
        sub = commands.add_parser(command)
        sub.add_argument("--session-id", help="Defaults to the latest saved session")
        if command == "send":
            sub.add_argument("message")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    client, app = None, None
    try:
        load_dotenv(args.env_file)
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise APIError("Set OPENAI_API_KEY in the project .env or export it in your shell.")
        project = configured_project()
        if not shutil.which("curl") or not shutil.which("bash"):
            raise APIError("Install curl 7.76+ and bash before running the application.")
        if args.timeout < 1:
            raise APIError("--timeout must be positive.")
        client = CurlClient(key, project)
        state = State(args.state_dir, project)
        app = Application(client, state, args.timeout)
        with state.lock():
            if args.command == "check":
                if args.oci:
                    from .oci_runtime import OCIRuntime
                    OCIRuntime(state, app.info).preflight()
                    app.info("OCI SDK/profile and executor-key configuration loaded; cloud access is not yet verified.")
                client.request("GET", "/agents?limit=1")
                app.info(f"Agents API read access verified for {project}. Inference and hosted execution require start.")
                return 0
            if args.command == "start":
                definition = read_json(args.agent_config)
                if not isinstance(definition, dict) or not definition.get("model"):
                    raise APIError("Agent JSON must include a model.")
                if not args.message.strip():
                    raise APIError("An initial message is required.")
                if args.oci:
                    from .oci_runtime import OCIRuntime
                    app.start_oci(OCIRuntime(state, app.info), definition, args.message, args.new_agent, args.agent_id)
                else:
                    environment = read_json(args.environment_config)
                    if isinstance(environment, dict) and environment.get("type") == "self_hosted":
                        raise APIError("For an OCI self-hosted environment, run .venv-oci/bin/python main.py start --oci.")
                    if not isinstance(environment, dict) or environment.get("type") not in ("openai_hosted", "none"):
                        raise APIError("Environment must be openai_hosted or none, or use --oci for OCI self-hosted execution.")
                    app.start(definition, environment, args.message, args.new_agent, args.agent_id)
            else:
                sid = state.session(args.session_id)
                app.session_id = sid
                if args.command in ("send", "resume"):
                    message = args.message if args.command == "send" else None
                    if message is not None and not message.strip():
                        raise APIError("Message cannot be empty.")
                    if state.load(f"{sid}-oci.json"):
                        from .oci_runtime import OCIRuntime
                        app.info("Checking OCI sandbox and executor…")
                        OCIRuntime(state, app.info).health(sid)
                        app.info("OCI sandbox and executor are running.")
                    app.continue_session(sid, message)
                else:
                    app.session_id = sid
                    if args.command == "inspect":
                        session, items, turns = app.snapshot()
                        print(client.redact(json.dumps({"session": session, "turns": turns, "items": items}, indent=2)))
                    elif args.command == "cancel":
                        client.request("POST", app.events_path(), {"events": [{"type": "agent.session.input.cancel"}]})
                        app.info("Cancellation requested. Use inspect to check the outcome.")
                    elif args.command == "delete":
                        errors = []
                        if state.load(f"{sid}-oci.json"):
                            try:
                                from .oci_runtime import OCIRuntime
                                errors.extend(OCIRuntime(state, app.info).cleanup(sid))
                            except APIError as exc:
                                errors.append(str(exc))
                        try:
                            client.request("DELETE", f"/agents/sessions/{sid}")
                        except APIError as exc:
                            if exc.status != 404:
                                errors.append(str(exc))
                        if errors:
                            raise APIError("Cleanup incomplete: " + "; ".join(errors))
                        if state.load("latest-session.json").get("session_id") == sid:
                            (state.folder / "latest-session.json").unlink()
                        app.info("Session deleted; sandbox cleanup may continue asynchronously.")
        return 0
    except KeyboardInterrupt:
        message = "Stopped following the stream. The remote turn may still be running. Use resume or cancel."
        if app:
            app.info(message)
        else:
            print(message, file=sys.stderr)
        return 130
    except (APIError, OSError, ValueError) as exc:
        message = client.redact(exc) if client else "Configuration error: " + str(exc)
        if app:
            app.info(f"Error: {message}")
        else:
            print(f"Error: {message}", file=sys.stderr)
        if app and app.session_id:
            print(f"Session retained: {app.session_id}. Run inspect or resume; do not resend uncertain input.", file=sys.stderr)
        elif args.command == "start":
            print("No session ID was received. If a write was sent, inspect the project before creating another resource.", file=sys.stderr)
        return 1
