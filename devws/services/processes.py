"""Process orchestration.

Starts and supervises long-running development processes (dev servers,
``docker compose up`` and friends) as their own process groups, captures
their combined output into a bounded ring buffer, and stops them with a
SIGTERM → SIGKILL escalation aimed at the whole group so shell-spawned
children die too.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from collections import deque

MAX_LOG_LINES = 2000
STOP_GRACE_SECONDS = 5.0


class ProcessError(Exception):
    pass


class ManagedProcess:
    def __init__(self, proc_id: str, label: str, project: str, kind: str,
                 command: str, cwd: str, popen: subprocess.Popen):
        self.id = proc_id
        self.label = label
        self.project = project
        self.kind = kind  # "dev" | "compose" | "task"
        self.command = command
        self.cwd = cwd
        self.popen = popen
        self.started_at = time.time()
        self.ended_at: float | None = None
        self._log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._log_offset = 0  # lines dropped from the front of the ring
        self._log_lock = threading.Lock()
        self._reader = threading.Thread(target=self._pump_output, daemon=True)
        self._reader.start()

    # -- output capture ---------------------------------------------------

    def _pump_output(self) -> None:
        stream = self.popen.stdout
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip("\n")
            with self._log_lock:
                if len(self._log) == self._log.maxlen:
                    self._log_offset += 1
                self._log.append(line)
        self.popen.wait()
        self.ended_at = time.time()

    def logs(self, since: int = 0) -> tuple[list[str], int]:
        """Return (lines, next_cursor) for lines at absolute index >= since."""
        with self._log_lock:
            start = self._log_offset
            lines = list(self._log)
        if since > start:
            lines = lines[since - start:]
            start = since
        return lines, start + len(lines)

    # -- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.popen.poll() is None

    @property
    def returncode(self) -> int | None:
        return self.popen.poll()

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self.popen.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            self.popen.send_signal(sig)

    def stop(self, grace: float = STOP_GRACE_SECONDS) -> int | None:
        """Terminate the process group, escalating to SIGKILL after ``grace``."""
        if self.running:
            self._signal_group(signal.SIGTERM)
            deadline = time.time() + grace
            while self.running and time.time() < deadline:
                time.sleep(0.05)
            if self.running:
                self._signal_group(signal.SIGKILL)
                self.popen.wait(timeout=10)
        if self.ended_at is None:
            self.ended_at = time.time()
        return self.popen.poll()

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "project": self.project,
            "kind": self.kind,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.popen.pid,
            "running": self.running,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "uptime": (self.ended_at or time.time()) - self.started_at,
        }


class Orchestrator:
    """Registry of managed processes, keyed by generated id."""

    def __init__(self):
        self._procs: dict[str, ManagedProcess] = {}
        self._lock = threading.Lock()

    def start(self, command: str, cwd: str, *, project: str = "", kind: str = "task",
              label: str | None = None, env: dict | None = None) -> ManagedProcess:
        command = (command or "").strip()
        if not command:
            raise ProcessError("command must not be empty")
        if not os.path.isdir(cwd):
            raise ProcessError(f"working directory does not exist: {cwd}")
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        try:
            popen = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                env=full_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                start_new_session=True,  # own process group => killable as a unit
            )
        except OSError as exc:
            raise ProcessError(f"failed to start {command!r}: {exc}") from exc
        if not label:
            try:
                label = shlex.split(command)[0]
            except ValueError:
                label = command.split()[0]
        proc = ManagedProcess(
            proc_id=uuid.uuid4().hex[:12],
            label=label,
            project=project,
            kind=kind,
            command=command,
            cwd=cwd,
            popen=popen,
        )
        with self._lock:
            self._procs[proc.id] = proc
        return proc

    def get(self, proc_id: str) -> ManagedProcess | None:
        with self._lock:
            return self._procs.get(proc_id)

    def list(self, project: str | None = None) -> list[dict]:
        with self._lock:
            procs = list(self._procs.values())
        snaps = [p.snapshot() for p in procs]
        if project is not None:
            snaps = [s for s in snaps if s["project"] == project]
        return sorted(snaps, key=lambda s: s["started_at"])

    def running_for(self, project: str, kind: str | None = None) -> list[ManagedProcess]:
        with self._lock:
            procs = list(self._procs.values())
        return [
            p for p in procs
            if p.project == project and p.running and (kind is None or p.kind == kind)
        ]

    def stop(self, proc_id: str, grace: float = STOP_GRACE_SECONDS) -> int | None:
        proc = self.get(proc_id)
        if proc is None:
            raise ProcessError(f"no such process: {proc_id}")
        return proc.stop(grace=grace)

    def remove(self, proc_id: str) -> bool:
        """Forget a finished process. Running processes must be stopped first."""
        with self._lock:
            proc = self._procs.get(proc_id)
            if proc is None:
                return False
            if proc.running:
                raise ProcessError("cannot remove a running process; stop it first")
            del self._procs[proc_id]
            return True

    def shutdown(self) -> None:
        """Stop everything (used at server exit)."""
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            if proc.running:
                proc.stop()
