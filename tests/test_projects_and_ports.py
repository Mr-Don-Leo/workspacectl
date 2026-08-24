import json
import os
import socket
import tempfile
import unittest

from devws.services import ports, projects


class ProjectScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def make_project(self, name, files):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        for fname, content in files.items():
            with open(os.path.join(path, fname), "w") as fh:
                fh.write(content)
        return path

    def test_detects_node_project_with_dev_script(self):
        self.make_project(
            "web", {"package.json": json.dumps({"scripts": {"dev": "vite"}})}
        )
        (project,) = projects.scan(self.root)
        self.assertIn("node", project["types"])
        self.assertEqual(project["suggested_command"], "npm run dev")

    def test_node_start_script_maps_to_npm_start(self):
        self.make_project(
            "api", {"package.json": json.dumps({"scripts": {"start": "node ."}})}
        )
        (project,) = projects.scan(self.root)
        self.assertEqual(project["suggested_command"], "npm start")

    def test_detects_compose_and_git(self):
        path = self.make_project("infra", {"compose.yaml": "services: {}\n"})
        os.makedirs(os.path.join(path, ".git"))
        (project,) = projects.scan(self.root)
        self.assertIn("docker", project["types"])
        self.assertTrue(project["is_git"])
        self.assertEqual(project["compose_file"], os.path.join(path, "compose.yaml"))

    def test_detects_django_and_rust(self):
        self.make_project("dj", {"manage.py": "", "requirements.txt": ""})
        self.make_project("rs", {"Cargo.toml": "[package]"})
        by_name = {p["name"]: p for p in projects.scan(self.root)}
        self.assertEqual(
            by_name["dj"]["suggested_command"], "python3 manage.py runserver"
        )
        self.assertEqual(by_name["rs"]["suggested_command"], "cargo run")

    def test_skips_hidden_and_ignored_dirs(self):
        for name in (".hidden", "node_modules", "dist"):
            os.makedirs(os.path.join(self.root, name))
        self.make_project("real", {})
        names = [p["name"] for p in projects.scan(self.root)]
        self.assertEqual(names, ["real"])

    def test_plain_folder_is_still_listed(self):
        self.make_project("docs", {"notes.txt": "hi"})
        (project,) = projects.scan(self.root)
        self.assertEqual(project["types"], ["folder"])
        self.assertIsNone(project["suggested_command"])

    def test_scan_missing_root_raises(self):
        with self.assertRaises(NotADirectoryError):
            projects.scan(os.path.join(self.root, "missing"))

    def test_list_directories_for_picker(self):
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "file.txt"), "w") as fh:
            fh.write("x")
        listing = projects.list_directories(self.root)
        self.assertEqual([d["name"] for d in listing["dirs"]], ["sub"])
        self.assertEqual(listing["parent"], os.path.dirname(self.root))


class EditorLaunchTests(unittest.TestCase):
    def test_folder_aware_gui_editor_gets_directory_argument(self):
        argv = projects.editor_launch_argv("code", "/work/app", "ptyxis")
        self.assertEqual(argv, ["code", "/work/app"])

    def test_plain_gui_editor_launches_bare(self):
        argv = projects.editor_launch_argv("gnome-text-editor", "/work/app", None)
        self.assertEqual(argv, ["gnome-text-editor"])

    def test_terminal_editor_wrapped_in_terminal_at_project_dir(self):
        argv = projects.editor_launch_argv("nano", "/work/app", "ptyxis")
        self.assertEqual(
            argv,
            ["ptyxis", "--new-window", "--working-directory", "/work/app", "-x", "nano"],
        )

    def test_terminal_editor_with_absolute_path(self):
        argv = projects.editor_launch_argv("/usr/bin/vim", "/work/app", "gnome-terminal")
        self.assertEqual(
            argv,
            ["gnome-terminal", "--working-directory=/work/app", "--", "/usr/bin/vim"],
        )

    def test_terminal_editor_without_terminal_raises(self):
        with self.assertRaisesRegex(RuntimeError, "terminal"):
            projects.editor_launch_argv("nano", "/work/app", None)

    def test_unknown_gui_editor_falls_back_to_dir_argument(self):
        argv = projects.editor_launch_argv("myeditor", "/work/app", None)
        self.assertEqual(argv, ["myeditor", "/work/app"])

    def test_available_editors_excludes_console_editor_without_terminal(self):
        from unittest.mock import patch

        def which(cmd):
            return f"/usr/bin/{os.path.basename(cmd)}" if os.path.basename(cmd) in ("nano",) else None

        with patch.object(projects.shutil, "which", side_effect=which), \
             patch.dict(os.environ, {"EDITOR": "nano"}):
            self.assertEqual(projects.available_editors(), [])

    def test_available_editors_includes_console_editor_with_terminal(self):
        from unittest.mock import patch

        def which(cmd):
            return (
                f"/usr/bin/{os.path.basename(cmd)}"
                if os.path.basename(cmd) in ("nano", "ptyxis")
                else None
            )

        with patch.object(projects.shutil, "which", side_effect=which), \
             patch.dict(os.environ, {"EDITOR": "nano"}):
            self.assertEqual(projects.available_editors(), ["nano"])


class PortsTests(unittest.TestCase):
    SAMPLE = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        "   0: 0100007F:22B8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 123456 1 0000000000000000 100 0 0 10 0\n"
        "   1: 00000000:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 123457 1 0000000000000000 100 0 0 10 0\n"
        "   2: 0100007F:0016 0100007F:B300 01 00000000:00000000 00:00000000 00000000  1000        0 123458 1 0000000000000000 100 0 0 10 0\n"
    )

    def test_parse_proc_net_tcp_listen_only(self):
        rows = ports.parse_proc_net_tcp(self.SAMPLE)
        self.assertEqual([r["port"] for r in rows], [0x22B8, 0x1F90])
        self.assertEqual(rows[0]["addr"], "127.0.0.1")
        self.assertEqual(rows[1]["addr"], "0.0.0.0")

    def test_ports_for_own_listening_socket(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            found = ports.ports_for_pid(os.getpid())
            self.assertIn(port, found)

    def test_all_listening_ports_contains_our_socket(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            entries = ports.all_listening_ports()
            self.assertIn(port, [e["port"] for e in entries])


if __name__ == "__main__":
    unittest.main()
