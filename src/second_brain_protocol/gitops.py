from __future__ import annotations

import json
import os
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .config import RuntimePaths, load_runtime_config
from .security import Finding, scan_text, scan_tree
from .state import StateStore


PUBLIC_BRANCH = "automation/protocol-publish"
PUBLIC_PROTOCOL_FINGERPRINT_KEY = "public_protocol_fingerprint"
GITHUB_ED25519_KNOWN_HOST = (
    "github.com ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)
PRIVATE_COMMIT_PATHS = (
    "Identity",
    "Experience",
    "Projects",
    "Skills",
    "Memory",
    "Goals",
    "Journal",
    "Inbox/Review",
    "Evidence/VoiceSamples",
    "Evidence/Certificates",
    "System",
    "Protocol",
    "Templates",
    "Archive",
    ".agents",
    ".obsidian/core-plugins.json",
    ".obsidian/daily-notes.json",
    ".obsidian/templates.json",
    "AGENTS.md",
    "README.md",
    ".gitignore",
    ".gitattributes",
)


class GitPolicyError(RuntimeError):
    pass


def _run(cwd: Path, *args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise GitPolicyError(result.stderr.strip() or result.stdout.strip() or "Command failed")
    return result


def initialize_local_repository(vault: Path) -> None:
    config = load_runtime_config()
    if not (vault / ".git").exists():
        _run(vault, "git", "init", "-b", "main")
    _run(vault, "git", "config", "user.name", config["git_name"])
    _run(vault, "git", "config", "user.email", config["git_email"])
    _run(vault, "git", "config", "core.autocrlf", "false")


def _content_findings(vault: Path) -> list[Finding]:
    findings = scan_tree(vault, block_paths=False)
    for path in vault.rglob("*.md"):
        if ".git" in path.parts or "Protocol" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        findings.extend(scan_text(text, path.relative_to(vault).as_posix(), block_paths=True))
    return findings


def assert_private_safe(vault: Path) -> None:
    findings = _content_findings(vault)
    if findings:
        preview = "; ".join(f"{item.kind} in {item.location}" for item in findings[:10])
        raise GitPolicyError(f"Privacy scan blocked Git operation: {preview}")


def commit_if_changed(vault: Path, message: str, *, paths: Iterable[str] | None = None) -> bool:
    initialize_local_repository(vault)
    assert_private_safe(vault)
    if paths:
        _run(vault, "git", "add", "--", *paths)
    else:
        allowed = [
            item
            for item in PRIVATE_COMMIT_PATHS
            if (vault / item).exists()
            or bool(_run(vault, "git", "ls-files", "--", item).stdout.strip())
        ]
        _run(vault, "git", "add", "-A", "--", *allowed)
    staged = _run(vault, "git", "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        return False
    _run(vault, "git", "commit", "-m", message)
    return True


def snapshot_manual_markdown(vault: Path) -> bool:
    initialize_local_repository(vault)
    status = _run(vault, "git", "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
    paths: list[str] = []
    for line in status:
        relative = line[3:].strip().strip('"').replace("\\", "/")
        if relative.endswith(".md") and not relative.startswith(("Journal/", "Inbox/Review/", "Evidence/VoiceSamples/")):
            paths.append(relative)
    if not paths:
        return False
    return commit_if_changed(vault, "Snapshot manual vault edits", paths=paths)


def _active_gh_account() -> str:
    result = _run(Path.cwd(), "gh", "api", "user", "--jq", ".login", check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    # GitHub can return its HTML "Unicorn" 503 page from /user while the
    # locally selected credential and other API surfaces remain usable. In
    # that narrow case, preserve the wrong-account guard using gh's structured
    # local auth state instead of blocking every publication on /user.
    status = _run(Path.cwd(), "gh", "auth", "status", "--json", "hosts", check=False)
    if status.returncode == 0:
        try:
            entries = json.loads(status.stdout).get("hosts", {}).get("github.com", [])
        except json.JSONDecodeError:
            entries = []
        for entry in entries:
            error = str(entry.get("error", ""))
            if entry.get("active") and entry.get("login") and (
                entry.get("state") == "success" or "HTTP 503" in error
            ):
                return str(entry["login"])

    detail = result.stderr.strip() or result.stdout.strip() or "GitHub account lookup failed"
    raise GitPolicyError(detail)


def _ensure_account(expected: str) -> None:
    active = _active_gh_account()
    if active.casefold() != expected.casefold():
        raise GitPolicyError(f"GitHub CLI account is {active}, expected {expected}; refusing to switch accounts.")


def _ensure_ssh_identity(paths: RuntimePaths, repository: str, key_name: str) -> Path:
    config = load_runtime_config(paths)
    key = paths.root / "ssh" / key_name
    key.parent.mkdir(parents=True, exist_ok=True)
    if not key.exists():
        _run(paths.root, "ssh-keygen", "-t", "ed25519", "-N", "", "-C", config["git_email"], "-f", str(key))
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    listed = _run(
        paths.root,
        "gh",
        "api",
        f"repos/{repository}/keys",
        "--paginate",
        "--jq",
        ".[].key",
    )
    known = {" ".join(line.split()[:2]) for line in listed.stdout.splitlines() if line.strip()}
    identity = " ".join(public_key.split()[:2])
    if identity not in known:
        _run(
            paths.root,
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/keys",
            "-f",
            "title=Personal Second Brain automation",
            "-f",
            f"key={public_key}",
            "-F",
            "read_only=false",
        )
    return key


def _configure_ssh_remote(checkout: Path, key: Path, known_hosts: Path, repository: str) -> None:
    remote = f"git@github.com:{repository}.git"
    existing = _run(checkout, "git", "remote", "get-url", "origin", check=False)
    if existing.returncode == 0:
        _run(checkout, "git", "remote", "set-url", "origin", remote)
    else:
        _run(checkout, "git", "remote", "add", "origin", remote)
    _run(
        checkout,
        "git",
        "config",
        "core.sshCommand",
        f"ssh -i {key.as_posix()} -o IdentitiesOnly=yes "
        f"-o UserKnownHostsFile={known_hosts.as_posix()} -o StrictHostKeyChecking=yes",
    )


def _ensure_github_known_hosts(paths: RuntimePaths) -> Path:
    known_hosts = paths.root / "ssh" / "github-known-hosts"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    expected = GITHUB_ED25519_KNOWN_HOST + "\n"
    if not known_hosts.exists() or known_hosts.read_text(encoding="utf-8") != expected:
        known_hosts.write_text(expected, encoding="utf-8")
    return known_hosts


def ensure_private_remote(vault: Path, paths: RuntimePaths, repository: str) -> None:
    _ensure_account(repository.split("/", 1)[0])
    exists = _run(vault, "gh", "repo", "view", repository, "--json", "name", check=False)
    if exists.returncode != 0:
        _run(vault, "gh", "repo", "create", repository, "--private", "--description", "Private evidence-backed personal second brain")
    key = _ensure_ssh_identity(paths, repository, "personal-second-brain-ed25519")
    known_hosts = _ensure_github_known_hosts(paths)
    _configure_ssh_remote(vault, key, known_hosts, repository)


def safe_push_private(vault: Path) -> None:
    assert_private_safe(vault)
    _run(vault, "git", "fetch", "origin", timeout=600)
    remote_head = _run(vault, "git", "rev-parse", "--verify", "origin/main", check=False)
    if remote_head.returncode == 0:
        ancestor = _run(vault, "git", "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
        if ancestor.returncode != 0:
            raise GitPolicyError("Remote main diverged or is ahead; refusing automatic merge or force-push.")
    _run(vault, "git", "push", "-u", "origin", "main", timeout=600)


ALLOWLIST_DIRS = {
    ".github",
    "assets",
    "config",
    "docs",
    "prompts",
    "runbooks",
    "schemas",
    "scripts",
    "src",
    "templates",
    "tests",
}
ALLOWLIST_FILES = {
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    "LICENSE",
    "NOTICE.md",
    "OperatingContract.md",
    "pyproject.toml",
    "uv.lock",
    ".gitattributes",
}
PRIVATE_ONLY_EXPORT_FILES = {"tests/test_structure.py"}


AUTHORSHIP_FILES = {"LICENSE", "NOTICE.md", "README.md", "pyproject.toml"}


def _redaction_policy(protocol: Path) -> dict[str, Any]:
    path = protocol.parent / "System" / "PublicExportRedactions.json"
    if not path.exists():
        return {"replacements": {}, "preserve_in_authorship_files": [], "forbidden_terms": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _genericize(text: str, relative: Path, policy: dict[str, Any]) -> str:
    replacements = dict(policy.get("replacements", {}))
    protected: dict[str, str] = {}
    if relative.as_posix() in AUTHORSHIP_FILES:
        preserved_terms = sorted(
            policy.get("preserve_in_authorship_files", []), key=len, reverse=True
        )
        for index, term in enumerate(preserved_terms):
            replacements.pop(term, None)
            placeholder = f"\uf000SB_PRESERVE_{index}\uf001"
            if term in text:
                text = text.replace(term, placeholder)
                protected[placeholder] = term
    for source, target in replacements.items():
        text = text.replace(source, target)
    for placeholder, term in protected.items():
        text = text.replace(placeholder, term)
    if relative.as_posix() == "config/defaults.json":
        data = json.loads(text)
        data["runtime_directory_name"] = "PersonalSecondBrain"
        data["identity"] = {
            "confirmed_git_owners": ["YOUR_GITHUB_USER"],
            "confirmed_author_names": ["YOUR_NAME"],
            "confirmed_author_emails": ["YOUR_EMAIL@example.com"],
            "excluded_authors": [],
        }
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return text


def export_public_protocol(protocol: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    policy = _redaction_policy(protocol)
    for path in protocol.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(protocol)
        if relative.as_posix() in PRIVATE_ONLY_EXPORT_FILES:
            continue
        if relative.parts[0] not in ALLOWLIST_DIRS and relative.as_posix() not in ALLOWLIST_FILES:
            continue
        if path.suffix.lower() in {".md", ".json", ".toml", ".py", ".ps1", ".yaml", ".yml", ".txt"}:
            content = _genericize(path.read_text(encoding="utf-8", errors="strict"), relative, policy)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        else:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    forbidden = list(policy.get("forbidden_terms", []))
    violations = []
    for path in destination.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            relative = path.relative_to(destination)
            terms = list(forbidden)
            if relative.as_posix() not in AUTHORSHIP_FILES:
                terms.extend(policy.get("preserve_in_authorship_files", []))
            if any(term.casefold() in text.casefold() for term in terms):
                violations.append(path.relative_to(destination).as_posix())
            if path.suffix.lower() in {".md", ".json", ".toml", ".yaml", ".yml"}:
                if scan_text(text, relative.as_posix(), block_paths=True):
                    violations.append(relative.as_posix())
    findings = scan_tree(destination, block_paths=False)
    if violations or findings:
        raise GitPolicyError(f"Public export failed privacy policy: {violations}; findings={findings[:5]}")


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def sanitized_protocol_fingerprint(vault: Path, paths: RuntimePaths) -> str:
    destination = paths.root / "protocol-export-fingerprint"
    export_public_protocol(vault / "Protocol", destination)
    return _tree_fingerprint(destination)


def _run_protocol_tests(vault: Path) -> None:
    result = _run(
        vault,
        "uv",
        "run",
        "--project",
        str(vault / "Protocol"),
        "pytest",
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        raise GitPolicyError("Protocol tests failed before public publishing: " + (result.stdout + result.stderr)[-4000:])


def _protocol_commit_message() -> str:
    message = os.environ.get("SB_PROTOCOL_COMMIT_MESSAGE", "Publish sanitized protocol update").strip()
    if not message or "\n" in message or "\r" in message or len(message) > 120:
        raise GitPolicyError("Protocol commit message must be one non-empty line of at most 120 characters.")
    return message


def publish_protocol_draft(vault: Path, paths: RuntimePaths, repository: str) -> str:
    config = load_runtime_config(paths)
    _ensure_account(repository.split("/", 1)[0])
    export_root = paths.protocol_publish
    source_export = paths.root / "protocol-export-next"
    export_public_protocol(vault / "Protocol", source_export)
    exists = _run(vault, "gh", "repo", "view", repository, "--json", "name", check=False)
    if exists.returncode != 0:
        _run(vault, "gh", "repo", "create", repository, "--public", "--add-readme", "--description", "Reusable evidence-backed personal second-brain protocol")
    if not (export_root / ".git").exists():
        if export_root.exists():
            shutil.rmtree(export_root)
        _run(paths.root, "gh", "repo", "clone", repository, str(export_root), timeout=600)
    _run(export_root, "git", "fetch", "origin", timeout=600)
    prs = _run(export_root, "gh", "pr", "list", "--repo", repository, "--head", PUBLIC_BRANCH, "--state", "open", "--json", "url", "--jq", ".[0].url")
    branch_exists = _run(export_root, "git", "show-ref", "--verify", f"refs/remotes/origin/{PUBLIC_BRANCH}", check=False)
    if branch_exists.returncode == 0 and prs.stdout.strip():
        _run(export_root, "git", "switch", "-C", PUBLIC_BRANCH, f"origin/{PUBLIC_BRANCH}")
    else:
        _run(export_root, "git", "switch", "-C", PUBLIC_BRANCH, "origin/main")
    for child in export_root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(source_export, export_root, dirs_exist_ok=True)
    _run(export_root, "git", "config", "user.name", config["git_name"])
    _run(export_root, "git", "config", "user.email", config["git_email"])
    _run(export_root, "git", "add", "-A")
    if _run(export_root, "git", "diff", "--cached", "--quiet", check=False).returncode != 0:
        _run(export_root, "git", "commit", "-m", _protocol_commit_message())
        _run(export_root, "git", "push", "-u", "origin", PUBLIC_BRANCH, timeout=600)
    if prs.stdout.strip():
        return prs.stdout.strip()
    result = _run(
        export_root,
        "gh",
        "pr",
        "create",
        "--repo",
        repository,
        "--base",
        "main",
        "--head",
        PUBLIC_BRANCH,
        "--draft",
        "--title",
        "Publish sanitized second-brain protocol",
        "--body",
        "Generated from the private canonical Protocol/ tree. Privacy checks and protocol tests must pass before manual merge.",
    )
    return result.stdout.strip()


def sync_protocol_draft(
    vault: Path,
    paths: RuntimePaths,
    repository: str,
    store: StateStore,
    *,
    if_changed: bool,
) -> dict[str, str]:
    fingerprint = sanitized_protocol_fingerprint(vault, paths)
    if if_changed and store.get_meta(PUBLIC_PROTOCOL_FINGERPRINT_KEY) == fingerprint:
        return {"status": "unchanged"}
    _run_protocol_tests(vault)
    draft_pr = publish_protocol_draft(vault, paths, repository)
    store.set_meta(PUBLIC_PROTOCOL_FINGERPRINT_KEY, fingerprint)
    return {"status": "published", "draft_pr": draft_pr}
