import json
import os
import tempfile
import unittest
import urllib.request

from devws import app as app_shell


class AppShellTests(unittest.TestCase):
    def test_gui_backend_reports_known_value(self):
        self.assertIn(app_shell.gui_backend(), ("gtk", "browser"))

    def test_background_server_uses_ephemeral_port_and_serves(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = os.path.join(tmp, "config.json")
            httpd, app, port = app_shell._serve_in_background("127.0.0.1", 0, config)
            try:
                self.assertGreater(port, 0)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/state", timeout=10
                ) as resp:
                    state = json.load(resp)
                self.assertIn("workspaces", state)
            finally:
                httpd.shutdown()
                app.orchestrator.shutdown()

    def test_server_binds_localhost_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, app, port = app_shell._serve_in_background(
                "127.0.0.1", 0, os.path.join(tmp, "c.json")
            )
            try:
                self.assertEqual(httpd.server_address[0], "127.0.0.1")
            finally:
                httpd.shutdown()
                app.orchestrator.shutdown()


if __name__ == "__main__":
    unittest.main()
