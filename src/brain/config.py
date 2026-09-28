"""Vault location and settings.

The engine is generic; everything personal lives in the vault:
  <vault>/brain/config.toml     paths and git identities
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
    return Config(
        vault=vault,
        projects_root=Path(data.get("projects_root", "~/Projects")).expanduser(),
        sources={name: Path(p).expanduser() for name, p in sources.items() if p},
        git_authors=list(data.get("git_authors", [])),
    )
