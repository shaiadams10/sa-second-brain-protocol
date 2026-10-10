"""The dashboard's Guide: the vault's documents, every command, and the voice study's live state.

Documents are found, not listed by hand: Markdown at the vault root and in me/, brain/, experience/,
career/, plus projects/catalog.md and the voice study's results in .brain/voice/. Weekly logs and
project notes are left out because the Log and Projects views already show them. Only files on
that list can be read through the API.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from brain import proc, voicetest
from brain.config import Config

GROUPS = (
    ("", "Start here"),
    ("me", "About the owner"),
    ("experience", "Experience"),
    ("career", "Career"),
    ("brain", "Engine and studies"),
    ("projects", "Projects"),
    (".brain/voice", "Voice study results"),
)
SKIP_DIRS = {"opportunities"}
_H1 = re.compile(r"^#\s+(.+)$", re.M)
_START = re.compile(r"mark: \d+ of \d+ messages already marked; (\d+) to go in (\d+) batches, (.+?), (\d+) in parallel")
_DONE = re.compile(r"(\d+)/(\d+) batches, ([\d,]+)/([\d,]+) messages marked \((\d+)%\), (\S+) so far"
                   r"(?:, about (\S+) left)?")
_ENDED = re.compile(r"mark: (?:this run ended|finished)")
_TOKENS = re.compile(r"tokens ([\d,]+) in / ([\d,]+) out")


def _title(path: Path) -> str:
    if path.suffix == ".md":
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            head = ""
        match = _H1.search(head)
        if match:
            return match.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").capitalize()


def documents(cfg: Config) -> list[dict]:
    vault = cfg.vault
    found: list[Path] = sorted(vault.glob("*.md"))
    for folder in ("me", "experience", "brain"):
        found += sorted((vault / folder).glob("*.md"))
    found += [p for p in sorted((vault / "career").rglob("*.md")) if not SKIP_DIRS & set(p.parts)]
    found += [vault / "projects" / "catalog.md"]
    voice = vault / ".brain" / "voice"
    found += sorted(voice.glob("*.md")) + [voice / "corpus-summary.json"]
    docs = []
    for path in found:
        if not path.is_file():
            continue
        rel = path.relative_to(vault).as_posix()
        group = next((g for g, _ in sorted(GROUPS, key=lambda g: -len(g[0])) if g and rel.startswith(g + "/")), "")
        stat = path.stat()
        docs.append({"path": rel, "title": _title(path), "group": group, "size": stat.st_size,
                     "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    return docs


def read_document(cfg: Config, rel: str) -> dict:
    allowed = {d["path"]: d for d in documents(cfg)}
    if rel not in allowed:
        raise KeyError(rel)
    text = (cfg.vault / rel).read_text(encoding="utf-8", errors="replace")
    return {**allowed[rel], "text": text}


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 64000))
        return fh.read().decode("utf-8", "replace").splitlines()[-lines:]


def voice_status(cfg: Config) -> dict | None:
    """Where the writing-style study stands, read from its files and log; None if it never ran."""
    folder = cfg.work_dir / "voice"
    summary_path = folder / "corpus-summary.json"
    if not summary_path.exists():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    marked = 0
    marks = folder / "marks.jsonl"
    if marks.exists():
        with marks.open("rb") as fh:
            marked = sum(1 for _ in fh)
    total = summary.get("messages", 0)
    log = _tail(folder / "voice.log", 400)
    run: dict = {"running": False}
    for line in log:
        if _START.search(line):
            m = _START.search(line)
            run = {"running": True, "started": line[:19], "batches": int(m.group(2)), "finished": 0,
                   "model": m.group(3), "workers": int(m.group(4)), "eta": None, "tokens_in": 0, "tokens_out": 0}
        elif _DONE.search(line) and run.get("running"):
            m = _DONE.search(line)
            run.update(finished=int(m.group(1)), elapsed=m.group(6), eta=m.group(7))
            t = _TOKENS.search(line)
            if t:
                run.update(tokens_in=int(t.group(1).replace(",", "")), tokens_out=int(t.group(2).replace(",", "")))
        elif _ENDED.search(line):
            run["running"] = False
        elif "out of usage or signed out" in line and "stopped" in line:
            run.update(running=False, account_stop=line[:19])
        elif line[20:].startswith("write: ") and "wrote" not in line:
            run.update(step="write", step_started=line[:19])
        elif line[20:].startswith(("test: saved", "test: not built", "test: no profile", "write: wrote", "test: wrote")):
            run.pop("step", None)
        elif line[20:].startswith("test: "):
            run.update(step="test", step_started=line[:19])
    # The log says what ran; only a live process says what is running now.
    active_path = folder / "active.json"
    active = None
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if active and proc.alive(int(active.get("pid", 0))):
        step = active.get("step")
        run["running"] = step == "mark" or (bool(run.get("running")) and step in ("weekly", "newtest"))
        if step in ("test", "weekly", "newtest"):
            run["step"] = "test"
        elif step == "write":
            run["step"] = "write"
    else:
        if run.get("running") or run.get("step"):
            run["interrupted"] = run.get("step") or "mark"
        run["running"] = False
        run.pop("step", None)
        if active:
            active_path.unlink(missing_ok=True)
    marked = min(marked, total)
    if marked < total and not run.get("running") and not run.get("step"):
        run.setdefault("paused", True)

    def stamp(name: str) -> str | None:
        p = folder / name
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds") if p.exists() else None

    script = cfg.vault / "brain" / "voice-study.ps1"
    return {
        "summary": summary, "marked": marked, "total": total, "run": run, "log": log[-40:],
        "files": {n: stamp(n) for n in ("corpus.jsonl", "findings.md", "holdout.json", "writing-style.draft.md",
                                         "write-notes.md", "blind-test.html", "pasted-review.md")},
        "settings": cfg.voice, "script": str(script) if script.exists() else None,
        "tests": voicetest.summary(cfg),
    }


def start_voice(cfg: Config) -> bool:
    """Open the run-or-resume script in its own window, where it shows progress and can be closed."""
    script = cfg.vault / "brain" / "voice-study.ps1"
    status = voice_status(cfg) or {}
    if not script.exists() or sys.platform != "win32" or (status.get("run") or {}).get("running"):
        return False
    subprocess.Popen(["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                     cwd=str(cfg.vault), creationflags=subprocess.CREATE_NEW_CONSOLE)
    return True


def start_test(cfg: Config) -> bool:
    """Build a new blind test now, in its own window (marking new messages first can take minutes)."""
    status = voice_status(cfg) or {}
    run = status.get("run") or {}
    if sys.platform != "win32" or run.get("running") or run.get("step"):
        return False
    subprocess.Popen(["powershell.exe", "-NoExit", "-Command",
                      "$host.UI.RawUI.WindowTitle = 'Voice blind test - building'; sbrain voice newtest; "
                      "Write-Host ''; Write-Host 'Done. Take the test on the dashboard (Guide). You can close this window.'"],
                     cwd=str(cfg.vault), creationflags=subprocess.CREATE_NEW_CONSOLE)
    return True


def commands(cfg: Config) -> list[dict]:
    script = cfg.vault / "brain" / "voice-study.ps1"
    groups = [
        {"title": "Everyday", "items": [
            ("sbrain dashboard", "Open this dashboard."),
            ("sbrain run --current", "Update the brain now, including the week in progress."),
            ("sbrain run", "Catch up every finished week that is not logged yet."),
            ("sbrain run --week 2026-W39", "Write one week again; the new result replaces the old one."),
        ]},
        {"title": "Deeper passes", "items": [
            ("sbrain run --backfill", "Walk all history from the first conversation, oldest first; resumes where it stopped."),
            ("sbrain run --learn", "Read logged weeks again for skills and what the owner stated, without rewriting logs."),
            ("sbrain run --cli codex --model gpt-6.1-sol --effort high", "Pick the CLI, model, and effort for one run."),
        ]},
        {"title": "Inspect", "items": [
            ("sbrain scan", "Show how the projects folder is classified."),
            ("sbrain digest --week last", "Build a week's digest without AI, to see what the model would read."),
        ]},
        {"title": "Voice study", "items": [
            *([(r"powershell -ExecutionPolicy Bypass -File brain\voice-study.ps1",
                "Run or resume the whole study in its own window, from the vault folder. Same as the desktop shortcut.")]
              if script.exists() else []),
            ("sbrain voice status", "Where the study stands."),
            ("sbrain corpus", "Collect every message typed, from all local sessions and the export folders."),
            ('sbrain corpus --exports "D:\\path\\to\\exports"', "Also read a folder of web chat exports."),
            ("sbrain voice mark", "Mark every message not marked yet."),
            ("sbrain voice combine", "Turn the marks into counts, a sample, and the blind-test set."),
            ("sbrain voice write", "Write the draft profile from the findings."),
            ("sbrain voice test", "Build the blind test page from the profile."),
            ("sbrain voice weekly", "This week's blind test: new messages only, results logged in the vault."),
            ("sbrain voice newtest", "A blind test right now, topped up with older untested messages."),
        ]},
        {"title": "Phrase it better", "items": [
            ("sbrain phrasing status", "Weeks built, cards, and terms in use."),
            ("sbrain phrasing week --week this", "Cards for the week in progress; Monday's run builds it again when it ends."),
            ("sbrain phrasing week --week 2026-W40", "Build one week again; answers stay with any card it picks again."),
            ("sbrain phrasing weekly", "Build every finished week not built yet, as the Monday run does."),
        ]},
        {"title": "Codex account", "items": [
            ("codex logout", "Sign out, when one account is out of usage."),
            ("codex login", "Sign in with another account, then resume the study."),
        ]},
        {"title": "Setup", "items": [
            ("sbrain install", "Register the weekly run and the desktop shortcut (Windows)."),
            ("sbrain uninstall", "Remove both."),
        ]},
    ]
    return [{"title": g["title"], "items": [{"cmd": c, "what": w} for c, w in g["items"]]} for g in groups]
