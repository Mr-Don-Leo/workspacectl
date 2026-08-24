"""Git status inspection for project folders.

Shells out to the ``git`` CLI with short timeouts; every public function is
safe to call on a directory that is not a repository (returns ``None`` or a
clearly-marked result instead of raising).
"""

from __future__ import annotations

import subprocess

GIT_TIMEOUT = 10  # seconds; local git commands should be near-instant


def _run_git(path: str, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_repo(path: str) -> bool:
    proc = _run_git(path, "rev-parse", "--is-inside-work-tree")
    return proc is not None and proc.returncode == 0 and proc.stdout.strip() == "true"


def parse_porcelain_status(text: str) -> dict:
    """Parse ``git status --porcelain=v1 -b`` output into summary counts."""
    branch = None
    ahead = behind = 0
    staged = unstaged = untracked = conflicts = 0
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:]
            # forms: "main...origin/main [ahead 1, behind 2]", "main", "No commits yet on main", "HEAD (no branch)"
            if head.startswith("No commits yet on "):
                branch = head[len("No commits yet on "):]
            elif head.startswith("HEAD"):
                branch = "(detached)"
            else:
                branch = head.split("...")[0]
            if "[" in head:
                inside = head[head.index("[") + 1 : head.rindex("]")]
                for part in inside.split(","):
                    part = part.strip()
                    if part.startswith("ahead "):
                        ahead = int(part.split()[1])
                    elif part.startswith("behind "):
                        behind = int(part.split()[1])
            continue
        if len(line) < 2:
            continue
        index_flag, tree_flag = line[0], line[1]
        if index_flag == "?" and tree_flag == "?":
            untracked += 1
        elif "U" in (index_flag, tree_flag) or (index_flag, tree_flag) in (
            ("A", "A"),
            ("D", "D"),
        ):
            conflicts += 1
        else:
            if index_flag not in (" ", "?"):
                staged += 1
            if tree_flag not in (" ", "?"):
                unstaged += 1
    return {
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "conflicts": conflicts,
        "clean": not (staged or unstaged or untracked or conflicts),
    }


def status(path: str) -> dict | None:
    """Full status summary for ``path``, or ``None`` if not a git repo."""
    proc = _run_git(path, "status", "--porcelain=v1", "-b")
    if proc is None or proc.returncode != 0:
        return None
    result = parse_porcelain_status(proc.stdout)

    log = _run_git(path, "log", "-1", "--format=%h%x1f%s%x1f%cr")
    if log is not None and log.returncode == 0 and log.stdout.strip():
        short_hash, subject, when = (log.stdout.strip().split("\x1f") + ["", ""])[:3]
        result["last_commit"] = {"hash": short_hash, "subject": subject, "when": when}
    else:
        result["last_commit"] = None
    return result
