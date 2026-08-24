"""Docker Compose integration.

All docker invocations go through an injectable ``runner`` callable so the
service can be exercised in tests (and degrade gracefully) on machines
without Docker installed.
"""

from __future__ import annotations

import json
import os
import subprocess

COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)

DOCKER_TIMEOUT = 60


class DockerError(Exception):
    pass


def default_runner(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a docker CLI command, returning (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=DOCKER_TIMEOUT
        )
    except FileNotFoundError as exc:
        raise DockerError("docker CLI is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"docker command timed out: {' '.join(args)}") from exc
    return proc.returncode, proc.stdout, proc.stderr


def find_compose_file(path: str) -> str | None:
    for name in COMPOSE_FILENAMES:
        candidate = os.path.join(path, name)
        if os.path.isfile(candidate):
            return candidate
    return None


class ComposeService:
    def __init__(self, runner=default_runner):
        self._runner = runner
        self._available: bool | None = None

    def available(self) -> bool:
        """True when `docker compose` is usable on this machine (cached)."""
        if self._available is None:
            try:
                code, _out, _err = self._runner(["docker", "compose", "version"])
                self._available = code == 0
            except DockerError:
                self._available = False
        return self._available

    def _compose(self, project_dir: str, *args: str) -> tuple[int, str, str]:
        compose_file = find_compose_file(project_dir)
        if compose_file is None:
            raise DockerError(f"no compose file found in {project_dir}")
        return self._runner(
            ["docker", "compose", "-f", compose_file, *args], cwd=project_dir
        )

    def up(self, project_dir: str, detach: bool = True, build: bool = False) -> str:
        args = ["up"]
        if detach:
            args.append("-d")
        if build:
            args.append("--build")
        code, out, err = self._compose(project_dir, *args)
        if code != 0:
            raise DockerError(err.strip() or out.strip() or "docker compose up failed")
        return out + err

    def down(self, project_dir: str) -> str:
        code, out, err = self._compose(project_dir, "down")
        if code != 0:
            raise DockerError(err.strip() or out.strip() or "docker compose down failed")
        return out + err

    def ps(self, project_dir: str) -> list[dict]:
        """List compose containers for the project as dicts."""
        code, out, err = self._compose(project_dir, "ps", "--format", "json", "-a")
        if code != 0:
            raise DockerError(err.strip() or "docker compose ps failed")
        return parse_compose_ps(out)


def parse_compose_ps(output: str) -> list[dict]:
    """Parse ``docker compose ps --format json`` output.

    Depending on the compose version this is either a JSON array or one JSON
    object per line; accept both.
    """
    output = output.strip()
    if not output:
        return []
    try:
        data = json.loads(output)
        rows = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        rows = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "name": row.get("Name") or row.get("name"),
                "service": row.get("Service") or row.get("service"),
                "state": row.get("State") or row.get("state"),
                "status": row.get("Status") or row.get("status"),
                "ports": row.get("Publishers") or row.get("Ports") or [],
            }
        )
    return result
