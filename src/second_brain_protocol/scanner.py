from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PRUNE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".claude",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
    ".next",
    ".turbo",
    ".cache",
    "target",
    "vendor",
    "site-packages",
    ".tox",
    ".nox",
    "__pycache__",
}

IGNORED_CONTROL_FILES = {"CLAUDE.md", "claude_desktop_config.json"}

TECH_MARKERS: dict[str, str] = {
    "package.json": "Node.js",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "Yarn",
    "bun.lockb": "Bun",
    "vite.config.ts": "Vite",
    "vite.config.js": "Vite",
    "next.config.js": "Next.js",
    "next.config.mjs": "Next.js",
    "next.config.ts": "Next.js",
    "tsconfig.json": "TypeScript",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "uv.lock": "uv",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "pom.xml": "Java/Maven",
    "build.gradle": "Java/Gradle",
    "build.gradle.kts": "Kotlin/Gradle",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
    "wrangler.toml": "Cloudflare Workers",
    "terraform.tf": "Terraform",
    "schema.prisma": "Prisma",
}

PROJECT_MARKER_FILES = set(TECH_MARKERS) | {
    "package-lock.json",
    "poetry.lock",
    "Pipfile",
    "environment.yml",
    "setup.py",
    "setup.cfg",
}
README_FILES = {"readme.md", "readme.mdx", "readme.rst", "readme.txt"}
GENERIC_CONTAINER_NAMES = {
    "app",
    "apps",
    "client",
    "code",
    "frontend",
    "lib",
    "libs",
    "packages",
    "server",
    "source",
    "src",
}

LANGUAGE_EXTENSIONS = {
    ".c": "C", ".cc": "C++", ".cpp": "C++", ".cs": "C#", ".css": "CSS",
    ".dart": "Dart", ".go": "Go", ".html": "HTML", ".java": "Java", ".js": "JavaScript",
    ".jsx": "JavaScript/JSX", ".kt": "Kotlin", ".php": "PHP", ".py": "Python", ".r": "R",
    ".rb": "Ruby", ".rs": "Rust", ".scss": "SCSS", ".sql": "SQL", ".svelte": "Svelte",
    ".swift": "Swift", ".ts": "TypeScript", ".tsx": "TypeScript/TSX", ".vue": "Vue",
}

CATEGORY_MARKERS = {
    "manifests": {"package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "composer.json", "Gemfile"},
    "lockfiles": {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "uv.lock", "poetry.lock", "Cargo.lock", "go.sum"},
    "databases_and_schemas": {"schema.prisma", "schema.sql", "migrations", "alembic.ini", "drizzle.config.ts"},
    "infrastructure": {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "terraform.tf", "Pulumi.yaml", "kustomization.yaml", "Chart.yaml"},
    "deployment": {"vercel.json", "netlify.toml", "wrangler.toml", "render.yaml", "fly.toml", "app.yaml"},
}


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = env.get("GIT_CONFIG_NOSYSTEM", "0")
    return env


def run_git(repo: Path, *args: str, timeout: int = 30) -> str:
    command = [
        "git",
        "-c",
        "core.preloadindex=false",
        "-c",
        "core.fscache=false",
        "-C",
        str(repo),
        *args,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_git_env(),
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def discover_git_roots(projects_root: Path, *, max_depth: int = 5) -> list[Path]:
    projects_root = projects_root.resolve()
    roots: list[Path] = []
    for current, directories, files in os.walk(projects_root):
        path = Path(current)
        depth = len(path.relative_to(projects_root).parts)
        is_git_root = ".git" in directories or ".git" in files
        directories[:] = [
            name
            for name in directories
            if name not in PRUNE_DIRS and not name.startswith(".worktree")
        ]
        if "pyvenv.cfg" in files:
            directories[:] = []
            continue
        if is_git_root:
            roots.append(path)
            directories[:] = []
            continue
        if depth >= max_depth:
            directories[:] = []
    return sorted(set(roots), key=lambda item: item.as_posix().lower())


def _has_filesystem_project_signature(files: list[str]) -> bool:
    names = set(files)
    lowered = {name.casefold() for name in files}
    if names & PROJECT_MARKER_FILES:
        return True
    source_files = [name for name in files if Path(name).suffix.casefold() in LANGUAGE_EXTENSIONS]
    return bool(lowered & README_FILES) and bool(source_files)


def discover_filesystem_roots(
    projects_root: Path,
    *,
    git_roots: Iterable[Path] = (),
    max_depth: int = 5,
) -> list[Path]:
    """Find bounded non-Git projects inside human grouping folders."""

    projects_root = projects_root.resolve()
    git_paths = {path.resolve() for path in git_roots}
    roots: list[Path] = []
    if not projects_root.exists():
        return roots
    for current, directories, files in os.walk(projects_root):
        path = Path(current).resolve()
        depth = len(path.relative_to(projects_root).parts)
        directories[:] = [
            name
            for name in directories
            if name not in PRUNE_DIRS and not name.startswith(".worktree")
        ]
        if "pyvenv.cfg" in files:
            directories[:] = []
            continue
        if path in git_paths:
            directories[:] = []
            continue
        if (
            depth
            and path.name.casefold() not in GENERIC_CONTAINER_NAMES
            and _has_filesystem_project_signature(files)
        ):
            roots.append(path)
            directories[:] = []
            continue
        if depth >= max_depth:
            directories[:] = []
    return sorted(set(roots), key=lambda item: item.as_posix().lower())


def _remote_owner(remote: str) -> str | None:
    if not remote:
        return None
    normalized = remote.replace("\\", "/").rstrip("/")
    match = re.search(r"(?:github\.com[:/])([^/]+)/", normalized, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _safe_remote(remote: str) -> str:
    if not remote:
        return ""
    return re.sub(r"(?i)^(https?://)[^/@\s]+@", r"\1", remote.strip())


def _tracked_files(repo: Path) -> list[str]:
    output = run_git(repo, "ls-files", "-z")
    return [item for item in output.split("\x00") if item and Path(item).name not in IGNORED_CONTROL_FILES]


def _fingerprint(repo: Path, tracked: Iterable[str], head_commit: str) -> str:
    digest = hashlib.sha256()
    digest.update(head_commit.encode("utf-8"))
    for relative in sorted(tracked):
        path = repo / relative
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def relevant_manifest(repo: Path, tracked: Iterable[str]) -> dict[str, tuple[int, int]]:
    manifest: dict[str, tuple[int, int]] = {}
    for relative in tracked:
        if any(part in PRUNE_DIRS for part in Path(relative).parts):
            continue
        path = repo / relative
        # Gitlink entries are represented by directories in the working tree.
        # Windows can report their directory size as either 0 or 4096 between
        # otherwise identical scans, which creates false project activity.  The
        # parent repository's Git head/commit delta already captures a changed
        # Gitlink pointer, so keep only a stable sentinel in the file manifest.
        if path.is_dir():
            manifest[relative.replace("\\", "/")] = (0, 0)
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        manifest[relative.replace("\\", "/")] = (stat.st_size, stat.st_mtime_ns)
    return manifest


def _package_tech(repo: Path, tracked: set[str]) -> set[str]:
    stack: set[str] = set()
    basenames = {Path(item).name for item in tracked}
    for marker, label in TECH_MARKERS.items():
        if marker in basenames or marker in tracked:
            stack.add(label)
    if "package.json" in basenames:
        package_paths = [item for item in tracked if Path(item).name == "package.json"][:20]
        for relative in package_paths:
            try:
                package = json.loads((repo / relative).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            dependencies = {
                **package.get("dependencies", {}),
                **package.get("devDependencies", {}),
            }
            mapping = {
                "react": "React",
                "vue": "Vue",
                "svelte": "Svelte",
                "next": "Next.js",
                "@angular/core": "Angular",
                "three": "Three.js",
                "gsap": "GSAP",
                "remotion": "Remotion",
                "express": "Express",
                "fastify": "Fastify",
                "prisma": "Prisma",
                "tailwindcss": "Tailwind CSS",
            }
            for dependency, label in mapping.items():
                if dependency in dependencies:
                    stack.add(label)
    return stack


def _second_brains(tracked: set[str]) -> list[str]:
    candidates = []
    for relative in tracked:
        lowered = relative.lower().replace("\\", "/")
        if any(
            marker in lowered
            for marker in (
                "second-brain/",
                "secondbrain/",
                "docs/second-brain/",
                "obsidian-second-brain/",
            )
        ):
            candidates.append(relative.replace("\\", "/"))
    return sorted(candidates)[:100]


def _language_counts(tracked: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for relative in tracked:
        language = LANGUAGE_EXTENSIONS.get(Path(relative).suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _artifact_categories(tracked: Iterable[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {key: [] for key in CATEGORY_MARKERS}
    categories.update({"tests": [], "documentation": [], "apis": []})
    for relative in tracked:
        normalized = relative.replace("\\", "/")
        path = Path(relative)
        basename = path.name
        parts = {part.casefold() for part in path.parts}
        for category, markers in CATEGORY_MARKERS.items():
            if basename in markers or any(marker.casefold() in parts for marker in markers):
                categories[category].append(normalized)
        if any(part in {"test", "tests", "spec", "specs", "__tests__"} for part in parts) or re.search(r"(?:^|/)(?:test_|.*\.(?:test|spec)\.)", normalized, re.IGNORECASE):
            categories["tests"].append(normalized)
        if path.suffix.lower() in {".md", ".mdx", ".rst"} or "docs" in parts:
            categories["documentation"].append(normalized)
        if any(part in {"api", "apis", "routes", "endpoints", "controllers"} for part in parts) or basename.casefold() in {"openapi.json", "openapi.yaml", "swagger.json", "swagger.yaml"}:
            categories["apis"].append(normalized)
    return {key: sorted(set(values))[:200] for key, values in categories.items()}


def _safe_excerpt(path: Path, *, max_chars: int = 5000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    from .security import sanitize_text

    return sanitize_text(text, redact_email=False, max_chars=max_chars)


def _project_excerpts(repo: Path, tracked: set[str], second_brain_files: list[str]) -> list[dict[str, str]]:
    priorities = []
    for name in ("README.md", "readme.md", "package.json", "pyproject.toml", "AGENTS.md"):
        priorities.extend(relative for relative in tracked if Path(relative).name == name)
    priorities.extend(second_brain_files)
    excerpts = []
    for relative in list(dict.fromkeys(priorities))[:40]:
        excerpt = _safe_excerpt(repo / relative)
        if excerpt:
            excerpts.append({"file": relative.replace("\\", "/"), "excerpt": excerpt})
    return excerpts


def _git_history(repo: Path, classification: str, defaults: dict[str, Any]) -> dict[str, Any]:
    count = run_git(repo, "rev-list", "--all", "--count")
    first = run_git(repo, "log", "--all", "--reverse", "--format=%aI", "-n", "1")
    last = run_git(repo, "log", "--all", "-1", "--format=%aI")
    recent_raw = run_git(repo, "log", "--all", "-n", "50", "--format=%H%x09%aI%x09%an%x09%ae%x09%s")
    confirmed_emails = {item.casefold() for item in defaults["identity"]["confirmed_author_emails"]}
    confirmed_names = {item.casefold() for item in defaults["identity"]["confirmed_author_names"]}
    recent = []
    confirmed_authored = 0
    for line in recent_raw.splitlines():
        parts = line.split("\t", 4)
        if len(parts) != 5:
            continue
        commit, timestamp, author, email, subject = parts
        authored = classification in {"first-party", "fork"} and (
            email.casefold() in confirmed_emails or author.casefold() in confirmed_names
        )
        confirmed_authored += int(authored)
        recent.append(
            {
                "commit": commit[:12],
                "timestamp": timestamp,
                "author": author,
                "email_confirmed_as_user": authored,
                "subject": subject[:500],
            }
        )
    return {
        "commit_count": int(count) if count.isdigit() else 0,
        "first_commit_at": first or None,
        "last_commit_at": last or None,
        "confirmed_user_commits_in_recent_window": confirmed_authored,
        "recent_commits": recent,
        "working_tree": run_git(repo, "status", "--porcelain=v1", "--untracked-files=all").splitlines()[:500],
    }


def _authors(repo: Path) -> list[dict[str, str]]:
    output = run_git(repo, "log", "--all", "--format=%an%x09%ae", "-n", "500")
    result: dict[tuple[str, str], dict[str, str]] = {}
    for line in output.splitlines():
        if "\t" not in line:
            continue
        name, email = line.split("\t", 1)
        result[(name, email)] = {"name": name, "email": email}
    return sorted(result.values(), key=lambda item: (item["name"].lower(), item["email"].lower()))


def _classification(remote: str, authors: list[dict[str, str]], defaults: dict[str, Any]) -> tuple[str, list[str]]:
    identity = defaults["identity"]
    reasons: list[str] = []
    owner = (_remote_owner(remote) or "").lower()
    confirmed_owners = {item.lower() for item in identity["confirmed_git_owners"]}
    confirmed_emails = {item.lower() for item in identity["confirmed_author_emails"]}
    excluded = {item.lower() for item in identity["excluded_authors"]}

    if owner and owner in confirmed_owners:
        reasons.append(f"confirmed remote owner: {owner}")
        return "first-party", reasons

    matching_email = [item for item in authors if item["email"].lower() in confirmed_emails]
    if owner and owner not in confirmed_owners and matching_email:
        reasons.append(
            f"unconfirmed organization remote with confirmed user authorship: {owner}"
        )
        return "review", reasons

    if owner and owner not in confirmed_owners:
        reasons.append(f"third-party remote owner: {owner}")
        return "third-party", reasons

    if matching_email:
        reasons.append("confirmed author email in repository without third-party remote")
        return "first-party", reasons

    excluded_matches = [item["name"] for item in authors if item["name"].lower() in excluded]
    if excluded_matches:
        reasons.append("excluded author name present without confirmed ownership")
    return "review", reasons or ["ownership could not be confirmed"]


def _lifecycle(path: Path) -> str:
    lowered = path.name.casefold()
    if any(token in lowered for token in ("archive", "archived", "deprecated", "legacy", "old")):
        return "archived"
    if any(token in lowered for token in ("playground", "sandbox", "experiment", "prototype", "spike")):
        return "experiment"
    return "active"


def _filesystem_manifest(root: Path) -> dict[str, tuple[int, int]]:
    manifest: dict[str, tuple[int, int]] = {}
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in PRUNE_DIRS]
        if "pyvenv.cfg" in files:
            directories[:] = []
            continue
        for name in files:
            if name in IGNORED_CONTROL_FILES:
                continue
            path = Path(current) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            manifest[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return manifest


@dataclass
class ProjectScanner:
    projects_root: Path
    defaults: dict[str, Any]
    ignored_paths: tuple[Path, ...] = ()
    collection_paths: tuple[Path, ...] = ()

    def _is_ignored(self, path: Path) -> bool:
        try:
            candidate = path.resolve()
        except OSError:
            return False
        for ignored in self.ignored_paths:
            try:
                candidate.relative_to(ignored.resolve())
                return True
            except (OSError, ValueError):
                continue
        return False

    def _configured_collections(self) -> list[Path]:
        """Return explicit organizational folders inside the configured root."""

        result: list[Path] = []
        root = self.projects_root.resolve()
        for configured in self.collection_paths:
            candidate = configured
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if (
                resolved != root
                and resolved.is_dir()
                and not self._is_ignored(resolved)
                and resolved not in result
            ):
                result.append(resolved)
        return sorted(result, key=lambda item: item.as_posix().casefold())

    def scan_repo(self, repo: Path) -> dict[str, Any]:
        tracked = _tracked_files(repo)
        tracked_set = set(tracked)
        remote = _safe_remote(run_git(repo, "remote", "get-url", "origin"))
        upstream = _safe_remote(run_git(repo, "remote", "get-url", "upstream"))
        head = run_git(repo, "rev-parse", "HEAD")
        initial = run_git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()
        initial_commit = initial[-1] if initial else ""
        authors = _authors(repo)
        classification, reasons = _classification(remote, authors, self.defaults)
        owner = (_remote_owner(remote) or "").lower()
        upstream_owner = (_remote_owner(upstream) or "").lower()
        confirmed_owners = {item.lower() for item in self.defaults["identity"]["confirmed_git_owners"]}
        if owner in confirmed_owners and upstream_owner and upstream_owner not in confirmed_owners:
            classification = "fork"
            reasons.append(f"upstream owned by {upstream_owner}")
        stable_key = f"{remote.casefold()}|{initial_commit}" if remote else initial_commit
        if not stable_key:
            stable_key = hashlib.sha256(json.dumps(authors, sort_keys=True).encode()).hexdigest()
        project_id = "project-" + hashlib.sha256(stable_key.encode()).hexdigest()[:16]
        manifest = relevant_manifest(repo, tracked)
        second_brain_files = _second_brains(tracked_set)
        return {
            "id": project_id,
            "name": repo.name,
            "local_path": str(repo),
            "remote_url": remote or None,
            "upstream_url": upstream or None,
            "remote_owner": _remote_owner(remote),
            "head_commit": head or None,
            "initial_commit": initial_commit or None,
            "classification": classification,
            "classification_reasons": reasons,
            "lifecycle": _lifecycle(repo),
            "authors": authors,
            "tech_stack": sorted(_package_tech(repo, tracked_set)),
            "second_brain_files": second_brain_files,
            "language_counts": _language_counts(tracked),
            "artifact_categories": _artifact_categories(tracked),
            "evidence_excerpts": _project_excerpts(repo, tracked_set, second_brain_files),
            "git_history": _git_history(repo, classification, self.defaults),
            "tracked_file_count": len(tracked),
            "fingerprint": _fingerprint(repo, tracked, head),
            "manifest": manifest,
        }

    def scan_non_git(self, root: Path, *, child_projects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        child_projects = child_projects or []
        manifest = {} if child_projects else _filesystem_manifest(root)
        tracked = set(manifest)
        if child_projects:
            classification = "collection"
            reasons = [f"contains {len(child_projects)} discovered projects"]
        else:
            lifecycle = _lifecycle(root)
            classification = lifecycle if lifecycle in {"archived", "experiment"} else "review"
            reasons = ["non-Git folder; ownership requires review"]
        fingerprint_source = {
            "children": sorted(item["id"] for item in child_projects),
            "manifest": manifest,
        }
        fingerprint = hashlib.sha256(json.dumps(fingerprint_source, sort_keys=True).encode()).hexdigest()
        try:
            relative = root.resolve().relative_to(self.projects_root.resolve())
        except ValueError:
            relative = Path(root.name)
        stable_name = root.name if len(relative.parts) == 1 else relative.as_posix()
        return {
            "id": "folder-" + hashlib.sha256(stable_name.casefold().encode()).hexdigest()[:16],
            "name": root.name,
            "local_path": str(root),
            "remote_url": None,
            "upstream_url": None,
            "remote_owner": None,
            "head_commit": None,
            "initial_commit": None,
            "classification": classification,
            "classification_reasons": reasons,
            "lifecycle": _lifecycle(root),
            "authors": [],
            "tech_stack": sorted(_package_tech(root, tracked)),
            "second_brain_files": _second_brains(tracked),
            "language_counts": _language_counts(tracked),
            "artifact_categories": _artifact_categories(tracked),
            "evidence_excerpts": _project_excerpts(root, tracked, _second_brains(tracked)),
            "git_history": None,
            "tracked_file_count": len(tracked),
            "fingerprint": fingerprint,
            "manifest": manifest,
            "child_project_ids": sorted(item["id"] for item in child_projects),
        }

    def scan_all(self) -> list[dict[str, Any]]:
        collection_roots = self._configured_collections()

        def inside_collection(path: Path) -> bool:
            resolved = path.resolve()
            return any(
                resolved == collection or resolved.is_relative_to(collection)
                for collection in collection_roots
            )

        git_roots = discover_git_roots(self.projects_root)
        git_projects = [
            self.scan_repo(repo)
            for repo in git_roots
            if not self._is_ignored(repo) and repo.resolve() not in collection_roots
        ]
        seen: dict[str, dict[str, Any]] = {}
        projects: list[dict[str, Any]] = []
        for project in git_projects:
            canonical = seen.get(project["id"])
            if canonical:
                project["canonical_project_id"] = canonical["id"]
                project["id"] = "duplicate-" + hashlib.sha256(project["local_path"].casefold().encode()).hexdigest()[:16]
                project["classification"] = "duplicate"
                project["classification_reasons"] = [f"same remote and initial commit as {canonical['name']}"]
            else:
                seen[project["id"]] = project
            projects.append(project)
        filesystem_projects = [
            self.scan_non_git(root)
            for root in discover_filesystem_roots(
                self.projects_root,
                git_roots=git_roots,
            )
            if not self._is_ignored(root)
        ]
        projects.extend(filesystem_projects)
        discovered_projects = git_projects + filesystem_projects

        # Only explicit organizational folders are collections. This avoids
        # treating a legitimate monorepo as a zero-count container merely
        # because it contains nested code. Direct children of a configured
        # collection are independently inventoried; the container itself is
        # always excluded from project counts.
        for collection in collection_roots:
            for child in sorted(
                (item for item in collection.iterdir() if item.is_dir()),
                key=lambda item: item.name.casefold(),
            ):
                if (
                    self._is_ignored(child)
                    or child.name in PRUNE_DIRS
                    or child.name.startswith(".worktree")
                    or (child / "pyvenv.cfg").is_file()
                ):
                    continue
                if any(
                    Path(project["local_path"]).resolve() == child.resolve()
                    for project in discovered_projects
                ):
                    continue
                nested = []
                for project in discovered_projects:
                    try:
                        relative = Path(project["local_path"]).resolve().relative_to(
                            child.resolve()
                        )
                    except ValueError:
                        continue
                    if relative.parts:
                        nested.append(project)
                project = self.scan_non_git(child, child_projects=nested)
                projects.append(project)
                discovered_projects.append(project)

            children = []
            for project in discovered_projects:
                try:
                    relative = Path(project["local_path"]).resolve().relative_to(
                        collection
                    )
                except ValueError:
                    continue
                if relative.parts:
                    children.append(project)
            projects = [
                project
                for project in projects
                if Path(project["local_path"]).resolve() != collection
            ]
            projects.append(self.scan_non_git(collection, child_projects=children))
            discovered_projects = list(projects)

        if self.projects_root.exists():
            for child in sorted((item for item in self.projects_root.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
                if self._is_ignored(child):
                    continue
                if child.resolve() in collection_roots or inside_collection(child):
                    continue
                if any(
                    Path(project["local_path"]).resolve() == child.resolve()
                    for project in discovered_projects
                ):
                    continue
                nested = []
                for project in discovered_projects:
                    try:
                        Path(project["local_path"]).resolve().relative_to(child.resolve())
                        nested.append(project)
                    except ValueError:
                        continue
                projects.append(self.scan_non_git(child, child_projects=nested))
        return sorted(projects, key=lambda item: (item["name"].casefold(), item["id"]))
