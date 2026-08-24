"""HTTP server exposing the workspace manager API and static UI.

Stdlib-only: ThreadingHTTPServer + a small router. The server binds to
127.0.0.1 — it launches processes on this machine and must not be exposed.
"""

from __future__ import annotations

import importlib.resources
import json
import mimetypes
import os
import posixpath
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from .services import dockerc, gitinfo, ports, projects
from .services.config import ConfigError, ConfigStore
from .services.dockerc import ComposeService, DockerError
from .services.processes import Orchestrator, ProcessError

class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class App:
    """Holds shared service instances; the request handler delegates here."""

    def __init__(self, config_path: str | None = None):
        self.config = ConfigStore(config_path)
        self.orchestrator = Orchestrator()
        self.compose = ComposeService()

    # -- helpers ----------------------------------------------------------

    def _workspace(self, workspace_id: str | None = None) -> dict:
        ws = (
            self.config.get_workspace(workspace_id)
            if workspace_id
            else self.config.active_workspace()
        )
        if ws is None:
            raise ApiError("no active workspace", 404)
        return ws

    def _project_dir(self, ws: dict, project: str) -> str:
        # Reject path traversal: a project is exactly one child of the root.
        if not project or "/" in project or project in (".", ".."):
            raise ApiError(f"invalid project name: {project!r}")
        path = os.path.join(ws["root"], project)
        if not os.path.isdir(path):
            raise ApiError(f"no such project: {project}", 404)
        return path

    # -- API operations ---------------------------------------------------

    def state(self) -> dict:
        return {
            "workspaces": self.config.list_workspaces(),
            "active_workspace": self.config.active_workspace(),
            "settings": self.config.get_settings(),
            "docker_available": self.compose.available(),
            "terminals": projects.available_terminals(),
            "editors": projects.available_editors(),
            "home": os.path.expanduser("~"),
        }

    def list_projects(self) -> list[dict]:
        ws = self._workspace()
        result = []
        for info in projects.scan(ws["root"]):
            name = info["name"]
            info["settings"] = ws["projects"].get(name, {})
            info["git"] = gitinfo.status(info["path"]) if info["is_git"] else None
            procs = self.orchestrator.list(project=name)
            info["processes"] = procs
            port_list = set()
            for snap in procs:
                if snap["running"]:
                    port_list.update(ports.ports_for_pid(snap["pid"]))
            info["ports"] = sorted(port_list)
            result.append(info)
        return result

    def start_process(self, project: str, kind: str, command: str | None) -> dict:
        ws = self._workspace()
        cwd = self._project_dir(ws, project)
        settings = ws["projects"].get(project, {})
        if kind == "dev":
            command = command or settings.get("dev_command") or projects.detect_project(cwd)["suggested_command"]
            if not command:
                raise ApiError(
                    f"no dev command known for {project}; set one in project settings"
                )
            if self.orchestrator.running_for(project, "dev"):
                raise ApiError(f"a dev server for {project} is already running", 409)
            proc = self.orchestrator.start(command, cwd, project=project, kind="dev",
                                           label=f"{project} · dev server")
        elif kind == "compose":
            if not self.compose.available():
                raise ApiError("docker compose is not available on this machine", 501)
            if dockerc.find_compose_file(cwd) is None:
                raise ApiError(f"no compose file in {project}", 404)
            if self.orchestrator.running_for(project, "compose"):
                raise ApiError(f"compose for {project} is already running", 409)
            proc = self.orchestrator.start(
                "docker compose up", cwd, project=project, kind="compose",
                label=f"{project} · compose",
            )
        elif kind == "task":
            if not command:
                raise ApiError("command is required for kind=task")
            proc = self.orchestrator.start(command, cwd, project=project, kind="task",
                                           label=f"{project} · {command}")
        else:
            raise ApiError(f"unknown kind: {kind}")
        return proc.snapshot()

    def compose_down(self, project: str) -> dict:
        ws = self._workspace()
        cwd = self._project_dir(ws, project)
        # Stop any `compose up` process we own first, then `down` for cleanup.
        for proc in self.orchestrator.running_for(project, "compose"):
            proc.stop()
        try:
            output = self.compose.down(cwd)
        except DockerError as exc:
            raise ApiError(str(exc), 502) from exc
        return {"output": output}

    def compose_ps(self, project: str) -> list[dict]:
        ws = self._workspace()
        cwd = self._project_dir(ws, project)
        try:
            return self.compose.ps(cwd)
        except DockerError as exc:
            raise ApiError(str(exc), 502) from exc

    def open_tool(self, project: str, tool: str) -> dict:
        ws = self._workspace()
        cwd = self._project_dir(ws, project)
        settings = self.config.get_settings()
        try:
            if tool == "terminal":
                return projects.open_terminal(cwd, settings.get("terminal"))
            if tool == "editor":
                project_settings = ws["projects"].get(project, {})
                preferred = project_settings.get("editor") or settings.get("editor")
                return projects.open_editor(cwd, preferred)
        except RuntimeError as exc:
            raise ApiError(str(exc), 501) from exc
        raise ApiError(f"unknown tool: {tool}")


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    server_version = "devws/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # quieter default logging
        pass

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError("JSON body must be an object")
        return data

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        # importlib.resources works both from a checkout and from a zipapp build
        rel = posixpath.normpath(path.lstrip("/"))
        if rel.startswith("..") or "/" in rel:
            self._send_json({"error": "not found"}, 404)
            return
        resource = importlib.resources.files("devws").joinpath("static", rel)
        if not resource.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        body = resource.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if not path.startswith("/api/"):
                if method == "GET":
                    self._serve_static(path)
                else:
                    self._send_json({"error": "not found"}, 404)
                return
            result = self._route(method, path, query)
            self._send_json(result if result is not None else {"ok": True})
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except (ConfigError, ProcessError, NotADirectoryError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001 — surface as 500, keep serving
            self._send_json({"error": f"internal error: {exc}"}, 500)

    def _route(self, method: str, path: str, query: dict):
        app = self.app

        if (method, path) == ("GET", "/api/state"):
            return app.state()

        if (method, path) == ("GET", "/api/fs"):
            return projects.list_directories(query.get("path", "~"))

        if (method, path) == ("GET", "/api/workspaces"):
            return app.config.list_workspaces()
        if (method, path) == ("POST", "/api/workspaces"):
            body = self._read_body()
            return app.config.add_workspace(body.get("name", ""), body.get("root", ""))
        match = re.fullmatch(r"/api/workspaces/([\w-]+)", path)
        if match and method == "DELETE":
            if not app.config.remove_workspace(match.group(1)):
                raise ApiError("no such workspace", 404)
            return {"ok": True}
        match = re.fullmatch(r"/api/workspaces/([\w-]+)/activate", path)
        if match and method == "POST":
            return app.config.set_active(match.group(1))

        if (method, path) == ("GET", "/api/projects"):
            return app.list_projects()

        match = re.fullmatch(r"/api/projects/([^/]+)/settings", path)
        if match and method == "POST":
            ws = app._workspace()
            body = self._read_body()
            return app.config.set_project_settings(ws["id"], unquote(match.group(1)), body)

        match = re.fullmatch(r"/api/projects/([^/]+)/start", path)
        if match and method == "POST":
            body = self._read_body()
            return app.start_process(
                unquote(match.group(1)), body.get("kind", "dev"), body.get("command")
            )

        match = re.fullmatch(r"/api/projects/([^/]+)/compose/down", path)
        if match and method == "POST":
            return app.compose_down(unquote(match.group(1)))
        match = re.fullmatch(r"/api/projects/([^/]+)/compose/ps", path)
        if match and method == "GET":
            return app.compose_ps(unquote(match.group(1)))

        match = re.fullmatch(r"/api/projects/([^/]+)/open/(terminal|editor)", path)
        if match and method == "POST":
            return app.open_tool(unquote(match.group(1)), match.group(2))

        if (method, path) == ("GET", "/api/processes"):
            return app.orchestrator.list()
        match = re.fullmatch(r"/api/processes/([\w-]+)/stop", path)
        if match and method == "POST":
            code = app.orchestrator.stop(match.group(1))
            return {"returncode": code}
        match = re.fullmatch(r"/api/processes/([\w-]+)/logs", path)
        if match and method == "GET":
            proc = app.orchestrator.get(match.group(1))
            if proc is None:
                raise ApiError("no such process", 404)
            since = int(query.get("since", 0))
            lines, cursor = proc.logs(since)
            return {"lines": lines, "cursor": cursor, "running": proc.running,
                    "returncode": proc.returncode}
        match = re.fullmatch(r"/api/processes/([\w-]+)", path)
        if match and method == "DELETE":
            if not app.orchestrator.remove(match.group(1)):
                raise ApiError("no such process", 404)
            return {"ok": True}

        if (method, path) == ("GET", "/api/ports"):
            return ports.all_listening_ports()

        if (method, path) == ("POST", "/api/settings"):
            return app.config.update_settings(self._read_body())

        raise ApiError(f"no route: {method} {path}", 404)


def make_server(host: str = "127.0.0.1", port: int = 8765,
                config_path: str | None = None) -> tuple[ThreadingHTTPServer, App]:
    app = App(config_path)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd, app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Developer Workspace Manager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=None, help="path to config.json")
    args = parser.parse_args()

    httpd, app = make_server(args.host, args.port, args.config)

    def shutdown(*_sig):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"Developer Workspace Manager → http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    finally:
        app.orchestrator.shutdown()
        print("stopped all managed processes, bye")
