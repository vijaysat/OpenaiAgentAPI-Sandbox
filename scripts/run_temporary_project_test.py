"""Exercise Agents API with an isolated OCI network policy, then clean up OCI."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geniagent.app import Application, INITIAL_MESSAGE, PROJECT, State, load_dotenv, read_json, save_json
from geniagent.oci_runtime import OCIRuntime
from geniagent.transport import APIError, CurlClient


def wait_project(client, project_id, target, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            project = client.get_generative_ai_project(project_id).data
        except Exception as exc:
            if target == "DELETED" and getattr(exc, "status", None) == 404:
                return None
            raise
        if project.lifecycle_state == target:
            return project
        if project.lifecycle_state in ("FAILED", "DELETED"):
            raise APIError(f"Temporary OCI project reached {project.lifecycle_state}.")
        time.sleep(3)
    raise APIError(f"Timed out waiting for temporary OCI project to become {target}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network-policy", choices=("allowlist", "allow-all"), default="allowlist")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    import oci
    from oci.generative_ai import GenerativeAiClient
    from oci.generative_ai import models

    load_dotenv(ROOT / ".env")
    if os.environ.get("OPENAI_PROJECT_ID", PROJECT) != PROJECT:
        raise APIError(f"This application targets {PROJECT}.")
    source_project_id = os.environ["OCI_SANDBOX_PROJECT_ID"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    folder = ROOT / ".geniagent" / "live-tests" / stamp
    state = State(folder, PROJECT)
    client = CurlClient(os.environ["OPENAI_API_KEY"], PROJECT)
    app = Application(client, state, args.timeout)
    runtime = OCIRuntime(state, app.info)
    runtime.preflight()
    config = oci.config.from_file(profile_name=os.environ.get("OCI_SANDBOX_PROFILE", "Sandbox"))
    config["region"] = os.environ.get("OCI_SANDBOX_REGION", "us-chicago-1")
    projects = GenerativeAiClient(config, signer=runtime.client.base_client.signer,
                                 timeout=(5, 10), retry_strategy=oci.retry.NoneRetryStrategy())
    report = {"status": "running", "project_id": None, "source_project_id": source_project_id,
              "network_policy": args.network_policy, "project_create_retry_token": str(uuid.uuid4())}
    project_id = None

    def save():
        save_json(folder / "report.json", report)

    save()
    try:
        definition = read_json(ROOT / "config/agent.json")
        root_state = State(ROOT / ".geniagent", PROJECT)
        with root_state.lock():
            agent_id = Application(client, root_state).agent(definition)
        report["agent_id"] = agent_id
        source = projects.get_generative_ai_project(source_project_id).data
        hosts = ["registry.npmjs.org", "api.openai.com", "codex-cloud-environments.chatgpt.com"]
        policy = (models.SandboxAllowAllNetworkPolicy() if args.network_policy == "allow-all" else
                  models.SandboxAllowNetworkPolicy(allowed_domains=[models.AllowedDomain(hostnames=hosts, ports=[443])]))
        app.info(f"Creating temporary OCI project with {args.network_policy} egress…")
        response = projects.create_generative_ai_project(models.CreateGenerativeAiProjectDetails(
            compartment_id=source.compartment_id, display_name=f"geniagent-test-{stamp}",
            description="Temporary OpenAI Agents API live test; remove after validation",
            sandbox_config=models.SandboxConfig(is_enabled=True,
                sandbox_outbound_networking_config=models.ManagedSandboxOutboundNetworkingConfig(network_policy=policy))),
            opc_retry_token=report["project_create_retry_token"])
        project_id = report["project_id"] = response.data.id
        save()
        project = wait_project(projects, project_id, "ACTIVE")
        actual = project.sandbox_config.sandbox_outbound_networking_config
        report["actual_networking"] = oci.util.to_dict(actual)
        save()
        if actual.network_mode != "MANAGED" or oci.util.to_dict(actual.network_policy) != oci.util.to_dict(policy):
            raise APIError("Temporary project network policy differs from the requested policy.")
        os.environ["OCI_SANDBOX_PROJECT_ID"] = project_id
        with state.lock():
            app.start_oci(runtime, definition, INITIAL_MESSAGE, agent_id=agent_id)
            report["session_id"] = app.session_id
            report["initial_turn_completed"] = True
            tool_results = [read_json(path)["result"] for path in (folder / "tool-results").glob("*.json")]
            time_outputs = [json.loads(result["output"]) for result in tool_results if result.get("success")]
            matching = [value for value in time_outputs if value.get("timezone") == "America/New_York"]
            if not matching:
                raise APIError("Initial turn did not successfully exercise get_current_time.")
            report["time_tool_result"] = matching[0]
            save()
            followup = Application(client, state, args.timeout)
            followup.continue_session(app.session_id,
                "Read /workspace/outputs/startup-check.json again and explain the verified result in one sentence.")
            report["followup_turn_completed"] = True
            session, items, turns = followup.snapshot()
            save_json(folder / "session-snapshot.json", {"session": session, "items": items, "turns": turns})
            record = state.load(runtime.filename(app.session_id))
            report["sandbox_id"] = record["sandbox_id"]
            verify = (
                "import json; from pathlib import Path; "
                "p=Path('/workspace/outputs/startup-check.json'); d=json.loads(p.read_text()); "
                "values=d.values() if isinstance(d,dict) else d if isinstance(d,list) else [d]; "
                "assert 60 in values, f'Expected result 60, received {d!r}'; print(json.dumps(d))"
            )
            command = runtime.call("run_sandbox_command", record["sandbox_id"],
                runtime.models.RunSandboxCommandDetails(command="python3 -c " + shlex.quote(verify), timeout="PT1M"),
                opc_retry_token=str(uuid.uuid4())).data
            report["verification_command_id"] = command.id
            save()
            runtime.wait_command(record["sandbox_id"], command.id, timeout=90)
            output = runtime.call("get_sandbox_command_output", record["sandbox_id"], command.id).data
            report["verified_file"] = json.loads(output.stdout)
            report["status"] = "passed"
            app.info("PASS: initial turn, application tool, follow-up, and OCI file result 60.")
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        if isinstance(exc, APIError):
            report["error"] = client.redact(exc)
        else:
            # OCI SDK exceptions can include credentials in request bodies.
            report["error"] = f"{type(exc).__name__}: status={getattr(exc, 'status', None)}, code={getattr(exc, 'code', None)}"
        app.info(report["error"])
    finally:
        report["session_id"] = app.session_id
        errors = []
        if app.session_id:
            record = state.load(runtime.filename(app.session_id))
            report["sandbox_id"] = record.get("sandbox_id")
            errors.extend(runtime.cleanup(app.session_id))
            if record.get("sandbox_id"):
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    try:
                        runtime.call("get_sandbox", record["sandbox_id"])
                    except APIError as exc:
                        if exc.status == 404:
                            report["sandbox_cleanup"] = "confirmed 404"
                            break
                        errors.append(str(exc))
                        break
                    time.sleep(3)
                if "sandbox_cleanup" not in report:
                    errors.append("Sandbox deletion not confirmed.")
        if project_id:
            try:
                deadline = time.monotonic() + 120
                while True:
                    try:
                        projects.delete_generative_ai_project(project_id)
                        break
                    except Exception as exc:
                        if getattr(exc, "status", None) == 404:
                            break
                        if getattr(exc, "status", None) != 409 or time.monotonic() >= deadline:
                            raise
                        time.sleep(3)
                wait_project(projects, project_id, "DELETED", timeout=120)
                report["project_cleanup"] = "confirmed deleted"
            except Exception as exc:
                errors.append(f"Project cleanup incomplete: {type(exc).__name__}, status={getattr(exc, 'status', None)}")
        os.environ["OCI_SANDBOX_PROJECT_ID"] = source_project_id
        report["cleanup_errors"] = errors
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        app.info(f"Live test report: {folder / 'report.json'}")
        if errors:
            app.info("Cleanup requires attention: " + "; ".join(errors))
    return 0 if report["status"] == "passed" and not report["cleanup_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
