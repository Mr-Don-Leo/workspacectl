"""Project discovery and external tool launching.

Scans the immediate children of a workspace root, classifies each folder by
its marker files, and suggests a dev-server command. Also resolves which
terminal emulator / editor is available for "open terminal here" and
"open in editor" actions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from . import dockerc

# marker file -> (type label, suggested dev command or None)
_MARKERS = [
    ("package.json", "node", None),  # command derived from scripts, see below
    ("pyproject.toml", "python", None),
    ("manage.py", "django", "python3 manage.py runserver"),
    ("requirements.txt", "python", None),
    ("Cargo.toml", "rust", "cargo run"),
    ("go.mod", "go", "go run ."),
    ("Gemfile", "ruby", "bundle exec rails server"),
    ("mix.exs", "elixir", "mix phx.server"),
    ("pom.xml", "maven", "mvn spring-boot:run"),
    ("build.gradle", "gradle", "./gradlew bootRun"),
    ("Makefile", "make", None),
    ("index.html", "static", None),
]

TERMINAL_CANDIDATES = [
    ("gnome-terminal", ["gnome-terminal", "--working-directory={dir}"]),
    ("ptyxis", ["ptyxis", "--new-window", "--working-directory", "{dir}"]),
    ("konsole", ["konsole", "--workdir", "{dir}"]),
    ("kitty", ["kitty", "--directory", "{dir}"]),
    ("alacritty", ["alacritty", "--working-directory", "{dir}"]),
    ("foot", ["foot", "--working-directory={dir}"]),
    ("xterm", ["xterm", "-e", "cd {dir} && exec $SHELL"]),
]

# how each terminal emulator runs a command inside a directory
TERMINAL_RUN_TEMPLATES = {
    "gnome-terminal": ["gnome-terminal", "--working-directory={dir}", "--", "{cmd}"],
    "ptyxis": ["ptyxis", "--new-window", "--working-directory", "{dir}", "-x", "{cmd}"],
    "konsole": ["konsole", "--workdir", "{dir}", "-e", "{cmd}"],
    "kitty": ["kitty", "--directory", "{dir}", "{cmd}"],
    "alacritty": ["alacritty", "--working-directory", "{dir}", "-e", "{cmd}"],
    "foot": ["foot", "--working-directory={dir}", "{cmd}"],
    "xterm": ["xterm", "-e", "{cmd}"],
}

# GUI editors that open a folder as a project take {dir}; plain GUI editors
# are launched with the project as their working directory instead
EDITOR_CANDIDATES = [
    ("code", ["code", "{dir}"]),
    ("codium", ["codium", "{dir}"]),
    ("subl", ["subl", "{dir}"]),
    ("zed", ["zed", "{dir}"]),
    ("idea", ["idea", "{dir}"]),
    ("gnome-text-editor", ["gnome-text-editor"]),
    ("gedit", ["gedit"]),
    ("kate", ["kate"]),
    ("mousepad", ["mousepad"]),
    ("geany", ["geany"]),
]

# console editors must run inside a terminal emulator, not detached
TERMINAL_EDITORS = {"nano", "vim", "nvim", "vi", "micro", "hx", "helix", "emacs"}

_IGNORED_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build"}


def _node_dev_command(project_dir: str) -> str | None:
    try:
        with open(os.path.join(project_dir, "package.json"), "r", encoding="utf-8") as fh:
            pkg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    scripts = pkg.get("scripts") or {}
    for name in ("dev", "start", "serve", "watch"):
        if name in scripts:
            return f"npm run {name}" if name != "start" else "npm start"
    return None


def detect_project(project_dir: str) -> dict:
    """Classify one folder: type, suggested dev command, compose file, git."""
    types = []
    suggested = None
    for marker, type_label, command in _MARKERS:
        if os.path.exists(os.path.join(project_dir, marker)):
            if type_label not in types:
                types.append(type_label)
            if suggested is None:
                if type_label == "node":
                    suggested = _node_dev_command(project_dir)
                elif type_label == "python" and marker == "pyproject.toml":
                    suggested = None
                else:
                    suggested = command
    compose_file = dockerc.find_compose_file(project_dir)
    if compose_file and "docker" not in types:
        types.append("docker")
    return {
        "name": os.path.basename(project_dir),
        "path": project_dir,
        "types": types or ["folder"],
        "suggested_command": suggested,
        "compose_file": compose_file,
        "is_git": os.path.isdir(os.path.join(project_dir, ".git")),
    }


def scan(root: str) -> list[dict]:
    """Detect projects among the immediate subdirectories of ``root``."""
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    projects = []
    for entry in sorted(os.listdir(root), key=str.lower):
        if entry.startswith(".") or entry in _IGNORED_DIRS:
            continue
        path = os.path.join(root, entry)
        if os.path.isdir(path):
            projects.append(detect_project(path))
    return projects


# -- external tools -------------------------------------------------------

def _first_available(candidates, preferred: str | None = None):
    ordered = list(candidates)
    if preferred:
        ordered.sort(key=lambda c: c[0] != preferred)
        if preferred not in {name for name, _ in candidates} and shutil.which(preferred):
            ordered.insert(0, (preferred, [preferred, "{dir}"]))
    for name, template in ordered:
        if shutil.which(name):
            return name, template
    return None, None


def available_terminals() -> list[str]:
    return [name for name, _ in TERMINAL_CANDIDATES if shutil.which(name)]


def available_editors() -> list[str]:
    """Editors usable on this device.

    Console editors ($EDITOR like nano/vim) count only when there is a
    terminal emulator to run them in.
    """
    found = [name for name, _ in EDITOR_CANDIDATES if shutil.which(name)]
    have_terminal = bool(available_terminals())
    env_editor = os.environ.get("EDITOR")
    candidates = [env_editor] if env_editor else []
    candidates += sorted(TERMINAL_EDITORS)
    for cand in candidates:
        base = os.path.basename(cand)
        if base in {os.path.basename(f) for f in found}:
            continue
        if not shutil.which(cand):
            continue
        if base in TERMINAL_EDITORS and not have_terminal:
            continue
        found.append(cand)
    return found


def editor_launch_argv(editor: str, project_dir: str,
                       terminal: str | None) -> list[str]:
    """Build the argv that opens ``editor`` on the project folder.

    - folder-aware GUI editors get the directory as an argument
    - plain GUI editors launch with the project as working directory
    - console editors run inside ``terminal`` at the project directory
    """
    base = os.path.basename(editor)
    if base in TERMINAL_EDITORS:
        if not terminal or terminal not in TERMINAL_RUN_TEMPLATES:
            raise RuntimeError(
                f"{base} is a terminal editor but no terminal emulator is available"
            )
        template = TERMINAL_RUN_TEMPLATES[terminal]
        argv = []
        for part in template:
            if part == "{cmd}":
                argv.append(editor)
            else:
                argv.append(part.replace("{dir}", project_dir))
        return argv
    for name, template in EDITOR_CANDIDATES:
        if name == base:
            return [part.replace("{dir}", project_dir) for part in template]
    return [editor, project_dir]


def _launch_detached(argv: list[str], cwd: str) -> int:
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def open_terminal(project_dir: str, preferred: str | None = None) -> dict:
    name, template = _first_available(TERMINAL_CANDIDATES, preferred)
    if name is None:
        raise RuntimeError(
            "no terminal emulator found (looked for: "
            + ", ".join(n for n, _ in TERMINAL_CANDIDATES) + ")"
        )
    argv = [part.replace("{dir}", project_dir) for part in template]
    pid = _launch_detached(argv, project_dir)
    return {"tool": name, "pid": pid}


def open_editor(project_dir: str, preferred: str | None = None) -> dict:
    editors = available_editors()
    if preferred and shutil.which(preferred):
        editor = preferred
    elif preferred and preferred in editors:
        editor = preferred
    elif editors:
        editor = editors[0]
    else:
        raise RuntimeError("no editor found (set $EDITOR or install one)")
    terminals = available_terminals()
    argv = editor_launch_argv(editor, project_dir, terminals[0] if terminals else None)
    pid = _launch_detached(argv, project_dir)
    return {"tool": os.path.basename(editor), "pid": pid}


# -- filesystem browsing for the directory picker -------------------------

def list_directories(path: str) -> dict:
    """Immediate subdirectories of ``path`` for the UI's folder browser."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        raise NotADirectoryError(path)
    dirs = []
    try:
        entries = sorted(os.listdir(path), key=str.lower)
    except PermissionError:
        entries = []
    for entry in entries:
        if entry.startswith("."):
            continue
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            dirs.append({"name": entry, "path": full})
    parent = os.path.dirname(path)
    return {
        "path": path,
        "parent": parent if parent != path else None,
        "dirs": dirs,
    }
