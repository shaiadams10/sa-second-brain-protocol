"""Starting other programs without flashing windows, and checking whether a process is alive.

On Windows a console program started from a windowless parent (the scheduled run, or a
dashboard without a console) gets a console window of its own that pops up and closes.
NO_WINDOW keeps every helper the brain starts (git, PowerShell, agy, codex) invisible.
"""

from __future__ import annotations

import subprocess
import sys

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run that never opens a window."""
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def alive(pid: int) -> bool:
    """True if a process with this id is running. Never signals the process."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
