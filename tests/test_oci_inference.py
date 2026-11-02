import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from geniagent.oci_inference import OCIClient, Output, payload, stream_in_sandbox
from geniagent.transport import APIError


class OCIInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "application-secret", "OPENAI_EXECUTOR_API_KEY": "executor-secret"}):
            self.client = OCIClient("oci-secret", "ocid1.generativeaiproject.fixture", "us-chicago-1")
        self.out, self.err = io.StringIO(), io.StringIO()
        self.output = Output(self.client, self.folder, self.out, self.err)

    def test_oci_transport_excludes_openai_credentials(self):
        self.assertNotIn("OPENAI_API_KEY", self.client.env)
        self.assertNotIn("OPENAI_EXECUTOR_API_KEY", self.client.env)
        self.assertEqual(self.client.env["OCI_GENAI_API_KEY"], "oci-secret")
        self.assertEqual(self.client.script.name, "oci_responses.sh")

    def test_astra_payload_keeps_model_and_omits_unsupported_temperature(self):
        body = payload("openai.gpt-6-astra", "hello", "resp_previous")
        self.assertEqual(body["model"], "openai.gpt-6-astra")
        self.assertNotIn("temperature", body)
        self.assertEqual(body["previous_response_id"], "resp_previous")

    def test_delta_and_done_do_not_duplicate_text(self):
        self.output.event({"type": "response.output_text.delta", "item_id": "a", "delta": "Hello"})
        self.output.event({"type": "response.output_text.done", "item_id": "a", "text": "Hello world"})
        self.assertEqual(self.out.getvalue(), "Hello world\n")

    def test_failed_response_is_not_success_and_redacts_credentials(self):
        with self.assertRaises(APIError) as error:
            self.output.event({"type": "response.failed", "error": {"message": "oci-secret"}})
        self.assertNotIn("oci-secret", str(error.exception))
        self.assertNotIn("oci-secret", (self.folder / "events.jsonl").read_text())
        self.assertFalse(self.output.completed)

    def test_completed_response_is_saved_for_followup(self):
        self.assertTrue(self.output.event({"type": "response.completed", "response": {
            "id": "resp_fixture", "status": "completed", "output": []}}))
        self.assertEqual(self.output.response_id, "resp_fixture")
        self.assertEqual(json.loads((self.folder / "response.json").read_text())["status"], "completed")

    def test_shell_rejects_endpoint_and_route_injection_before_network(self):
        env = dict(self.client.env)
        for region, route in [("us-chicago-1/../../evil", "/responses"), ("us-chicago-1", "/responses/../agents")]:
            env["OCI_GENAI_REGION"] = region
            result = subprocess.run(["bash", str(self.client.script), "GET", route], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

    def test_sandbox_receives_only_oci_credentials_and_streams_once(self):
        events = [{"type": "response.output_text.delta", "item_id": "a", "delta": "OK"},
                  {"type": "response.completed", "response": {"id": "resp_remote", "status": "completed"}}]
        # The real OCI output API strips the trailing blank frame separator.
        transcript = "".join("data: " + json.dumps(e) + "\n\n" for e in events).rstrip("\n") + "\n"
        runtime = Mock()
        runtime.filename.return_value = "sess_fixture-oci.json"
        state = Mock()
        state.load.return_value = {"sandbox_id": "sbx_fixture"}
        runtime.models.RunSandboxCommandDetails.side_effect = lambda **kw: NS(**kw)
        runtime.models.EnvironmentVariable.side_effect = lambda **kw: NS(**kw)
        def call(method, *args, **kwargs):
            if method == "get_sandbox": return NS(data=NS(lifecycle_state="RUNNING"))
            if method == "run_sandbox_command": return NS(data=NS(id="cmd_fixture"))
            if method == "get_sandbox_command": return NS(data=NS(exit_code=0))
            if method == "get_sandbox_command_output": return NS(data=NS(stdout=transcript, stderr=""))
            return NS(data=None)
        runtime.call.side_effect = call
        with patch.dict(os.environ, {"OPENAI_PROJECT_ID": "proj_test"}), \
             patch("geniagent.oci_inference.State", return_value=state), \
             patch("geniagent.oci_runtime.OCIRuntime", return_value=runtime):
            stream_in_sandbox(self.client, payload("openai.gpt-6-astra", "hello"), self.output, 30, "sess_fixture")
        details = next(c.args[2] for c in runtime.call.call_args_list if c.args[0] == "run_sandbox_command")
        self.assertEqual({e.name for e in details.environment_variables},
                         {"OCI_GENAI_API_KEY", "OCI_GENAI_PROJECT_ID", "OCI_GENAI_REGION"})
        self.assertNotIn("oci-secret", details.command)
        self.assertEqual(self.out.getvalue(), "OK")
