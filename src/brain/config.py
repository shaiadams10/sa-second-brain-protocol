"""Vault location and settings.

The engine is generic; everything personal lives in the vault:
  <vault>/brain/config.toml     paths, git identities, and which CLI and model runs use
  <vault>/brain/projects.json   the owner's decisions about folders and real projects
  <vault>/.brain/               regenerable working files (digests), not committed
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SOURCES = {
    "codex": "~/.codex/sessions",
    "claude_code": "~/.claude/projects",
    "antigravity": "~/.gemini/antigravity/brain",
}


@dataclass
class Config:
    vault: Path
    projects_root: Path
    sources: dict[str, Path]
    git_authors: list[str] = field(default_factory=list)
    owner: str = "the owner"
    auto_commit: bool = True
    places: dict[str, str] = field(default_factory=dict)  # extra project folders outside projects_root
    # Projects or places whose conversations are about the owner (advice, goals, self-description),
    # read for what they say about the owner, not only for project work.
    about_me: list[str] = field(default_factory=list)
    model_cli: str = "agy"  # which CLI answers scheduled runs: "agy" or "codex"
    model_name: str | None = None  # None: newest Gemini Flash (agy) or the Codex default model
    model_effort: str | None = None  # reasoning effort, e.g. "medium"; None: the CLI's default
    schedule_day: str = "Monday"  # the weekly run, in the PC's local time
    schedule_time: str = "09:00"

    @property
    def work_dir(self) -> Path:
        return self.vault / ".brain"

    @property
    def decisions_file(self) -> Path:
        return self.vault / "brain" / "projects.json"

    def load_decisions(self) -> dict:
        if self.decisions_file.exists():
            return json.loads(self.decisions_file.read_text(encoding="utf-8"))
        return {"folders": {}, "marked": []}

    def save_decisions(self, decisions: dict) -> None:
        self.decisions_file.parent.mkdir(parents=True, exist_ok=True)
        self.decisions_file.write_text(json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")


def find_vault(explicit: str | None = None) -> Path:
    for candidate in (explicit, os.environ.get("BRAIN_VAULT")):
        if candidate:
            return Path(candidate).expanduser().resolve()
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / "brain" / "config.toml").exists():
            return folder
    raise SystemExit("No vault found. Pass --vault, set BRAIN_VAULT, or run inside the vault.")


def load(vault: Path) -> Config:
    path = vault / "brain" / "config.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    sources = {**DEFAULT_SOURCES, **data.get("sources", {})}
    model = data.get("model", {})
    schedule = data.get("schedule", {})
    return Config(
        vault=vault,
        projects_root=Path(data.get("projects_root", "~/Projects")).expanduser(),
        sources={name: Path(p).expanduser() for name, p in sources.items() if p},
        git_authors=list(data.get("git_authors", [])),
        owner=data.get("owner", "the owner"),
        auto_commit=bool(data.get("auto_commit", True)),
        places={name: str(Path(p).expanduser()) for name, p in data.get("places", {}).items()},
        about_me=list(data.get("about_me", [])),
        model_cli=model.get("cli") or "agy",
        model_name=model.get("name") or None,
        model_effort=model.get("effort") or None,
        schedule_day=schedule.get("day") or "Monday",
        schedule_time=schedule.get("time") or "09:00",
    )


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def save_settings(vault: Path, sections: dict[str, dict[str, str]]) -> None:
    """Set string values in config.toml, e.g. {"model": {"cli": "codex"}}, keeping every comment
    and every other line as the owner wrote it. Missing keys and sections are added."""
    path = vault / "brain" / "config.toml"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for section, values in sections.items():
        header = f"[{section}]"
        start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if start is None:
            lines += ["", header] + [f"{k} = {json.dumps(v)}" for k, v in values.items()]
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        for key, value in values.items():
            row = f"{key} = {json.dumps(value)}"
            found = next((i for i in range(start + 1, end)
                          if lines[i].split("=", 1)[0].strip() == key and not lines[i].lstrip().startswith("#")), None)
            if found is None:
                lines.insert(start + 1, row)
                end += 1
            else:
                lines[found] = row
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
