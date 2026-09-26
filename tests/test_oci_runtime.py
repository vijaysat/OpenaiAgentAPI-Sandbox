from contextlib import redirect_stderr
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from geniagent.app import Application, State, PROJECT, main
from geniagent.oci_runtime import OCIRuntime
from geniagent.transport import APIError


class FakeSDK:
    def __init__(self):
        self.calls = []
        self.exit_code = 0
        self.fail_stop = False

    def create_sandbox(self, project, details, **kwargs):
        self.calls.append(("create", project, details, kwargs))
        return NS(data=NS(id="ocid1.sandbox.fixture"))

    def get_sandbox(self, sid):
        return NS(data=NS(lifecycle_state="RUNNING"))

    def run_sandbox_command(self, sid, details, **kwargs):
        self.calls.append(("command", sid, details, kwargs))
        return NS(data=NS(id="command_" + str(len(self.calls))))

    def get_sandbox_command(self, sid, cid):
        return NS(data=NS(exit_code=self.exit_code, time_finished=None))

    def get_sandbox_command_output(self, sid, cid):
        return NS(data=NS(stderr="npm error ENOTFOUND registry.npmjs.org"))

    def stop_sandbox(self, sid, details):
        self.calls.append(("stop", sid))
        if self.fail_stop:
            error = RuntimeError("secret-containing SDK diagnostic")
            error.status = 500
            raise error

    def delete_sandbox(self, sid):
        self.calls.append(("delete", sid))


class OCITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(Path(self.tmp.name), PROJECT)
        self.sdk = FakeSDK()
        self.models = NS(CreateSandboxDetails=NS, RunSandboxCommandDetails=NS,
                         EnvironmentVariable=NS, StopSandboxDetails=NS)
        self.runtime = OCIRuntime(self.state, lambda _: None, self.sdk, self.models)
        self.session = {"id": "sess_fixture", "environment": {
            "id": "env_fixture", "remote_url": "https://api.openai.com/v1/agents/api?route=abc&zone=xyz"}}
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "application-key",
            "OPENAI_EXECUTOR_API_KEY": "environment-key",
            "OCI_SANDBOX_PROJECT_ID": "ocid1.generativeaiproject.fixture"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_provision_preserves_url_and_uses_only_executor_key(self):
        self.runtime.preflight()
        self.runtime.provision(self.session)
        commands = [c[2] for c in self.sdk.calls if c[0] == "command"]
        self.assertIn("command -v npm", commands[0].command)
        inner = shlex.split(commands[1].command)[2]
        self.assertIn(self.session["environment"]["remote_url"], shlex.split(inner))
        self.assertEqual(commands[1].environment_variables[0].value, "environment-key")
        self.assertNotIn("application-key", str(commands))
        record = self.state.load("sess_fixture-oci.json")
        self.assertEqual(record["sandbox_id"], "ocid1.sandbox.fixture")
        self.assertIn("executor_command_id", record)
        saved = (self.state.folder / "sess_fixture-oci.json").read_text()
        self.assertNotIn("environment-key", saved)

    def test_application_key_cannot_be_reused_as_executor_key(self):
        os.environ["OPENAI_EXECUTOR_API_KEY"] = "application-key"
        with self.assertRaisesRegex(APIError, "separate"):
            self.runtime.preflight()

    def test_setup_failure_retains_sandbox_id(self):
        self.sdk.exit_code = 127
        with self.assertRaisesRegex(APIError, "setup failed"):
            self.runtime.provision(self.session)
        self.assertEqual(self.state.load("sess_fixture-oci.json")["sandbox_id"], "ocid1.sandbox.fixture")

    def test_startup_timeout_resumes_same_sandbox_without_duplicate_commands(self):
        with patch.object(self.runtime, "wait_running", side_effect=self.runtime.issue("waiting", "OCI_STARTUP_TIMEOUT")):
            with self.assertRaises(APIError):
                self.runtime.provision(self.session)
        self.assertEqual([c[0] for c in self.sdk.calls], ["create"])
        self.runtime.resume(self.session)
        self.sdk.exit_code = None  # Executor still runs; setup has already finished.
        setup = self.state.load("sess_fixture-oci.json")["setup_command_id"]
        with patch.object(self.sdk, "get_sandbox_command", side_effect=lambda sid,cid: NS(data=NS(exit_code=0 if cid == setup else None, time_finished=None))):
            self.runtime.provision(self.session)
        self.assertEqual([c[0] for c in self.sdk.calls], ["create", "command", "command"])

    def test_uncertain_setup_command_is_not_replayed(self):
        with patch.object(self.sdk, "run_sandbox_command", side_effect=RuntimeError("lost response")):
            with self.assertRaises(APIError):
                self.runtime.provision(self.session)
        with self.assertRaisesRegex(APIError, "not repeated"):
            self.runtime.resume(self.session)
        self.assertEqual([c[0] for c in self.sdk.calls], ["create"])

    def test_resume_refuses_wrong_environment_and_expired_sandbox(self):
        with patch.object(self.runtime, "wait_running", side_effect=self.runtime.issue("waiting", "OCI_STARTUP_TIMEOUT")):
            with self.assertRaises(APIError):
                self.runtime.provision(self.session)
        bad = {**self.session, "environment": {**self.session["environment"], "id": "wrong"}}
        with self.assertRaisesRegex(APIError, "identity"):
            self.runtime.resume(bad)
        with patch.object(self.sdk, "get_sandbox", return_value=NS(data=NS(lifecycle_state="STOPPED"))):
            with self.assertRaises(APIError) as error:
                self.runtime.resume(self.session)
            self.assertEqual(error.exception.code, "OCI_SANDBOX_STOPPED")
        self.assertEqual([c[0] for c in self.sdk.calls], ["create"])

    def test_timeout_retains_safe_oci_request_reference(self):
        response = NS(data=NS(lifecycle_state="CREATING"), headers={"opc-request-id": "AAA/BBB/CCC"})
        with patch.object(self.sdk, "get_sandbox", return_value=response), patch("geniagent.oci_runtime.time.monotonic", side_effect=[0,0,2]), patch("geniagent.oci_runtime.time.sleep"):
            with self.assertRaises(APIError) as error:
                self.runtime.wait_running("sandbox", timeout=1)
        self.assertEqual(error.exception.code, "OCI_STARTUP_TIMEOUT")
        self.assertEqual(error.exception.request_id, "AAA/BBB/CCC")

    def test_lifecycle_observation_retains_times_without_provider_secrets(self):
        self.state.save("sess_fixture-oci.json", {"sandbox_id": "sandbox", "create_retry_token": "saved"})
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        response = NS(data=NS(lifecycle_state="STOPPED", time_created=now, time_expires=now,
                             environment_variables=[{"value": "secret-must-not-be-saved"}]),
                      headers={"opc-request-id": "safe/request"})
        with patch.object(self.sdk, "get_sandbox", return_value=response):
            with self.assertRaises(APIError):
                self.runtime.wait_running("sandbox", session_id="sess_fixture")
        record = self.state.load("sess_fixture-oci.json")
        self.assertEqual(record["observation"]["expiresAt"], now.timestamp())
        self.assertEqual(record["observation"]["status"], "STOPPED")
        self.assertEqual(record["create_retry_token"], "saved")
        self.assertNotIn("secret-must-not-be-saved", json.dumps(record))

    def test_resumed_setup_preserves_latest_lifecycle_evidence(self):
        self.runtime.provision(self.session)
        self.assertEqual(self.state.load("sess_fixture-oci.json")["observation"]["status"], "RUNNING")
        self.assertEqual(self.sdk.calls[0][2].expires_in_minutes, 30)

    def test_setup_dns_error_identifies_network_policy(self):
        self.sdk.exit_code = 1
        with self.assertRaisesRegex(APIError, "DNS resolution failed"):
            self.runtime.provision(self.session)

    def test_new_project_propagation_reuses_creation_token(self):
        original = self.sdk.create_sandbox
        attempts = []
        def create(project, details, **kwargs):
            attempts.append(kwargs["opc_retry_token"])
            if len(attempts) == 1:
                error = RuntimeError("Project has not propagated")
                error.status, error.code = 404, "ProjectNotFound"
                raise error
            return original(project, details, **kwargs)
        with patch.object(self.sdk, "create_sandbox", side_effect=create), \
             patch("geniagent.oci_runtime.time.sleep"):
            self.runtime.provision(self.session)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])

    def test_cleanup_attempts_delete_after_stop_failure(self):
        self.state.save("sess_fixture-oci.json", {"sandbox_id": "ocid1.sandbox.fixture"})
        self.sdk.fail_stop = True
        errors = self.runtime.cleanup("sess_fixture")
        self.assertEqual([c[0] for c in self.sdk.calls], ["stop", "delete"])
        self.assertEqual(len(errors), 1)
        self.assertNotIn("secret-containing", errors[0])

    def test_unknown_create_result_cannot_be_reported_deleted(self):
        self.state.save("sess_fixture-oci.json", {"sandbox_id": None, "create_retry_token": "saved-token"})
        self.assertTrue(self.runtime.cleanup("sess_fixture"))

    def test_remote_credentials_cannot_be_sent_to_another_host(self):
        self.session["environment"]["remote_url"] = "https://untrusted.example/executor"
        with self.assertRaisesRegex(APIError, "OpenAI remote_url"):
            self.runtime.provision(self.session)
        self.assertEqual(self.sdk.calls, [])

    def test_executor_exit_is_reported(self):
        self.state.save("sess_fixture-oci.json", {"sandbox_id": "ocid1.sandbox.fixture", "executor_command_id": "cmd"})
        with self.assertRaisesRegex(APIError, "stopped or expired"):
            self.runtime.health("sess_fixture")


class FlowTests(unittest.TestCase):
    def test_oci_cli_accepts_self_hosted_environment_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "agent.json").write_text(json.dumps({"model": "gpt-6-astra"}))
            (root / "environment.json").write_text(json.dumps({"type": "self_hosted"}))
            args = ["--env-file", str(root / "absent.env"), "--state-dir", str(root / "state"),
                    "start", "--agent-config", str(root / "agent.json"),
                    "--environment-config", str(root / "environment.json")]
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture", "OPENAI_PROJECT_ID": PROJECT}), \
                 patch("geniagent.app.CurlClient") as client, \
                 patch("geniagent.oci_runtime.OCIRuntime"), \
                 patch.object(Application, "start_oci") as start_oci:
                self.assertEqual(main(args + ["--oci"]), 0)
                start_oci.assert_called_once()
                client.return_value.request.assert_not_called()

    def test_self_hosted_cli_without_oci_explains_required_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "agent.json").write_text(json.dumps({"model": "gpt-6-astra"}))
            (root / "environment.json").write_text(json.dumps({"type": "self_hosted"}))
            errors = io.StringIO()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture", "OPENAI_PROJECT_ID": PROJECT}), \
                 patch("geniagent.app.CurlClient") as client, redirect_stderr(errors):
                client.return_value.redact.side_effect = str
                self.assertEqual(main(["--env-file", str(root / "absent.env"),
                    "--state-dir", str(root / "state"), "start",
                    "--agent-config", str(root / "agent.json"),
                    "--environment-config", str(root / "environment.json")]), 1)
                self.assertIn(".venv-oci/bin/python main.py start --oci", errors.getvalue())
                client.return_value.request.assert_not_called()

    def test_self_hosted_flow_subscribes_before_provision_and_input(self):
        calls = []
        class Client:
            def redact(self, value):
                return str(value)
            def request(self, method, path, body=None):
                calls.append((method, path, body))
                if path == "/agents":
                    return {"id": "agent_fixture"}
                if path == "/agents/sessions":
                    return {"id": "sess_fixture", "environment": {"id": "env_fixture"}}
                if path == "/agents/environments/env_fixture":
                    return {"status": "connected"}
                return {}
            def stream(self, *args, **kwargs):
                class Stream:
                    def __enter__(self):
                        calls.append(("subscribe",))
                        return iter([{"type": "agent.session.turn.completed", "turn": {"id": "turn_fixture"}}])
                    def __exit__(self, *args):
                        pass
                return Stream()
        class Runtime:
            def preflight(self):
                calls.append(("preflight",))
            def provision(self, session):
                calls.append(("provision",))
        with tempfile.TemporaryDirectory() as folder:
            app = Application(Client(), State(Path(folder), PROJECT), out=io.StringIO(), err=io.StringIO())
            app.start_oci(Runtime(), {"model": "gpt-6-astra"}, "Hello")
        labels = [x[0] for x in calls]
        self.assertLess(labels.index("subscribe"), labels.index("provision"))
        self.assertEqual(calls[-1][0:2], ("POST", "/agents/sessions/sess_fixture/events"))
        session_body = next(c[2] for c in calls if len(c) == 3 and c[1] == "/agents/sessions")
        self.assertEqual(session_body["agent_id"], "agent_fixture")
        self.assertEqual(session_body["environment"]["type"], "self_hosted")
        self.assertNotIn("input", session_body)
