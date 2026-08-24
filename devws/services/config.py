"""Workspace configuration store.

Persists workspaces (a named root directory plus per-project overrides) and
global settings as JSON. Writes are atomic (tmp file + rename) so a crash
mid-save never corrupts the store.
"""

from __future__ import annotations

import json
import os
import threading
import uuid


def default_config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "dev-workspace-manager", "config.json")


class ConfigError(Exception):
    pass


class ConfigStore:
    """Thread-safe JSON-backed store for workspaces and settings."""

    def __init__(self, path: str | None = None):
        self.path = path or default_config_path()
        self._lock = threading.RLock()
        self._data = self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return self._empty()
        except (json.JSONDecodeError, OSError) as exc:
            raise ConfigError(f"cannot read config at {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config at {self.path} is not a JSON object")
        data.setdefault("workspaces", [])
        data.setdefault("settings", {})
        data.setdefault("active_workspace", None)
        return data

    @staticmethod
    def _empty() -> dict:
        return {"workspaces": [], "settings": {}, "active_workspace": None}

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)

    # -- workspaces -------------------------------------------------------

    def list_workspaces(self) -> list[dict]:
        with self._lock:
            return [dict(ws) for ws in self._data["workspaces"]]

    def get_workspace(self, workspace_id: str) -> dict | None:
        with self._lock:
            for ws in self._data["workspaces"]:
                if ws["id"] == workspace_id:
                    return dict(ws)
        return None

    def add_workspace(self, name: str, root: str) -> dict:
        name = (name or "").strip()
        root = os.path.abspath(os.path.expanduser(root or ""))
        if not name:
            raise ConfigError("workspace name must not be empty")
        if not os.path.isdir(root):
            raise ConfigError(f"root directory does not exist: {root}")
        with self._lock:
            if any(ws["root"] == root for ws in self._data["workspaces"]):
                raise ConfigError(f"a workspace for {root} already exists")
            ws = {
                "id": uuid.uuid4().hex[:12],
                "name": name,
                "root": root,
                "projects": {},
            }
            self._data["workspaces"].append(ws)
            self._data["active_workspace"] = ws["id"]
            self.save()
            return dict(ws)

    def remove_workspace(self, workspace_id: str) -> bool:
        with self._lock:
            before = len(self._data["workspaces"])
            self._data["workspaces"] = [
                ws for ws in self._data["workspaces"] if ws["id"] != workspace_id
            ]
            removed = len(self._data["workspaces"]) != before
            if removed:
                if self._data["active_workspace"] == workspace_id:
                    self._data["active_workspace"] = (
                        self._data["workspaces"][0]["id"]
                        if self._data["workspaces"]
                        else None
                    )
                self.save()
            return removed

    def set_active(self, workspace_id: str) -> dict:
        with self._lock:
            ws = self.get_workspace(workspace_id)
            if ws is None:
                raise ConfigError(f"no such workspace: {workspace_id}")
            self._data["active_workspace"] = workspace_id
            self.save()
            return ws

    def active_workspace(self) -> dict | None:
        with self._lock:
            wid = self._data["active_workspace"]
            return self.get_workspace(wid) if wid else None

    # -- per-project settings --------------------------------------------

    def get_project_settings(self, workspace_id: str, project: str) -> dict:
        with self._lock:
            ws = self.get_workspace(workspace_id)
            if ws is None:
                raise ConfigError(f"no such workspace: {workspace_id}")
            return dict(ws["projects"].get(project, {}))

    def set_project_settings(
        self, workspace_id: str, project: str, settings: dict
    ) -> dict:
        allowed = {"dev_command", "editor", "auto_start", "notes"}
        unknown = set(settings) - allowed
        if unknown:
            raise ConfigError(f"unknown project settings: {', '.join(sorted(unknown))}")
        with self._lock:
            for ws in self._data["workspaces"]:
                if ws["id"] == workspace_id:
                    current = ws["projects"].setdefault(project, {})
                    current.update(settings)
                    # dropping a key by setting it to None/empty
                    for key in [k for k, v in current.items() if v in (None, "")]:
                        del current[key]
                    if not current:
                        del ws["projects"][project]
                    self.save()
                    return dict(current)
        raise ConfigError(f"no such workspace: {workspace_id}")

    # -- global settings --------------------------------------------------

    def get_settings(self) -> dict:
        with self._lock:
            return dict(self._data["settings"])

    def update_settings(self, settings: dict) -> dict:
        allowed = {"terminal", "editor", "theme"}
        unknown = set(settings) - allowed
        if unknown:
            raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
        with self._lock:
            self._data["settings"].update(settings)
            for key in [k for k, v in self._data["settings"].items() if v in (None, "")]:
                del self._data["settings"][key]
            self.save()
            return dict(self._data["settings"])
