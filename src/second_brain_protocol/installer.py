from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import RuntimePaths, setup_runtime
from .model_runner import find_codex_executable


def install_standalone_codex(paths: RuntimePaths) -> Path:
    setup_runtime(paths)
    install_dir = paths.root / "codex-bin"
    install_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(paths.codex_home)
    env["CODEX_INSTALL_DIR"] = str(install_dir)
    env["CODEX_NON_INTERACTIVE"] = "1"
    command = "irm https://chatgpt.com/codex/install.ps1 | iex"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Codex standalone installation failed")
    setup_runtime(paths)
    return find_codex_executable(paths)


def login_dedicated_account(paths: RuntimePaths) -> int:
    executable = find_codex_executable(paths)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(paths.codex_home)
    return subprocess.run([str(executable), "login"], env=env, check=False).returncode
