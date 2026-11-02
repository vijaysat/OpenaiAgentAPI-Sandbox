"""Connect an existing Agents session to OCI; retain compute for interactive use."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geniagent.app import Application, configured_project, State, load_dotenv, resource_id
from geniagent.oci_runtime import OCIRuntime
from geniagent.transport import APIError, CurlClient
from scripts.run_temporary_project_test import wait_project


def prepare_replacement(runtime, session_id):
    """Never overwrite a provider mapping unless its sandbox is confirmed absent."""
    record = runtime.state.load(runtime.filename(session_id))
    if not record:
        return
    if not record.get("sandbox_id"):
        raise APIError("Previous sandbox creation has no returned ID. Reconcile its retry token before reconnecting.")
    try:
        runtime.call("get_sandbox", record["sandbox_id"])
    except APIError as exc:
        if exc.status != 404:
            raise
    else:
        raise APIError("A sandbox already exists for this session. Inspect or clean it up before creating a replacement.")
    runtime.state.save(f"{session_id}-oci-previous-{uuid.uuid4().hex[:8]}.json", record)


def connect(app, runtime, projects, models, session_id, policy_name):
    session_id = resource_id(session_id)
    session = app.client.request("GET", f"/agents/sessions/{session_id}")
    env = session.get("environment") or {}
    if env.get("type") != "self_hosted" or env.get("workspace_directory") != "/workspace":
        raise APIError("This connector requires a self_hosted session with workspace_directory /workspace.")
    if session.get("status") == "failed":
        raise APIError("The session has failed. Inspect its error before starting more compute.")
    environment_id = resource_id(env.get("id"))
    current = app.client.request("GET", f"/agents/environments/{environment_id}")
    if current.get("status") == "connected":
        app.info("OpenAI already reports this session's environment as connected.")
        return
    runtime.preflight()
    prepare_replacement(runtime, session_id)
    name = f"{session_id}-oci-project.json"
    record = app.state.load(name)
    if record and not record.get("deleted"):
        raise APIError("A previous connection attempt has a retained project. Use --cleanup before retrying.")
    source_id = os.environ["OCI_SANDBOX_PROJECT_ID"]
    source = projects.get_generative_ai_project(source_id).data
    policy = (models.SandboxAllowAllNetworkPolicy() if policy_name == "allow-all" else
              models.SandboxAllowNetworkPolicy(allowed_domains=[models.AllowedDomain(
                  hostnames=["registry.npmjs.org", "api.openai.com", "codex-cloud-environments.chatgpt.com"], ports=[443])]))
    record = {"session_id": session_id, "source_project_id": source_id,
              "oci_project_id": None, "network_policy": policy_name,
              "create_retry_token": str(uuid.uuid4()), "status": "creating"}
    app.state.save(name, record)
    app.info(f"Creating an isolated OCI project with {policy_name} egress for the existing session…")
    project = projects.create_generative_ai_project(models.CreateGenerativeAiProjectDetails(
        compartment_id=source.compartment_id,
        display_name=f"GeniAgent-G-connect-{session_id[-8:]}-{uuid.uuid4().hex[:6]}",
        description="Interactive Agents API sandbox connection; clean up using scripts/connect_oci_session.py --cleanup",
        sandbox_config=models.SandboxConfig(is_enabled=True,
            sandbox_outbound_networking_config=models.ManagedSandboxOutboundNetworkingConfig(network_policy=policy))),
        opc_retry_token=record["create_retry_token"]).data
    record["oci_project_id"] = project.id
    app.state.save(name, record)
    project = wait_project(projects, project.id, "ACTIVE")
    actual = project.sandbox_config.sandbox_outbound_networking_config
    import oci
    if actual.network_mode != "MANAGED" or oci.util.to_dict(actual.network_policy) != oci.util.to_dict(policy):
        raise APIError("The new OCI project's network policy does not match the requested policy.")
    app.remember_session(session_id)
    try:
        os.environ["OCI_SANDBOX_PROJECT_ID"] = project.id
        # Subscribe before starting the executor so connection events are captured.
        with app.client.stream("GET", app.events_path() + "?stream=true", timeout=900) as stream:
            runtime.provision(session)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                current = app.client.request("GET", f"/agents/environments/{environment_id}")
                if current.get("status") == "connected":
                    break
                if current.get("status") in ("failed", "expired"):
                    raise APIError("OpenAI reported that the environment could not connect.")
                runtime.health(session_id)
                time.sleep(2)
            else:
                raise APIError("Executor connection timed out. IDs are retained for cleanup.")
            # No user input is submitted or replayed by this connection command.
            for event in stream:
                app.record_event(event)
                app.state.save(f"{session_id}-connection-event.json", json.loads(app.client.redact(json.dumps(event))))
                app.info(f"[event] {event.get('type')}")
                if event.get("type") == "agent.session.environment.connected":
                    break
            else:
                raise APIError("Connection stream ended before its connected event.")
        latest, items, turns = app.snapshot()
        record.update(status="connected", environment_id=environment_id,
                      connected_at=datetime.now(timezone.utc).isoformat(),
                      session_status=latest.get("status"),
                      required_actions=latest.get("required_actions", []),
                      sandbox_id=app.state.load(runtime.filename(session_id)).get("sandbox_id"))
        app.state.save(name, record)
        app.info("CONNECTED: OpenAI confirmed the environment connection. OCI compute is retained for up to 30 minutes.")
        app.info(f"Session status: {latest.get('status')}; turns: {len(turns)}; items: {len(items)}.")
        app.info(f"Cleanup: .venv-oci/bin/python scripts/connect_oci_session.py --session-id {session_id} --cleanup")
    finally:
        os.environ["OCI_SANDBOX_PROJECT_ID"] = source_id


def cleanup(app, runtime, projects, session_id):
    errors = runtime.cleanup(session_id)
    runtime_record = app.state.load(runtime.filename(session_id))
    if runtime_record.get("sandbox_id"):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                runtime.call("get_sandbox", runtime_record["sandbox_id"])
            except APIError as exc:
                if exc.status == 404:
                    break
                raise
            time.sleep(3)
        else:
            raise APIError("Sandbox deletion is not yet confirmed; the project was retained.")
    name = f"{session_id}-oci-project.json"
    record = app.state.load(name)
    if record and not record.get("deleted"):
        if not record.get("oci_project_id"):
            raise APIError("Project creation has no returned ID. Reconcile its saved retry token in OCI.")
        deadline = time.monotonic() + 120
        while True:
            try:
                projects.delete_generative_ai_project(record["oci_project_id"])
                break
            except Exception as exc:
                if getattr(exc, "status", None) == 404:
                    break
                if getattr(exc, "status", None) != 409 or time.monotonic() >= deadline:
                    raise
                time.sleep(3)
        wait_project(projects, record["oci_project_id"], "DELETED", timeout=120)
        record.update(deleted=True, status="deleted")
        app.state.save(name, record)
    if errors:
        raise APIError("; ".join(errors))
    app.info("OCI sandbox and connection project are deleted. OpenAI session history is retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--network-policy", choices=("allow-all", "allowlist"), default="allowlist")
    parser.add_argument("--cleanup", action="store_true", help="Delete this connector's OCI resources; keep session history")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    project = configured_project()
    import oci
    from oci.generative_ai import GenerativeAiClient, models
    client = CurlClient(os.environ["OPENAI_API_KEY"], project)
    app = Application(client, State(ROOT / ".geniagent", project))
    runtime = OCIRuntime(app.state, app.info)
    config = oci.config.from_file(profile_name=os.environ.get("OCI_SANDBOX_PROFILE", "Sandbox"))
    config["region"] = os.environ.get("OCI_SANDBOX_REGION", "us-chicago-1")
    projects = GenerativeAiClient(config, signer=runtime.client.base_client.signer,
                                 timeout=(5, 10), retry_strategy=oci.retry.NoneRetryStrategy())
    session_id = resource_id(args.session_id)
    try:
        with app.state.lock():
            if args.cleanup:
                cleanup(app, runtime, projects, session_id)
            else:
                connect(app, runtime, projects, models, session_id, args.network_policy)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        app.info(client.redact(exc) if isinstance(exc, APIError) else
                 f"{type(exc).__name__}: status={getattr(exc, 'status', None)}, code={getattr(exc, 'code', None)}")
        app.info("Resource IDs were retained. Inspect saved state and run this command with --cleanup if needed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
