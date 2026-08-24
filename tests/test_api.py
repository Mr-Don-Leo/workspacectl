"""End-to-end API tests: real HTTP server, real filesystem, real processes."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from devws.server import make_server

PY = sys.executable or "python3"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "projects")
        os.makedirs(os.path.join(cls.root, "alpha"))
        os.makedirs(os.path.join(cls.root, "beta"))
        with open(os.path.join(cls.root, "beta", "compose.yaml"), "w") as fh:
            fh.write("services: {}\n")
        config_path = os.path.join(cls.tmp.name, "config.json")
        cls.httpd, cls.app = make_server("127.0.0.1", 0, config_path)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.app.orchestrator.shutdown()
        cls.tmp.cleanup()

    def request(self, method, path, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_00_state_before_setup(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(state["active_workspace"])
        self.assertIn("docker_available", state)

    def test_01_fs_browser(self):
        status, listing = self.request("GET", f"/api/fs?path={self.tmp.name}")
        self.assertEqual(status, 200)
        self.assertIn("projects", [d["name"] for d in listing["dirs"]])

    def test_02_create_workspace_and_list_projects(self):
        status, ws = self.request(
            "POST", "/api/workspaces", {"name": "Test WS", "root": self.root}
        )
        self.assertEqual(status, 200)
        type(self).ws_id = ws["id"]

        status, plist = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        names = [p["name"] for p in plist]
        self.assertEqual(names, ["alpha", "beta"])
        beta = next(p for p in plist if p["name"] == "beta")
        self.assertIsNotNone(beta["compose_file"])

    def test_03_project_settings_and_dev_process_lifecycle(self):
        status, _ = self.request(
            "POST",
            "/api/projects/alpha/settings",
            {"dev_command": f"{PY} -u -c \"import time; print('serving'); time.sleep(60)\""},
        )
        self.assertEqual(status, 200)

        status, proc = self.request(
            "POST", "/api/projects/alpha/start", {"kind": "dev"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(proc["running"])

        # duplicate dev server is refused
        status, err = self.request("POST", "/api/projects/alpha/start", {"kind": "dev"})
        self.assertEqual(status, 409)

        # logs stream through the cursor API
        deadline = time.time() + 10
        lines = []
        while time.time() < deadline and not lines:
            _, log = self.request("GET", f"/api/processes/{proc['id']}/logs")
            lines = log["lines"]
            time.sleep(0.05)
        self.assertEqual(lines, ["serving"])

        status, result = self.request("POST", f"/api/processes/{proc['id']}/stop")
        self.assertEqual(status, 200)
        _, listing = self.request("GET", "/api/processes")
        me = next(p for p in listing if p["id"] == proc["id"])
        self.assertFalse(me["running"])

    def test_04_dev_start_without_command_fails_cleanly(self):
        status, err = self.request("POST", "/api/projects/beta/start", {"kind": "dev"})
        self.assertEqual(status, 400)
        self.assertIn("no dev command", err["error"])

    def test_05_path_traversal_is_rejected(self):
        status, err = self.request(
            "POST", "/api/projects/..%2F..%2Fetc/start", {"kind": "dev"}
        )
        self.assertIn(status, (400, 404))

    def test_06_unknown_route_is_404(self):
        status, _ = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)

    def test_07_ports_endpoint(self):
        status, entries = self.request("GET", "/api/ports")
        self.assertEqual(status, 200)
        self.assertIn(self.port, [e["port"] for e in entries])

    def test_08_static_ui_is_served(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
        self.assertEqual(resp.status, 200)
        self.assertIn("Workspace", body)

    def test_09_workspace_delete(self):
        status, _ = self.request("DELETE", f"/api/workspaces/{self.ws_id}")
        self.assertEqual(status, 200)
        status, _ = self.request("GET", "/api/projects")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
