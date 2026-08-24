import os
import tempfile
import unittest

from devws.services.dockerc import (
    ComposeService,
    DockerError,
    find_compose_file,
    parse_compose_ps,
)


class FakeRunner:
    """Records docker CLI invocations and replays scripted results."""

    def __init__(self):
        self.calls = []
        self.results = {}  # first arg after "compose" -> (code, out, err)
        self.version_result = (0, "Docker Compose version v2.29", "")

    def __call__(self, args, cwd=None):
        self.calls.append({"args": args, "cwd": cwd})
        if args[:3] == ["docker", "compose", "version"]:
            return self.version_result
        subcommand = args[4] if len(args) > 4 else None  # docker compose -f FILE <sub>
        return self.results.get(subcommand, (0, "", ""))


class ComposeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = os.path.join(self.tmp.name, "svc")
        os.makedirs(self.project)
        self.compose_path = os.path.join(self.project, "docker-compose.yml")
        with open(self.compose_path, "w") as fh:
            fh.write("services:\n  web:\n    image: nginx\n")
        self.runner = FakeRunner()
        self.svc = ComposeService(runner=self.runner)

    def test_available_caches_version_probe(self):
        self.assertTrue(self.svc.available())
        self.assertTrue(self.svc.available())
        probes = [c for c in self.runner.calls if "version" in c["args"]]
        self.assertEqual(len(probes), 1)

    def test_unavailable_when_cli_missing(self):
        def broken_runner(args, cwd=None):
            raise DockerError("docker CLI is not installed")

        self.assertFalse(ComposeService(runner=broken_runner).available())

    def test_up_builds_expected_command(self):
        self.svc.up(self.project)
        call = self.runner.calls[-1]
        self.assertEqual(
            call["args"],
            ["docker", "compose", "-f", self.compose_path, "up", "-d"],
        )
        self.assertEqual(call["cwd"], self.project)

    def test_up_with_build_flag(self):
        self.svc.up(self.project, build=True)
        self.assertIn("--build", self.runner.calls[-1]["args"])

    def test_up_failure_raises_with_stderr(self):
        self.runner.results["up"] = (1, "", "port already allocated")
        with self.assertRaisesRegex(DockerError, "port already allocated"):
            self.svc.up(self.project)

    def test_down_builds_expected_command(self):
        self.svc.down(self.project)
        self.assertEqual(
            self.runner.calls[-1]["args"],
            ["docker", "compose", "-f", self.compose_path, "down"],
        )

    def test_missing_compose_file_raises(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        with self.assertRaisesRegex(DockerError, "no compose file"):
            self.svc.up(empty)

    def test_ps_parses_containers(self):
        self.runner.results["ps"] = (
            0,
            '{"Name":"svc-web-1","Service":"web","State":"running","Status":"Up 5 minutes"}\n',
            "",
        )
        containers = self.svc.ps(self.project)
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["service"], "web")
        self.assertEqual(containers[0]["state"], "running")


class ComposeFileDetectionTests(unittest.TestCase):
    def test_prefers_modern_compose_yaml_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("docker-compose.yml", "compose.yaml"):
                open(os.path.join(tmp, name), "w").close()
            self.assertEqual(
                find_compose_file(tmp), os.path.join(tmp, "compose.yaml")
            )

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_compose_file(tmp))


class ComposePsParserTests(unittest.TestCase):
    def test_json_lines_format(self):
        out = (
            '{"Name":"a-web-1","Service":"web","State":"running","Status":"Up"}\n'
            '{"Name":"a-db-1","Service":"db","State":"exited","Status":"Exited (0)"}\n'
        )
        rows = parse_compose_ps(out)
        self.assertEqual([r["service"] for r in rows], ["web", "db"])

    def test_json_array_format(self):
        out = '[{"Name":"a-web-1","Service":"web","State":"running","Status":"Up"}]'
        rows = parse_compose_ps(out)
        self.assertEqual(rows[0]["name"], "a-web-1")

    def test_empty_and_garbage_output(self):
        self.assertEqual(parse_compose_ps(""), [])
        self.assertEqual(parse_compose_ps("   \n"), [])
        self.assertEqual(parse_compose_ps("not json at all"), [])


if __name__ == "__main__":
    unittest.main()
