"""Listening-port discovery.

Reads ``/proc/net/tcp``/``tcp6`` directly (no privileges or external tools
needed for the parse) and maps sockets back to pids by scanning our own
processes' ``/proc/<pid>/fd``. Mapping is only guaranteed for processes owned
by the current user — which covers everything the orchestrator spawns.
"""

from __future__ import annotations

import os

TCP_LISTEN = "0A"  # state column value for LISTEN in /proc/net/tcp


def parse_proc_net_tcp(text: str) -> list[dict]:
    """Parse /proc/net/tcp(6) content into [{port, inode, addr}] LISTEN rows."""
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        local, state, inode = parts[1], parts[3], parts[9]
        if state != TCP_LISTEN:
            continue
        addr_hex, _, port_hex = local.rpartition(":")
        try:
            port = int(port_hex, 16)
        except ValueError:
            continue
        rows.append({"port": port, "inode": inode, "addr": _format_addr(addr_hex)})
    return rows


def _format_addr(addr_hex: str) -> str:
    if len(addr_hex) == 8:  # IPv4, little-endian hex
        try:
            raw = bytes.fromhex(addr_hex)
        except ValueError:
            return addr_hex
        return ".".join(str(b) for b in reversed(raw))
    return "::" if set(addr_hex) <= {"0"} else "[ipv6]"


def _listening_sockets() -> list[dict]:
    rows = []
    for name in ("tcp", "tcp6"):
        try:
            with open(f"/proc/net/{name}", "r", encoding="ascii") as fh:
                rows.extend(parse_proc_net_tcp(fh.read()))
        except OSError:
            continue
    return rows


def _socket_inodes_by_pid(pids: list[int]) -> dict[str, int]:
    """Map socket inode -> pid for the given pids (best effort)."""
    inode_to_pid: dict[str, int] = {}
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("socket:["):
                inode_to_pid[target[8:-1]] = pid
    return inode_to_pid


def _descendant_pids(root_pid: int) -> list[int]:
    """root_pid plus all of its descendants, via /proc children lists."""
    result, queue = [], [root_pid]
    while queue:
        pid = queue.pop()
        result.append(pid)
        for tid_dir in (f"/proc/{pid}/task",):
            try:
                tids = os.listdir(tid_dir)
            except OSError:
                continue
            for tid in tids:
                try:
                    with open(f"{tid_dir}/{tid}/children", "r") as fh:
                        queue.extend(int(c) for c in fh.read().split())
                except (OSError, ValueError):
                    continue
    return result


def ports_for_pid(root_pid: int) -> list[int]:
    """Listening TCP ports owned by ``root_pid`` or any of its descendants."""
    pids = _descendant_pids(root_pid)
    inode_to_pid = _socket_inodes_by_pid(pids)
    seen = set()
    for sock in _listening_sockets():
        if sock["inode"] in inode_to_pid:
            seen.add(sock["port"])
    return sorted(seen)


def all_listening_ports() -> list[dict]:
    """Every LISTEN socket on the machine, deduplicated by port+addr."""
    seen = set()
    result = []
    for sock in _listening_sockets():
        key = (sock["port"], sock["addr"])
        if key in seen:
            continue
        seen.add(key)
        result.append({"port": sock["port"], "addr": sock["addr"]})
    return sorted(result, key=lambda s: s["port"])
