import io
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from geniagent.app import State
from geniagent.transport import APIError
from scripts.run_oracle_guide import BRIEF, BRIEF_PATH, PLAN_PATH, prepare_brief, run


PROJECT = "proj_test"

class GuideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(Path(self.tmp.name), PROJECT)
        self.state.save("sess_fixture-oci.json", {"sandbox_id": "sbx_fixture", "environment_id": "env_fixture"})
        self.files = {}
        self.runtime = Mock(state=self.state)
        self.runtime.filename.return_value = "sess_fixture-oci.json"
        self.runtime.call.side_effect = self.call
        self.app = Mock(state=self.state, timeout=30, out=io.StringIO(), err=io.StringIO())
        self.app.client.redact.side_effect = str
        self.app.client.request.return_value = {"status": "connected"}
        self.app.snapshot.return_value = ({"status": "idle", "environment": {
            "id": "env_fixture", "type": "self_hosted", "workspace_directory": "/workspace"}}, [], [])

    def call(self, method, sid, path, *args, **kwargs):
        if method == "write_sandbox_file":
            self.files[path] = args[0].read()
            return NS(data=None)
        if path not in self.files:
            raise APIError("File not found", status=404)
        return NS(data=NS(content=self.files[path]))

    def test_existing_user_brief_is_preserved(self):
        self.files[BRIEF_PATH] = b"Existing work"
        with self.assertRaisesRegex(APIError, "different content"):
            prepare_brief(self.runtime, "sbx_fixture")
        self.assertEqual(self.files[BRIEF_PATH], b"Existing work")

    def test_existing_plan_prevents_another_submission(self):
        self.files[PLAN_PATH] = b"Existing plan"
        self.assertFalse(run(self.app, self.runtime, "sess_fixture", self.state.folder / "run"))
        self.app.continue_session.assert_not_called()
        self.assertEqual(self.files[PLAN_PATH], b"Existing plan")

    def test_prepare_only_verifies_file_without_model_request(self):
        self.assertTrue(run(self.app, self.runtime, "sess_fixture", self.state.folder / "run", prepare_only=True))
        self.app.continue_session.assert_not_called()
        self.assertEqual(self.files[BRIEF_PATH], BRIEF.encode())

    def test_billing_failure_does_not_create_a_fake_plan_or_retry(self):
        self.app.continue_session.side_effect = APIError("usage_limit_exceeded")
        folder = self.state.folder / "run"
        self.assertFalse(run(self.app, self.runtime, "sess_fixture", folder))
        self.app.continue_session.assert_called_once()
        self.assertFalse((folder / "plan.md").exists())
        self.assertNotIn(PLAN_PATH, self.files)

    def test_completed_turn_requires_independent_nonempty_oci_file(self):
        self.app.continue_session.side_effect = lambda *args: self.files.update({PLAN_PATH: b"Five steps"})
        folder = self.state.folder / "run"
        self.assertTrue(run(self.app, self.runtime, "sess_fixture", folder))
        self.assertEqual((folder / "plan.md").read_text(), "Five steps")
