"""Use the checked-in shell/curl transport, including for SSE streams."""
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time


class APIError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def parse_headers(path):
    blocks = re.split(r"\r?\n\r?\n", Path(path).read_text(errors="replace"))
    result = (0, {})
    for block in blocks[:-1]:
        lines = block.splitlines()
        if not lines or not lines[0].startswith("HTTP/"):
            continue
        status = int(lines[0].split()[1])
        headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
        result = status, {k.lower(): v.strip() for k, v in headers.items()}
    return result


def decode_sse(lines):
    """Parse complete SSE frames, including comments, CRLF and multiline data."""
    data, kind = [], None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                payload = "\n".join(data)
                if payload != "[DONE]":
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise APIError("Invalid JSON in the event stream.") from exc
                    if not isinstance(event, dict):
                        raise APIError("Expected an object in the event stream.")
                    if kind:
                        event.setdefault("type", kind)
                    yield event
            data, kind = [], None
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
        elif line.startswith("event:"):
            kind = line[6:].strip()
        # Comments and SSE IDs need no action: this API does not replay events.
    if data:
        raise APIError("Event stream ended in an incomplete frame.")


class CurlClient:
    def __init__(self, key, project, base_url="https://api.openai.com/v1"):
        self.key = key
        self.script = Path(__file__).resolve().parents[1] / "scripts/api.sh"
        self.env = dict(os.environ, OPENAI_API_KEY=key, OPENAI_PROJECT_ID=project,
                        GENIAGENT_API_BASE=base_url)

    def redact(self, value):
        value = str(value)
        if self.key:
            value = value.replace(self.key, "[REDACTED]")
        executor_key = os.environ.get("OPENAI_EXECUTOR_API_KEY")
        if executor_key:
            value = value.replace(executor_key, "[REDACTED]")
        return re.sub(r"sk-[A-Za-z0-9_.*-]+", "[REDACTED]", value)

    def command(self, method, path, headers, timeout):
        return ["bash", str(self.script), method, path, str(headers), str(timeout)]

    def error(self, code, headers, body, stderr):
        status, values = parse_headers(headers)
        request_id = values.get("x-request-id", "unavailable")
        hints = {
            401: "Check OPENAI_API_KEY and the selected project.",
            403: "Check Agents API access and api.agents.read/write plus api.responses.write permissions.",
            404: "Check Agents API availability and the resource ID in this project.",
            429: "Check quota or rate limits before retrying.",
        }
        detail = body.strip() or stderr.strip() or "No response body."
        return APIError(self.redact(
            f"HTTP {status or 'unavailable'}; curl exit {code}; request ID {request_id}. "
            f"{hints.get(status, '')}\n{detail[:3000]}"
        ), status=status)

    def request(self, method, path, body=None):
        with tempfile.TemporaryDirectory(prefix="geniagent-http-") as folder:
            headers = Path(folder) / "headers"
            headers.touch()
            payload = json.dumps(body) if body is not None else ""
            try:
                result = subprocess.run(self.command(method, path, headers, 60),
                                        input=payload, capture_output=True, text=True,
                                        env=self.env, timeout=65)
            except subprocess.TimeoutExpired as exc:
                raise APIError("HTTP request timed out; inspect saved state before retrying a write.") from exc
            status, _ = parse_headers(headers)
            if result.returncode or not 200 <= status < 300:
                raise self.error(result.returncode, headers, result.stdout, result.stderr)
            if not result.stdout.strip():
                return {}
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise APIError("The API returned an invalid JSON response.") from exc

    def stream(self, method, path, body=None, timeout=900):
        return CurlStream(self, method, path, body, timeout)


class CurlStream:
    def __init__(self, client, method, path, body, timeout):
        self.client, self.method, self.path = client, method, path
        self.body, self.timeout = body, timeout
        self.lines = queue.Queue()

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="geniagent-stream-")
        self.headers = Path(self.temp.name) / "headers"
        self.headers.touch()
        self.stderr = tempfile.TemporaryFile(mode="w+")
        self.payload = tempfile.TemporaryFile(mode="w+")
        if self.body is not None:
            json.dump(self.body, self.payload)
        self.payload.seek(0)
        self.process = subprocess.Popen(
            self.client.command(self.method, self.path, self.headers, self.timeout),
            stdin=self.payload, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding="utf-8", errors="replace", env=self.client.env,
            start_new_session=True,
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        self.deadline = time.monotonic() + self.timeout + 5
        try:
            # HTTP headers establish subscription readiness before follow-up POSTs.
            header_deadline = min(self.deadline, time.monotonic() + 60)
            while time.monotonic() < header_deadline:
                status, headers = parse_headers(self.headers)
                if status >= 200 and "content-type" in headers:
                    if status < 300 and "text/event-stream" in headers["content-type"]:
                        return self
                    break
                if self.process.poll() is not None:
                    break
                time.sleep(0.02)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise APIError("Timed out opening the event stream; inspect the session before retrying.") from exc
            body = "".join(self._raw_lines())
            self.stderr.seek(0)
            raise self.client.error(self.process.returncode, self.headers, body, self.stderr.read())
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def _raw_lines(self):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise APIError("Session stream deadline exceeded. Use resume to recover saved work.")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise APIError("Session stream deadline exceeded. Use resume to recover saved work.") from exc
            if line is None:
                return
            yield line

    def __iter__(self):
        yield from decode_sse(self._raw_lines())
        code = self.process.wait(timeout=5)
        if code:
            self.stderr.seek(0)
            raise self.client.error(code, self.headers, "", self.stderr.read())

    def __exit__(self, *args):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.thread.join(timeout=3)
        self.process.stdout.close()
        self.payload.close()
        self.stderr.close()
        self.temp.cleanup()
