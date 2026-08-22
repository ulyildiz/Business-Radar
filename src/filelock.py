# -*- coding: utf-8 -*-
"""Cross-process advisory file lock, standard library only.

Single responsibility: making sure only one holder at a time owns a named
resource. Knows nothing about APIs, quotas, or scans — it raises, it does not
log, so it can sit at the very bottom of the dependency graph.

WHY THIS EXISTS
Two copies of the app started at the same moment will each throttle themselves
correctly and still overwhelm the API, because the server sees one API key, not
two processes. Per-process rate limiting cannot solve that; the runs have to be
serialized: daily quotas and rate limits are both scoped to the key.
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Dict, Optional

STALE_AFTER_S = 6 * 3600     # a lock older than this is assumed abandoned
POLL_S = 0.05


class LockBusy(Exception):
    """The lock is held by a live process. `info` describes the holder."""

    def __init__(self, path: str, info: Dict[str, Any]):
        self.path = path
        self.info = info
        holder = info.get("pid", "?")
        started = info.get("started")
        age = f", running for {int(time.time() - started)}s" if started else ""
        super().__init__(f"lock held by PID {holder}{age} ({path})")


def pid_alive(pid: int) -> bool:
    """Is this PID still running? Conservative: unknown counts as alive.

    Note on Windows: `os.kill(pid, 0)` must NOT be used as a liveness probe —
    CPython maps it to TerminateProcess, so it would kill the very process we
    are asking about. We query the process handle instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True      # exists but owned by someone else
    except OSError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    still_active = 259
    query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32          # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True                        # cannot tell -> assume alive
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _read_info(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_stale(info: Dict[str, Any], stale_after: float) -> bool:
    """A lock is stale if its owner is gone, or it is implausibly old."""
    started = info.get("started")
    if isinstance(started, (int, float)) and time.time() - started > stale_after:
        return True
    pid = info.get("pid")
    if not isinstance(pid, int):
        return True                            # unreadable owner -> reclaimable
    if info.get("host") not in (None, socket.gethostname()):
        return False                           # another machine: never reclaim
    return not pid_alive(pid)


class FileLock:
    """Exclusive lock built on atomic O_CREAT|O_EXCL file creation.

    `timeout=0` fails immediately (used for the run lock, where a second run
    should be refused rather than queued); a positive timeout waits instead.
    """

    def __init__(self, path: str, *, timeout: float = 0.0,
                 stale_after: float = STALE_AFTER_S, label: str = ""):
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.label = label
        self._acquired = False

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_create():
                self._acquired = True
                return
            info = _read_info(self.path)
            if _is_stale(info, self.stale_after):
                self._break_stale()
                continue
            if time.monotonic() >= deadline:
                raise LockBusy(self.path, info)
            time.sleep(POLL_S)

    def _try_create(self) -> bool:
        payload = json.dumps({
            "pid": os.getpid(),
            "started": time.time(),
            "host": socket.gethostname(),
            "label": self.label,
        })
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError:
            return False
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _break_stale(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def resolve_lock_path(name: str) -> str:
    """Anchor a relative lock name to the current working directory."""
    return name if os.path.isabs(name) else os.path.join(os.getcwd(), name)


def describe_holder(path: str) -> Optional[str]:
    """Human-readable owner of an existing lock, or None if it is free."""
    info = _read_info(path)
    if not info:
        return None
    return f"PID {info.get('pid', '?')} on {info.get('host', '?')}"
