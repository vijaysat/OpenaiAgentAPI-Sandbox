"""Independent OCI Responses API inference, authenticated only with an OCI key."""
import argparse
from datetime import datetime, timezone
import io
import json
import os
import re
import shlex
import sys
import time
import uuid

from .app import ROOT, PROJECT, State, load_dotenv, resource_id, save_json
from .transport import APIError, CurlClient, decode_sse, parse_headers


class OCIClient(CurlClient):
    def __init__(self, key, project, region):
        if not key or not project.startswith("ocid1.generativeaiproject."):
            raise APIError("Set OCI_GENAI_API_KEY and OCI_GENAI_PROJECT_ID to OCI credentials, separate from OpenAI keys.")
        if not re.fullmatch(r"[a-z]+-[a-z]+-[0-9]+", region):
            raise APIError("Invalid OCI_GENAI_REGION.")
        self.key = key
        self.script = ROOT / "scripts/oci_responses.sh"
        self.env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_EXECUTOR_API_KEY")}
        self.env.update(OCI_GENAI_API_KEY=key, OCI_GENAI_PROJECT_ID=project, OCI_GENAI_REGION=region)

    def error(self, code, headers, body, stderr):
        status, values = parse_headers(headers)
        request_id = values.get("opc-request-id", values.get("x-request-id", "unavailable"))
        return APIError(self.redact(f"OCI HTTP {status}; curl exit {code}; request ID {request_id}. "
                                   f"{(body or stderr)[:3000]}"), status=status)


def payload(model, prompt, previous_response_id=None):
    result = {"model": model, "input": prompt, "stream": True,
              "reasoning": {"effort": "medium"}, "text": {"verbosity": "medium"}}
    if previous_response_id:
        result["previous_response_id"] = resource_id(previous_response_id)
    # Astra does not accept temperature on the validated OCI path.
    return result


class Output:
    def __init__(self, client, folder, out=None, err=None):
        self.client, self.folder = client, folder
        self.out, self.err = out or sys.stdout, err or sys.stderr
        self.parts = {}
        self.response_id = None
        self.completed = False

    def event(self, event):
        fd = os.open(self.folder / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(self.client.redact(json.dumps(event)) + "\n")
        response = event.get("response") or {}
        if response.get("id"):
            self.response_id = response["id"]
            save_json(self.folder / "response.json", json.loads(self.client.redact(json.dumps(response))))
        kind = event.get("type", "unknown")
        if kind in ("error", "response.failed", "response.incomplete"):
            raise APIError(self.client.redact(json.dumps(event)))
        key = (event.get("item_id", event.get("output_index")), event.get("content_index", 0))
        if kind == "response.output_text.delta":
            text = event.get("delta", "")
            self.parts[key] = self.parts.get(key, "") + text
            print(self.client.redact(text), end="", file=self.out, flush=True)
        elif kind == "response.output_text.done":
            text, previous = event.get("text", ""), self.parts.get(key, "")
            print(self.client.redact(text[len(previous):] if text.startswith(previous) else text), file=self.out, flush=True)
            self.parts[key] = text
        else:
            print(f"[OCI event] {kind}", file=self.err, flush=True)
        if kind == "response.completed":
            if response.get("status") != "completed":
                raise APIError("OCI response ended without completed status.")
            self.completed = True
            return True
        return False


def stream_local(client, body, output, timeout):
    with client.stream("POST", "/responses", body, timeout=timeout) as stream:
        for event in stream:
            if output.event(event):
                return
    raise APIError("OCI stream ended before response.completed. Inspect the saved response ID before retrying.")


def stream_in_sandbox(client, body, output, timeout, session_id):
    """Run the same curl transport inside an existing sandbox; poll its output."""
    from .oci_runtime import OCIRuntime
    state = State(ROOT / ".geniagent", PROJECT)
    runtime = OCIRuntime(state, lambda text: print(client.redact(text), file=sys.stderr, flush=True))
    record = state.load(runtime.filename(resource_id(session_id)))
    if not record.get("sandbox_id") or record.get("deleted"):
        raise APIError("No retained OCI sandbox exists for that session.")
    sandbox_id = record["sandbox_id"]
    sandbox = runtime.call("get_sandbox", sandbox_id).data
    if sandbox.lifecycle_state != "RUNNING":
        raise APIError("The OCI sandbox is not running. Connect a new sandbox before retrying.")
    stem = "/workspace/oci-inference-" + uuid.uuid4().hex
    for suffix, data in ((".sh", client.script.read_bytes()), (".json", json.dumps(body).encode())):
        runtime.call("write_sandbox_file", sandbox_id, stem + suffix, io.BytesIO(data), opc_retry_token=str(uuid.uuid4()))
    command = "bash " + shlex.quote(stem + ".sh") + " POST /responses /dev/null " + str(timeout) + " < " + shlex.quote(stem + ".json")
    launched = runtime.call("run_sandbox_command", sandbox_id,
        runtime.models.RunSandboxCommandDetails(command=command, timeout=f"PT{timeout + 30}S",
            environment_variables=[runtime.models.EnvironmentVariable(name=name, value=client.env[name])
                                   for name in ("OCI_GENAI_API_KEY", "OCI_GENAI_PROJECT_ID", "OCI_GENAI_REGION")]),
        opc_retry_token=str(uuid.uuid4())).data
    save_json(output.folder / "sandbox-command.json", {"sandbox_id": sandbox_id, "command_id": launched.id,
                                                       "remote_script": stem + ".sh", "remote_payload": stem + ".json"})
    deadline = time.monotonic() + timeout + 45
    consumed = 0
    while time.monotonic() < deadline:
        command_state = runtime.call("get_sandbox_command", sandbox_id, launched.id).data
        snapshot = runtime.call("get_sandbox_command_output", sandbox_id, launched.id).data
        stdout = snapshot.stdout or ""
        # OCI command output is cumulative. Only parse new complete SSE frames.
        normalized = stdout.replace("\r\n", "\n")
        end = normalized.rfind("\n\n")
        end = end + 2 if end >= 0 else 0
        if end > consumed:
            for event in decode_sse(normalized[consumed:end].splitlines(keepends=True)):
                output.event(event)
            consumed = end
        if command_state.exit_code is not None:
            if command_state.exit_code != 0:
                raise APIError(client.redact(f"OCI sandbox curl failed with exit {command_state.exit_code}: "
                                             + (snapshot.stderr or stdout)[:3000]))
            # OCI's output API removes the final blank SSE separator. Flush only
            # after successful process exit; malformed/truncated JSON still fails.
            if consumed < len(normalized):
                for event in decode_sse((normalized[consumed:] + "\n\n").splitlines(keepends=True)):
                    output.event(event)
            if not output.completed:
                raise APIError("OCI sandbox stream ended without a completed response.")
            return
        time.sleep(1)
    raise APIError("OCI inference command timed out; its ID was retained. Input was not resubmitted.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("--model", help="Defaults to OCI_GENAI_MODEL or openai.gpt-6-astra")
    parser.add_argument("--previous-response-id", help="Continue an OCI response; never use an OpenAI session ID")
    parser.add_argument("--sandbox-session-id", help="Run curl inside this session's OCI sandbox instead of locally")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")
    folder = ROOT / ".geniagent" / "oci-inference" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    report = {"provider": "oci", "status": "running", "execution": "oci_sandbox" if args.sandbox_session_id else "local"}
    client = None
    output = None
    try:
        if args.timeout < 1 or not args.prompt.strip():
            raise APIError("Use a positive timeout and a nonempty prompt.")
        region = os.environ.get("OCI_GENAI_REGION", "us-chicago-1")
        project = os.environ.get("OCI_GENAI_PROJECT_ID", "")
        model = args.model or os.environ.get("OCI_GENAI_MODEL", "openai.gpt-6-astra")
        client = OCIClient(os.environ.get("OCI_GENAI_API_KEY", "").strip(), project, region)
        report.update(project_id=project, model=model, endpoint=f"https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1/responses")
        save_json(folder / "report.json", report)
        output = Output(client, folder)
        body = payload(model, args.prompt, args.previous_response_id)
        if args.sandbox_session_id:
            stream_in_sandbox(client, body, output, args.timeout, args.sandbox_session_id)
        else:
            stream_local(client, body, output, args.timeout)
        report["status"] = "completed"
        return 0
    except (APIError, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = client.redact(exc) if client else str(exc)
        print(report["error"], file=sys.stderr)
        return 1
    finally:
        report["response_id"] = output.response_id if output else None
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_json(folder / "report.json", report)
        print(f"OCI inference report: {folder / 'report.json'}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
