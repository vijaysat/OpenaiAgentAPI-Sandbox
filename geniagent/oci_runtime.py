"""Optional OCI beta sandbox lifecycle; Agents API calls remain curl-based."""
import os
from datetime import datetime, timezone
from pathlib import Path
import shlex
import time
from urllib.parse import urlparse
import uuid

from .transport import APIError


def build_sdk():
    try:
        import oci
        from oci.generative_ai_sandbox import GenerativeAiSandboxClient, models
    except ImportError as exc:
        raise APIError("OCI beta SDK missing. Run with .venv-oci/bin/python after installing the Oracle-provided wheel.") from exc
    profile = os.environ.get("OCI_SANDBOX_PROFILE", "Sandbox")
    region = os.environ.get("OCI_SANDBOX_REGION", "us-chicago-1")
    try:
        config = oci.config.from_file(profile_name=profile)
        token = Path(config["security_token_file"]).expanduser().read_text().strip()
        private_key = oci.signer.load_private_key_from_file(config["key_file"])
        signer = oci.auth.signers.SecurityTokenSigner(token, private_key)
        config["region"] = region
        endpoint = f"https://inference.generativeai.{region}.oci.oraclecloud.com"
        client = GenerativeAiSandboxClient(config, signer=signer, service_endpoint=endpoint,
                                          timeout=(5, 10), retry_strategy=oci.retry.NoneRetryStrategy())
    except Exception as exc:
        raise APIError(f"OCI profile {profile!r} could not be loaded. Renew its session with oci session authenticate "
                       f"--profile-name {shlex.quote(profile)} --region {shlex.quote(region)}. "
                       f"Error type: {type(exc).__name__}") from None
    return client, models


class OCIRuntime:
    def __init__(self, state, info, client=None, models=None):
        self.state, self.info = state, info
        self.client, self.models = (client, models) if client is not None else build_sdk()

    def preflight(self):
        if not os.environ.get("OCI_SANDBOX_PROJECT_ID", "").startswith("ocid1.generativeaiproject."):
            raise APIError("Set OCI_SANDBOX_PROJECT_ID to a sandbox-enabled Generative AI project OCID.")
        if not os.environ.get("OPENAI_EXECUTOR_API_KEY", "").strip():
            raise APIError("Set OPENAI_EXECUTOR_API_KEY to a separate OpenAI environment key for this project. "
                           "The application key is not sent into the OCI sandbox.")
        if os.environ.get("OPENAI_EXECUTOR_API_KEY") == os.environ.get("OPENAI_API_KEY"):
            raise APIError("OPENAI_EXECUTOR_API_KEY must be a separate environment key, not the application key.")

    def call(self, method, *args, **kwargs):
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except Exception as exc:
            # SDK errors can include command bodies and environment variables.
            status = getattr(exc, "status", "unknown")
            error = APIError(f"OCI {method} failed (HTTP {status}, {type(exc).__name__}). "
                             "Inspect retained resource IDs; writes are not automatically retried.")
            error.status = status
            error.code = getattr(exc, "code", None)
            error.request_id = getattr(exc, "request_id", None)
            error.provider = "OCI"
            raise error from None

    def issue(self, message, code, request_id=None):
        error = APIError(message, code=code, request_id=request_id)
        error.provider = "OCI"
        return error

    def filename(self, session_id):
        return f"{session_id}-oci.json"

    def observe(self, sandbox_id, session_id=None):
        """Read lifecycle evidence only; never persist the provider's environment values."""
        response = self.call("get_sandbox", sandbox_id)
        data = response.data
        observation = {"status": data.lifecycle_state, "checkedAt": time.time(),
                       "requestId": getattr(response, "headers", {}).get("opc-request-id")}
        for source, target in (("time_created", "createdAt"), ("time_expires", "expiresAt")):
            value = getattr(data, source, None)
            if isinstance(value, datetime):
                observation[target] = value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
        if session_id:
            name = self.filename(session_id)
            record = self.state.load(name)
            if record and record.get("sandbox_id") == sandbox_id:
                record["observation"] = observation
                self.state.save(name, record)
        return observation

    def wait_running(self, sandbox_id, timeout=180, session_id=None):
        deadline = time.monotonic() + timeout
        request_id = None
        while time.monotonic() < deadline:
            observation = self.observe(sandbox_id, session_id)
            request_id = observation["requestId"]
            status = observation["status"]
            if status == "RUNNING":
                return
            if status not in ("CREATING", "UPDATING"):
                raise self.issue(f"OCI sandbox did not start; lifecycle state: {status}.", "OCI_SANDBOX_STOPPED", request_id)
            time.sleep(2)
        raise self.issue("OCI sandbox startup timed out; resource IDs were retained for recovery.", "OCI_STARTUP_TIMEOUT", request_id)

    def wait_command(self, sandbox_id, command_id, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            command = self.call("get_sandbox_command", sandbox_id, command_id).data
            if command.exit_code is not None:
                if command.exit_code != 0:
                    detail = ""
                    try:
                        output = self.call("get_sandbox_command_output", sandbox_id, command_id).data
                        if "ENOTFOUND" in str(getattr(output, "stderr", "")):
                            detail = " DNS resolution failed during npm installation; check the OCI project's outbound network policy."
                    except APIError:
                        pass
                    raise APIError(f"OCI runtime setup failed with exit code {command.exit_code}. "
                                   "Select a runtime containing python3, Node.js and npm; check outbound npm access." + detail)
                return
            if getattr(command, "time_finished", None):
                raise APIError("OCI runtime setup ended without an exit code.")
            time.sleep(2)
        raise APIError("OCI runtime setup timed out; resource IDs were retained.")

    def provision(self, session):
        environment = session.get("environment") or {}
        remote = environment.get("remote_url", "")
        environment_id = environment.get("id")
        # The exact returned URL, including routing parameters, is used below.
        parsed = urlparse(remote)
        if not environment_id or parsed.scheme != "https" or parsed.hostname != "api.openai.com" or parsed.username:
            raise APIError("Self-hosted session did not return a valid environment ID and OpenAI remote_url.")
        session_id = session["id"]
        name = self.filename(session_id)
        if self.state.load(name):
            return self.resume(session)
        record = {"session_id": session_id, "environment_id": environment_id,
                  "create_retry_token": str(uuid.uuid4()), "sandbox_id": None}
        self.state.save(name, record)
        record["oci_project_id"] = os.environ["OCI_SANDBOX_PROJECT_ID"]
        self.state.save(name, record)
        details = self.models.CreateSandboxDetails(
                                display_name=f"GeniAgent-G-{session_id[-12:]}",
                                runtime=os.environ.get("OCI_SANDBOX_RUNTIME", "python-3.11"),
                                shape=os.environ.get("OCI_SANDBOX_SHAPE", "SMALL"),
                                expires_in_minutes=30)
        deadline = time.monotonic() + 120
        while True:
            try:
                created = self.call("create_sandbox", record["oci_project_id"], details,
                                    opc_retry_token=record["create_retry_token"])
                break
            except APIError as exc:
                if exc.status != 404 or exc.code != "ProjectNotFound" or time.monotonic() >= deadline:
                    raise
                self.info("Waiting for the new OCI project to reach the Sandbox API…")
                time.sleep(5)
        record["sandbox_id"] = sandbox_id = str(created.data.id)
        self.state.save(name, record)
        self.info(f"OCI sandbox {sandbox_id}; runtime expires after 30 minutes.")
        self.resume(session)

    def resume(self, session):
        """Continue only known provisioning steps; never recreate lost writes."""
        name = self.filename(session["id"])
        record = self.state.load(name)
        environment = session.get("environment") or {}
        remote = environment.get("remote_url", "")
        parsed = urlparse(remote)
        if (not record or record.get("session_id") != session["id"]
                or record.get("environment_id") != environment.get("id")
                or record.get("oci_project_id") != os.environ.get("OCI_SANDBOX_PROJECT_ID")
                or record.get("deleted") or not record.get("sandbox_id")
                or not record.get("create_retry_token")
                or parsed.scheme != "https" or parsed.hostname != "api.openai.com" or parsed.username):
            raise self.issue("OCI provisioning identity is unconfirmed; no resources were recreated.", "OCI_SETUP_UNCONFIRMED")
        sandbox_id = record["sandbox_id"]
        self.wait_running(sandbox_id, session_id=session["id"])
        record = self.state.load(name)  # Preserve the lifecycle evidence saved while waiting.
        command = (
            "command -v python3 >/dev/null && command -v node >/dev/null && "
            "command -v npm >/dev/null && mkdir -p /workspace/.local /workspace/outputs && "
            "npm install --global --prefix /workspace/.local @openai/codex@alpha && "
            "/workspace/.local/bin/codex exec-server --help >/dev/null"
        )
        if not record.get("setup_command_id"):
            if record.get("setup_submitted"):
                raise self.issue("OCI setup command submission is unconfirmed; it was not repeated.", "OCI_SETUP_UNCONFIRMED")
            record["setup_submitted"] = True
            self.state.save(name, record)
            installed = self.call("run_sandbox_command", sandbox_id,
                                  self.models.RunSandboxCommandDetails(command=command, timeout="PT5M"),
                                  opc_retry_token=record["create_retry_token"] + "-install")
            record["setup_command_id"] = str(installed.data.id)
            self.state.save(name, record)
        self.wait_command(sandbox_id, record["setup_command_id"])
        if record.get("executor_command_id"):
            return self.health(session["id"])
        if record.get("executor_submitted"):
            raise self.issue("OCI executor submission is unconfirmed; it was not repeated.", "OCI_SETUP_UNCONFIRMED")
        inner = "cd /workspace && exec " + shlex.join([
            "/workspace/.local/bin/codex", "exec-server", "--remote", remote,
            "--environment-id", environment["id"],
        ])
        record["executor_submitted"] = True
        self.state.save(name, record)
        executor = self.call("run_sandbox_command", sandbox_id,
                             self.models.RunSandboxCommandDetails(
                                 command="sh -lc " + shlex.quote(inner), timeout="PT30M",
                                 environment_variables=[self.models.EnvironmentVariable(
                                     name="CODEX_API_KEY", value=os.environ["OPENAI_EXECUTOR_API_KEY"])]),
                             opc_retry_token=record["create_retry_token"] + "-executor")
        record["executor_command_id"] = str(executor.data.id)
        self.state.save(name, record)
        self.info("OCI executor started; waiting for OpenAI to confirm the environment connection.")

    def health(self, session_id):
        record = self.state.load(self.filename(session_id))
        if not record:
            return
        sandbox_id = record.get("sandbox_id")
        if not sandbox_id or not record.get("executor_command_id"):
            raise APIError("OCI setup was incomplete. Inspect retained IDs and use delete before starting again.")
        sandbox = self.call("get_sandbox", sandbox_id).data
        command = self.call("get_sandbox_command", sandbox_id, record["executor_command_id"]).data
        if sandbox.lifecycle_state != "RUNNING" or command.exit_code is not None or getattr(command, "time_finished", None):
            raise APIError("OCI sandbox/executor stopped or expired. Inspect saved work and clean up this session.")

    def cleanup(self, session_id):
        record = self.state.load(self.filename(session_id))
        if not record or record.get("deleted"):
            return []
        sandbox_id = record.get("sandbox_id")
        if not sandbox_id:
            return ["OCI creation has no returned ID. Reconcile its saved create_retry_token in OCI before assuming cleanup."]
        errors = []
        try:
            self.call("stop_sandbox", sandbox_id, self.models.StopSandboxDetails(is_force=True))
        except APIError as exc:
            if exc.status not in (404, 409):
                errors.append(str(exc))
        # Stop failure must not suppress the independent delete attempt.
        deadline = time.monotonic() + 60
        while True:
            try:
                self.call("delete_sandbox", sandbox_id)
                break
            except APIError as exc:
                if exc.status == 404:
                    break
                if exc.status == 409 and time.monotonic() < deadline:
                    time.sleep(2)
                    continue
                errors.append(str(exc))
                return errors
        record["deleted"] = True
        self.state.save(self.filename(session_id), record)
        self.info("OCI sandbox deletion accepted; physical cleanup may continue asynchronously.")
        return errors
