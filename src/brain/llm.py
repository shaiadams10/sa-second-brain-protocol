"""Model calls through a coding-agent CLI: Antigravity (`agy -p`) or Codex (`codex exec`).

Every call returns JSON forced into a schema, and every call's token usage is counted so
each run can report what it cost.

agy: each call gets its own workspace folder holding the input files. The prompt tells the
model to read files only with its file viewer: headless mode cannot approve terminal
commands, and a model that tries one produces no output. With no model named, the newest
Gemini Flash the signed-in account offers is used, found by listing models at run time.

codex: the input files are inlined into the prompt on stdin, in a read-only sandbox,
ephemeral (so the brain's own calls never show up as conversations), without the user's
Codex config (which halves the fixed cost of a call). Token usage comes from the
`turn.completed` events of `--json`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
import uuid
from pathlib import Path

CLIS = ("agy", "codex")
AGY_CANDIDATES = ("agy", "~/AppData/Local/agy/bin/agy.exe", "~/.local/bin/agy")
AGY_EFFORTS = ("low", "medium", "high", "max")
CODEX_HOME = Path("~/.codex").expanduser()
READ_RULE = (
    "Read the input files in the current folder with your file viewing tool only, every file "
    "completely, page by page to the end. Never run terminal commands, never edit or create "
    "files, and never read anything outside the current folder."
)
INLINE_RULE = (
    "The input files are included below, each after a line `===== <name> =====`. Read every "
    "file completely. Do not run commands, do not read or change anything else."
)
_FLASH = re.compile(r"^gemini-(\d+(?:\.\d+)*)-flash-(high|medium|low)\b")
_TIERED = re.compile(r"-(high|medium|low)$")


class ModelError(RuntimeError):
    pass


def _empty_usage() -> dict:
    return {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}


def _inline(files: dict[str, str]) -> str:
    return "\n".join(f"===== {name} =====\n{content}\n" for name, content in files.items())


# ----- Antigravity


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


def agy_models(agy: str) -> list[tuple[str, str]]:
    """(id, label) for every model the signed-in account offers, in the CLI's order."""
    out = subprocess.run([agy, "models"], capture_output=True, text=True, timeout=120,
                         encoding="utf-8", errors="replace").stdout
    models = []
    for line in out.splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) == 2 and parts[0] and " " not in parts[0]:
            models.append((parts[0], parts[1].strip()))
    return models


def newest_flash(agy: str, tier: str = "high") -> str:
    found: list[tuple[tuple[int, ...], str]] = []
    for model_id, _ in agy_models(agy):
        match = _FLASH.match(model_id)
        if match and match.group(2) == tier:
            found.append((tuple(int(x) for x in match.group(1).split(".")), model_id))
    if not found:
        raise ModelError("No Gemini Flash model is available to the signed-in account.")
    return max(found)[1]


class Model:
    """Antigravity CLI."""

    cli = "agy"

    def __init__(self, workspace: Path, tier: str = "high", agy: str | None = None, name: str | None = None,
                 effort: str | None = None) -> None:
        self.agy = agy or find_agy()
        if not name and effort in ("low", "medium", "high"):
            tier = effort
        self.name = name or newest_flash(self.agy, tier)
        # Model ids like gemini-3.8-flash-high carry their own effort; others take --effort.
        self.effort = None if _TIERED.search(self.name) else effort
        self.workspace = workspace
        self.usage = _empty_usage()

    def ask(self, task: str, files: dict[str, str], schema: dict, attempts: int = 3,
            timeout_minutes: int = 10) -> dict:
        folder = self.workspace / uuid.uuid4().hex[:10]
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (folder / name).write_text(content, encoding="utf-8")
        schema_path = folder.parent / f"{folder.name}.schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        prompt = f"{READ_RULE}\nInput files: {', '.join(files)}.\n\n{task}"
        command = [self.agy, "-p", prompt, "--model", self.name, "--json-schema", str(schema_path),
                   "--output-format", "json", "--print-timeout", f"{timeout_minutes}m"]
        if self.effort:
            command += ["--effort", self.effort]
        last_error = ""
        try:
            for _ in range(attempts):
                result = subprocess.run(
                    command, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_minutes * 60 + 60,
                )
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    last_error = (result.stderr or result.stdout)[-800:]
                    continue
                usage = data.get("usage") or {}
                self.usage["calls"] += 1
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    self.usage[key] += usage.get(key, 0) or 0
                if data.get("status") == "SUCCESS" and isinstance(data.get("structured_output"), dict):
                    return data["structured_output"]
                last_error = json.dumps({k: data.get(k) for k in ("status", "denied_actions", "response")})[:800]
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            schema_path.unlink(missing_ok=True)
        raise ModelError(f"The model gave no usable answer after {attempts} attempts: {last_error}")


# ----- Codex


def find_codex() -> str:
    found = shutil.which("codex")
    if found:
        return found
    raise ModelError("The Codex CLI (codex) was not found. Install it and sign in once.")


def codex_models() -> list[dict]:
    """Models the Codex CLI lists, from its local cache: id, label, efforts, default effort."""
    try:
        data = json.loads((CODEX_HOME / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = []
    for m in data.get("models", []):
        if m.get("visibility") != "list" or not m.get("slug"):
            continue
        efforts = [lvl.get("effort") if isinstance(lvl, dict) else lvl
                   for lvl in m.get("supported_reasoning_levels", [])]
        models.append({"id": m["slug"], "label": m.get("display_name") or m["slug"],
                       "efforts": [e for e in efforts if e], "default_effort": m.get("default_reasoning_level")})
    return models


def codex_default_model() -> str | None:
    """The model the user picked in ~/.codex/config.toml, else the first listed one."""
    try:
        name = tomllib.loads((CODEX_HOME / "config.toml").read_text(encoding="utf-8")).get("model")
    except (OSError, tomllib.TOMLDecodeError):
        name = None
    if isinstance(name, str) and name:
        return name
    listed = codex_models()
    return listed[0]["id"] if listed else None


def parse_codex_events(stdout: str) -> tuple[dict, str]:
    """Sum the token usage of every finished turn and keep the last error message."""
    usage, error = _empty_usage(), ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            u = event.get("usage") or {}
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                usage[key] += u.get(key, 0) or 0
        elif event.get("type") in ("turn.failed", "error"):
            error = json.dumps(event.get("error") or event.get("message") or event)[:800]
    return usage, error


class CodexModel:
    """Codex CLI (`codex exec`)."""

    cli = "codex"

    def __init__(self, workspace: Path, name: str | None = None, effort: str | None = None,
                 codex: str | None = None) -> None:
        self.codex = codex or find_codex()
        self.name = name or codex_default_model()
        if not self.name:
            raise ModelError("No Codex model is configured or listed. Sign in to Codex once.")
        self.effort = effort
        self.workspace = workspace
        self.usage = _empty_usage()

    def ask(self, task: str, files: dict[str, str], schema: dict, attempts: int = 3,
            timeout_minutes: int = 10) -> dict:
        folder = self.workspace / uuid.uuid4().hex[:10]
        folder.mkdir(parents=True, exist_ok=True)
        schema_path, answer_path = folder / "schema.json", folder / "answer.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        prompt = f"{INLINE_RULE}\n\n{task}\n\n{_inline(files)}"
        command = [self.codex, "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check",
                   "--ephemeral", "--ignore-user-config", "--ignore-rules", "-m", self.name,
                   "--output-schema", str(schema_path), "-o", str(answer_path), "-C", str(folder)]
        if self.effort:
            command += ["-c", f"model_reasoning_effort={self.effort}"]
        command.append("-")  # the prompt comes from stdin: too long for a Windows command line
        last_error = ""
        try:
            for _ in range(attempts):
                answer_path.unlink(missing_ok=True)
                try:
                    result = subprocess.run(command, cwd=folder, input=prompt, capture_output=True, text=True,
                                            encoding="utf-8", errors="replace", timeout=timeout_minutes * 60)
                except subprocess.TimeoutExpired:
                    last_error = f"no answer within {timeout_minutes} minutes"
                    continue
                usage, error = parse_codex_events(result.stdout)
                self.usage["calls"] += 1
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    self.usage[key] += usage[key]
                try:
                    data = json.loads(answer_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    return data
                last_error = error or (result.stderr or result.stdout)[-800:]
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        raise ModelError(f"The model gave no usable answer after {attempts} attempts: {last_error}")


# ----- Choosing


def make_model(work_dir: Path, cli: str = "agy", name: str | None = None, effort: str | None = None,
               log=print):
    """The model a run uses. A name starting with "manual" means a person or assistant answers."""
    if name and name.startswith("manual"):
        model = ManualModel(work_dir / "manual", name=name)
        log(f"Manual mode: answer each request in {model.workspace}")
        return model
    if cli == "codex":
        return CodexModel(work_dir / "codex", name=name, effort=effort)
    if cli != "agy":
        raise ModelError(f"Unknown CLI {cli!r}; use one of {', '.join(CLIS)}.")
    agy = find_agy()
    log("Updating the Antigravity CLI")
    update(agy)
    return Model(work_dir / "agy", agy=agy, name=name, effort=effort)


def available_models() -> dict:
    """What the dashboard offers in Run now: models and effort levels per installed CLI."""
    options: dict = {}
    try:
        agy = find_agy()
        options["agy"] = {"models": [{"id": i, "label": label, "efforts": [] if _TIERED.search(i) else list(AGY_EFFORTS)}
                                     for i, label in agy_models(agy)],
                          "default": None}
    except (ModelError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        find_codex()
        options["codex"] = {"models": codex_models(), "default": codex_default_model()}
    except ModelError:
        pass
    return options


class ManualModel:
    """A person or an assistant session answers instead of a model: each request is written to
    <workspace>/<n>/request.md with its schema, and the run waits for <n>/answer.json.
    Used for a one-time backfill done by hand, with the exact same prompts and rules.
    Tokens are spent in that person's or session's own tool, so they are not measured here."""

    cli = "manual"
    effort = None

    def __init__(self, workspace: Path, name: str = "manual", poll_seconds: float = 2.0) -> None:
        self.workspace = workspace
        self.name = name
        self.poll_seconds = poll_seconds
        self.usage = {"calls": 0, "measured": False}
        workspace.mkdir(parents=True, exist_ok=True)
        existing = [int(p.name) for p in workspace.iterdir() if p.is_dir() and p.name.isdigit()]
        self._n = max(existing, default=0)

    def ask(self, task: str, files: dict[str, str], schema: dict, **_) -> dict:
        import time

        self._n += 1
        folder = self.workspace / f"{self._n:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        body = [task, ""] + [f"===== {name} =====\n{content}\n" for name, content in files.items()]
        (folder / "request.md").write_text("\n".join(body), encoding="utf-8")
        (folder / "schema.json").write_text(json.dumps(schema, indent=1), encoding="utf-8")
        answer = folder / "answer.json"
        while True:
            if answer.exists():
                try:
                    data = json.loads(answer.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict) and all(k in data for k in schema.get("required", [])):
                    self.usage["calls"] += 1
                    (folder / "done").write_text("", encoding="utf-8")
                    return data
            time.sleep(self.poll_seconds)
