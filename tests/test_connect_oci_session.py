import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from geniagent.app import Application, PROJECT, State
from geniagent.transport import APIError
from scripts.connect_oci_session import connect, prepare_replacement


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = State(Path(self.folder.name), PROJECT)
        self.runtime = Mock(state=self.state)
        self.runtime.filename.return_value = "sess_fixture-oci.json"
        self.client = Mock()
        self.client.redact.side_effect = str
        self.app = Application(self.client, self.state, err=io.StringIO())
        self.session = {"id": "sess_fixture", "status": "requires_action", "environment": {
            "id": "env_fixture", "type": "self_hosted", "workspace_directory": "/workspace"}}

    def test_connected_environment_does_not_create_compute_or_send_input(self):
        self.client.request.side_effect = [self.session, {"status": "connected"}]
        projects = Mock()
        connect(self.app, self.runtime, projects, Mock(), "sess_fixture", "allow-all")
        self.runtime.provision.assert_not_called()
        projects.create_generative_ai_project.assert_not_called()
        self.assertTrue(all(call.args[0] == "GET" for call in self.client.request.call_args_list))

    def test_failed_session_does_not_create_compute(self):
        self.session["status"] = "failed"
        self.client.request.return_value = self.session
        projects = Mock()
        with self.assertRaisesRegex(APIError, "session has failed"):
            connect(self.app, self.runtime, projects, Mock(), "sess_fixture", "allow-all")
        projects.create_generative_ai_project.assert_not_called()

    def test_existing_sandbox_cannot_be_overwritten_even_when_marked_deleted(self):
        original = {"sandbox_id": "sbx_fixture", "deleted": True}
        self.state.save("sess_fixture-oci.json", original)
        with self.assertRaisesRegex(APIError, "sandbox already exists"):
            prepare_replacement(self.runtime, "sess_fixture")
        self.assertEqual(self.state.load("sess_fixture-oci.json")["sandbox_id"], "sbx_fixture")
        self.assertEqual(list(self.state.folder.glob("*previous*")), [])

    def test_confirmed_absent_sandbox_mapping_is_archived(self):
        self.state.save("sess_fixture-oci.json", {"sandbox_id": "sbx_fixture"})
        self.runtime.call.side_effect = APIError("Not found", status=404)
        prepare_replacement(self.runtime, "sess_fixture")
        self.assertEqual(len(list(self.state.folder.glob("*previous*"))), 1)

    def test_unknown_creation_outcome_prevents_duplicate_sandbox(self):
        self.state.save("sess_fixture-oci.json", {"sandbox_id": None, "create_retry_token": "token"})
        with self.assertRaisesRegex(APIError, "no returned ID"):
            prepare_replacement(self.runtime, "sess_fixture")
        self.runtime.call.assert_not_called()
