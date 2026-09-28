"""Find the projects inside the projects folder, and attribute paths to them.

A folder is one of:

- project      it has its own project files (.git, package.json, README, ...).
               Its subfolders are parts of it and are not scanned further.
- group        it has no project files of its own, but contains projects.
               Its children are tracked separately, grouped under its name.
- loose        no project files anywhere, but not empty (homework, a Blender scenes folder).
               Tracked as one low-weight item.
- copy         a worktree, backup, or legacy copy of a sibling (app.worktrees,
               app-backups). Its activity counts toward the original.

A project with only a README or .git at its root and several independent-looking
projects inside may really be a collection (an "Arduino Projects" repo). Files alone cannot
settle that, so it is scanned as one project and flagged for the owner to decide once.
Decisions live in the vault's brain/projects.json and always win over the guess.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MANIFESTS = {
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml", "go.mod",
    "platformio.ini", "CMakeLists.txt", "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
    "pom.xml", "build.gradle", "fxmanifest.lua", "Makefile", "deno.json", "composer.json",
}
MANIFEST_SUFFIXES = (".sln", ".csproj", ".vcxproj", ".ino", ".uproject", ".xcodeproj")
SOFT_MARKERS = {"readme.md", "readme", "readme.txt", "index.html", "main.py", "app.py"}
IGNORED_DIRS = {
    "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next", ".git",
    "target", ".idea", ".vscode", ".cache", "out", "bin", "obj", ".pytest_cache", ".turbo",
    ".svelte-kit", "coverage", "site-packages",
}
_COPY = re.compile(
    r"^(?P<base>.+?)(?:\.worktrees|[ _-]+backups?|[ _-]+legacy(?:[ _-]\d{4}-\d{2}-\d{2})?"
    r"|[ _-]+old|[ _-]+copy|\s-\sCopy(?:\s\(\d+\))?)$",
    re.I,
)
# Subfolder names that mean "part of one project", not "separate project".
PART_NAMES = {
    "src", "lib", "docs", "doc", "scripts", "test", "tests", "assets", "config", "configs",
    "app", "apps", "frontend", "backend", "api", "server", "client", "web", "ui", "dashboard",
    "deployment", "deploy", "integration", "evaluation", "knowledge", "infra", "packages", "tools",
    "data", "models", "notebooks", "examples", "public", "static", "landing-page", "services",
}
MAX_DEPTH = 3


@dataclass
class Project:
    id: str  # path relative to the projects folder, with "/" separators
    name: str
    path: Path
    kind: str  # "project" | "loose" | "historical" (a folder that no longer exists)
    group: str | None = None
    has_git: bool = False
    aliases: list[Path] = field(default_factory=list)  # worktrees, backups, legacy copies
    needs_review: str | None = None  # why the owner should confirm how this folder is treated
    subprojects: list[str] = field(default_factory=list)  # named parts tracked inside one project


@dataclass
class Catalog:
    root: Path
    projects: dict[str, Project] = field(default_factory=dict)
    groups: set[str] = field(default_factory=set)
    _index: list[tuple[str, str]] = field(default_factory=list)

    def resolve(self, path: str | os.PathLike | None) -> str | None:
        """Return the id of the most specific project or group containing path.

        History outlives folder layouts. A path under the projects root that no longer
        exists is matched by folder name to where that folder lives now (a project moved
        into a group). If nothing matches, it becomes a historical project under its old
        name, which the owner can merge later with a "part-of:<id>" folder rule."""
        if not path:
            return None
        target = _norm(path)
        for prefix, pid in self._index:
            if target == prefix or target.startswith(prefix + os.sep):
                return pid
        root = _norm(self.root)
        if not target.startswith(root + os.sep):
            return None
        # Take the name from the original path: normcase lowercases it on Windows.
        original = os.path.normpath(str(path).replace("/", os.sep))
        name = original[len(root) + 1:].split(os.sep, 1)[0]
        return self._moved(name) or self._historical(name)

    def _moved(self, name: str) -> str | None:
        key = name.casefold()
        exact = [p.id for p in self.projects.values() if p.kind != "historical" and p.name.casefold() == key]
        if len(exact) == 1:
            return exact[0]
        if len(key) >= 4:  # a path cut short, e.g. "VSD Craft (StreamDeck"
            prefix = [p.id for p in self.projects.values() if p.kind != "historical" and p.name.casefold().startswith(key)]
            if len(prefix) == 1:
                return prefix[0]
        return None

    def _historical(self, name: str) -> str:
        for p in self.projects.values():
            if p.kind == "historical" and p.name.casefold() == name.casefold():
                return p.id
        self.projects[name] = Project(id=name, name=name, path=self.root / name, kind="historical")
        self.build_index()
        return name

    def subproject(self, path: str | os.PathLike | None, pid: str) -> str | None:
        """For a project tracked with sub-projects, name the one that path is inside."""
        project = self.projects.get(pid)
        if not path or not project or not project.subprojects:
            return None
        target = _norm(path)
        for root in (project.path, *project.aliases):
            base = _norm(root)
            if target.startswith(base + os.sep):
                first = target[len(base) + 1:].split(os.sep, 1)[0]
                for name in project.subprojects:
                    if os.path.normcase(name) == first:
                        return name
        return None

    def build_index(self) -> None:
        entries = []
        for pid, project in self.projects.items():
            entries.append((_norm(project.path), pid))
            entries.extend((_norm(alias), pid) for alias in project.aliases)
        for gid in self.groups:
            entries.append((_norm(self.root / gid), gid))
        self._index = sorted(entries, key=lambda e: len(e[0]), reverse=True)


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(str(path).replace("/", os.sep)))


def _subdirs(path: Path) -> list[Path]:
    try:
        return sorted(
            p for p in path.iterdir()
            if p.is_dir() and p.name.lower() not in IGNORED_DIRS and not p.name.startswith((".", "$"))
        )
    except OSError:
        return []


def _markers(path: Path) -> tuple[bool, bool, bool]:
    """(has .git, has a build/package manifest, has a soft marker like a README)."""
    git = manifest = soft = False
    try:
        for entry in path.iterdir():
            name = entry.name
            if name == ".git":
                git = True
            elif name in MANIFESTS or name.lower().endswith(MANIFEST_SUFFIXES):
                manifest = True
            elif name.lower() in SOFT_MARKERS:
                soft = True
    except OSError:
        pass
    return git, manifest, soft


def _is_project_dir(path: Path) -> bool:
    return any(_markers(path))


def _contains_projects(path: Path, depth: int) -> bool:
    if depth > MAX_DEPTH:
        return False
    return any(_is_project_dir(c) or _contains_projects(c, depth + 1) for c in _subdirs(path))


def _has_files(path: Path) -> bool:
    for _, _, files in os.walk(path):
        if files:
            return True
    return False


def _copy_base(name: str, siblings: set[str]) -> str | None:
    match = _COPY.match(name)
    if match and match.group("base") in siblings:
        return match.group("base")
    return None


def scan(root: Path, overrides: dict[str, str] | None = None, extra: dict[str, str] | None = None) -> Catalog:
    """Classify every folder under root. overrides maps folder id to "project",
    "project+subprojects", "collection", "ignore", or "part-of:<id>"."""
    overrides = overrides or {}
    catalog = Catalog(root=root)
    pending_aliases: list[tuple[Path, str]] = []

    def visit(folder: Path, group: str | None, depth: int) -> None:
        fid = folder.relative_to(root).as_posix()
        rule = overrides.get(fid, "")
        if rule == "ignore":
            return
        if rule.startswith("part-of:"):
            pending_aliases.append((folder, rule.removeprefix("part-of:")))
            return

        git, manifest, soft = _markers(folder)
        children = _subdirs(folder)
        marked = git or manifest or soft

        if not marked and not rule:
            parts = sum(c.name.lower() in PART_NAMES for c in children)
            members = [c for c in children if _is_project_dir(c) or _contains_projects(c, depth + 2)]
            if parts >= 2 or len(members) == 1:
                rule = "project"  # a project laid out in parts, or a wrapper around one project
            elif len(members) >= 2:
                rule = "collection"

        if rule == "collection":
            catalog.groups.add(fid)
            visit_children(folder, children, fid, depth + 1)
            return

        if rule == "project+subprojects":
            names = {c.name for c in children}
            subs = [c.name for c in children if (_is_project_dir(c) or _contains_projects(c, depth + 2))
                    and not _copy_base(c.name, names)]
            catalog.projects[fid] = Project(id=fid, name=folder.name, path=folder, kind="project",
                                            group=group, has_git=git, subprojects=subs)
            return

        if rule == "project" or marked:
            project = Project(id=fid, name=folder.name, path=folder, kind="project", group=group, has_git=git)
            if not rule and not manifest:
                names = {c.name for c in children}
                inner = [c.name for c in children
                         if any(_markers(c)[:2]) and not _copy_base(c.name, names)
                         and c.name.lower() not in PART_NAMES]
                if len(inner) >= 2:
                    project.needs_review = (
                        f"Has only a README or .git at its root and {len(inner)} projects inside "
                        f"({', '.join(inner[:4])}{'…' if len(inner) > 4 else ''}). One project, or a collection?"
                    )
            catalog.projects[fid] = project
            return

        if _has_files(folder):
            catalog.projects[fid] = Project(id=fid, name=folder.name, path=folder, kind="loose", group=group)

    def visit_children(folder: Path, children: list[Path], group: str | None, depth: int) -> None:
        names = {c.name for c in children}
        for child in children:
            cid = child.relative_to(root).as_posix()
            base = None if cid in overrides else _copy_base(child.name, names)
            if base:
                pending_aliases.append((child, (folder / base).relative_to(root).as_posix()))
            else:
                visit(child, group, depth)

    visit_children(root, _subdirs(root), None, 0)

    for fid, place in (extra or {}).items():
        catalog.projects[fid] = Project(id=fid, name=fid, path=Path(place), kind="project",
                                        has_git=(Path(place) / ".git").exists())
    for fid, rule in overrides.items():
        # "part-of" rules for folders that no longer exist merge their history into the target.
        if rule.startswith("part-of:") and not (root / fid).exists():
            pending_aliases.append((root / fid, rule.removeprefix("part-of:")))

    for alias, target in pending_aliases:
        if target in catalog.projects:
            catalog.projects[target].aliases.append(alias)
        elif target in catalog.groups:
            # A backup of a whole group: attribute its contents to the group.
            catalog.projects.setdefault(target, Project(id=target, name=Path(target).name,
                                                        path=root / target, kind="loose")).aliases.append(alias)
    catalog.build_index()
    return catalog
