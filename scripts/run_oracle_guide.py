"""Run Oracle's brief.txt -> plan.md workflow in an existing OCI-backed session."""
import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geniagent.app import Application, PROJECT, State, load_dotenv, resource_id, save_json
from geniagent.oci_runtime import OCIRuntime
from geniagent.transport import APIError, CurlClient

BRIEF = "Migrate a synchronous Python service to async without changing its API.\n"
BRIEF_PATH = "/workspace/brief.txt"
PLAN_PATH = "/workspace/plan.md"
PROMPT = "Read /workspace/brief.txt and write a five-step migration plan to /workspace/plan.md. Read the file back and report what you actually wrote."
FOLLOWUP = "Add a section headed '## Rollback' to /workspace/plan.md, keeping the original five steps. Read the updated file and summarize the rollback procedure."


def read_text(runtime, sandbox_id, path):
    content = runtime.call("read_sandbox_file", sandbox_id, path).data.content
    if isinstance(content, bytes):
        return content.decode("utf-8")
    if isinstance(content, str):
        return content
    raise APIError("OCI returned an unsupported file response.")


def prepare_brief(runtime, sandbox_id):
    # A retry after a billing failure may reuse the same brief. Preserve other files.
    try:
        existing = read_text(runtime, sandbox_id, BRIEF_PATH)
    except APIError as exc:
        if exc.status != 404:
            raise
    else:
        if existing != BRIEF:
            raise APIError("/workspace/brief.txt already contains different content. Preserve it before running this example.")
    try:
        read_text(runtime, sandbox_id, PLAN_PATH)
    except APIError as exc:
        if exc.status != 404:
            raise
    else:
        raise APIError("/workspace/plan.md already exists. Download and rename it before starting another guide run.")
    runtime.call("write_sandbox_file", sandbox_id, BRIEF_PATH, io.BytesIO(BRIEF.encode()),
                 opc_retry_token=str(uuid.uuid4()))
    if read_text(runtime, sandbox_id, BRIEF_PATH) != BRIEF:
        raise APIError("OCI brief.txt readback did not match the uploaded content.")


def save_text(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)


def wait_idle(app):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        session = app.client.request("GET", f"/agents/sessions/{app.session_id}")
        if session.get("status") == "idle":
            return
        if session.get("status") == "failed":
            raise APIError("Session failed after the turn completed.")
        time.sleep(1)
    raise APIError("Session did not become idle. Inspect it before sending further input.")


def run(app, runtime, session_id, folder, *, follow_up=False, prepare_only=False):
    report = {"session_id": session_id, "status": "running", "brief_verified": False,
              "initial_turn_completed": False, "followup_turn_completed": False}
    app.session_id = resource_id(session_id)

    def save():
        save_json(folder / "report.json", json.loads(app.client.redact(json.dumps(report))))

    save()
    try:
        session, _, _ = app.snapshot()
        if session.get("status") != "idle":
            raise APIError("The session must be idle before starting the guide workflow. Inspect or resume it first.")
        environment = session.get("environment") or {}
        if environment.get("type") != "self_hosted" or environment.get("workspace_directory") != "/workspace":
            raise APIError("Select the existing OCI self-hosted session with workspace_directory /workspace.")
        environment_id = resource_id(environment.get("id"))
        current = app.client.request("GET", f"/agents/environments/{environment_id}")
        if current.get("status") != "connected":
            raise APIError("The executor is disconnected. Run scripts/connect_oci_session.py first.")
        runtime.health(session_id)
        record = app.state.load(runtime.filename(session_id))
        if record.get("deleted") or not record.get("sandbox_id") or record.get("environment_id") != environment_id:
            raise APIError("No matching live OCI sandbox mapping exists for this environment.")
        sandbox_id = report["sandbox_id"] = record["sandbox_id"]
        report["agent_id"] = (session.get("agent") or {}).get("id")
        prepare_brief(runtime, sandbox_id)
        report["brief_verified"] = True
        save_text(folder / "brief.txt", BRIEF)
        save()
        app.info("OCI upload and readback verified: /workspace/brief.txt.")
        if prepare_only:
            report["status"] = "prepared"
            return True
        app.continue_session(session_id, PROMPT)
        report["initial_turn_completed"] = True
        plan = read_text(runtime, sandbox_id, PLAN_PATH)
        if not plan.strip():
            raise APIError("The agent turn completed but OCI plan.md is empty.")
        save_text(folder / "plan.md", app.client.redact(plan))
        report["plan_verified"] = True
        save()
        app.info(f"Read plan.md back through OCI and saved {folder / 'plan.md'}.")
        if follow_up:
            wait_idle(app)
            follow = Application(app.client, app.state, app.timeout, out=app.out, err=app.err)
            follow.continue_session(session_id, FOLLOWUP)
            report["followup_turn_completed"] = True
            updated = read_text(runtime, sandbox_id, PLAN_PATH)
            if "## rollback" not in updated.casefold() or updated == plan:
                raise APIError("The follow-up completed without the requested rollback section in OCI plan.md.")
            save_text(folder / "plan-with-rollback.md", app.client.redact(updated))
            report["rollback_verified"] = True
        report["status"] = "passed"
        return True
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc) if isinstance(exc, APIError) else type(exc).__name__
        app.info(report["error"])
        return False
    finally:
        try:
            session, _, turns = app.snapshot()
            report["session_status"] = session.get("status")
            if turns:
                report["last_turn"] = {key: turns[-1].get(key) for key in ("id", "status", "error")}
        except APIError as exc:
            report["snapshot_error"] = str(exc)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        app.info(f"Guide report: {folder / 'report.json'}")
        app.info("Existing session and OCI compute retained; this command does not provision or delete resources.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", help="Defaults to the latest saved session")
    parser.add_argument("--follow-up", action="store_true", help="Add and verify the guide's rollback section")
    parser.add_argument("--prepare-only", action="store_true", help="Verify OCI file upload/readback without starting a model turn")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if os.environ.get("OPENAI_PROJECT_ID", PROJECT) != PROJECT or args.timeout < 1:
        parser.error("Use the configured OpenAI project and a positive timeout.")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        parser.error("Set OPENAI_API_KEY in this project's .env.")
    state = State(ROOT / ".geniagent", PROJECT)
    client = CurlClient(key, PROJECT)
    app = Application(client, state, args.timeout)
    try:
        with state.lock():
            session_id = state.session(args.session_id)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
            folder = state.folder / "guide-runs" / stamp
            return 0 if run(app, OCIRuntime(state, app.info), session_id, folder,
                            follow_up=args.follow_up, prepare_only=args.prepare_only) else 1
    except APIError as exc:
        app.info(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
