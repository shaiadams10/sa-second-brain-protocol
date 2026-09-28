"""Model calls through the Antigravity CLI (`agy -p`).

Each call gets its own workspace folder holding the input files, and the answer
is forced into a JSON schema with --json-schema. The prompt tells the model to
read files only with its file viewer: headless mode cannot approve terminal
commands, and a model that tries one produces no output.

The model is the newest Gemini Flash the signed-in account offers, found by
listing models at run time, so a new Flash release is picked up automatically.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

AGY_CANDIDATES = ("agy", "~/AppData/Local/agy/bin/agy.exe", "~/.local/bin/agy")
READ_RULE = (
    "Read the input files in the current folder with your file viewing tool only, every file "
    "completely, page by page to the end. Never run terminal commands, never edit or create "
    "files, and never read anything outside the current folder."
)
_FLASH = re.compile(r"^gemini-(\d+(?:\.\d+)*)-flash-(high|medium|low)\b")


class ModelError(RuntimeError):
    pass


def find_agy() -> str:
    for candidate in AGY_CANDIDATES:
        found = shutil.which(str(Path(candidate).expanduser()))
        if found:
            return found
    raise ModelError("The Antigravity CLI (agy) was not found. Install it and sign in once.")


def update(agy: str) -> None:
    """Keep the CLI current; a failed update is not a reason to skip the run."""
    try:
        subprocess.run([agy, "update"], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        pass


def newest_flash(agy: str, tier: str = "high") -> str:
    out = subprocess.run([agy, "models"], capture_output=True, text=True, timeout=120,
                         encoding="utf-8", errors="replace").stdout
    found: list[tuple[tuple[int, ...], str]] = []
    for line in out.splitlines():
        match = _FLASH.match(line.strip())
        if match and match.group(2) == tier:
            version = tuple(int(x) for x in match.group(1).split("."))
            found.append((version, line.split()[0]))
    if not found:
        raise ModelError(f"No Gemini Flash model is available to the signed-in account:\n{out[-500:]}")
    return max(found)[1]


class Model:
    def __init__(self, workspace: Path, tier: str = "high", agy: str | None = None, name: str | None = None) -> None:
        self.agy = agy or find_agy()
        self.name = name or newest_flash(self.agy, tier)
        self.workspace = workspace
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def ask(self, task: str, files: dict[str, str], schema: dict, attempts: int = 3,
            timeout_minutes: int = 10) -> dict:
        folder = self.workspace / uuid.uuid4().hex[:10]
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (folder / name).write_text(content, encoding="utf-8")
        schema_path = folder.parent / f"{folder.name}.schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        prompt = f"{READ_RULE}\nInput files: {', '.join(files)}.\n\n{task}"
        last_error = ""
        try:
            for _ in range(attempts):
                result = subprocess.run(
                    [self.agy, "-p", prompt, "--model", self.name, "--json-schema", str(schema_path),
                     "--output-format", "json", "--print-timeout", f"{timeout_minutes}m"],
                    cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_minutes * 60 + 60,
                )
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    last_error = (result.stderr or result.stdout)[-800:]
                    continue
                usage = data.get("usage") or {}
                self.usage["calls"] += 1
                self.usage["input_tokens"] += usage.get("input_tokens", 0)
                self.usage["output_tokens"] += usage.get("output_tokens", 0)
                if data.get("status") == "SUCCESS" and isinstance(data.get("structured_output"), dict):
                    return data["structured_output"]
                last_error = json.dumps({k: data.get(k) for k in ("status", "denied_actions", "response")})[:800]
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            schema_path.unlink(missing_ok=True)
        raise ModelError(f"The model gave no usable answer after {attempts} attempts: {last_error}")
