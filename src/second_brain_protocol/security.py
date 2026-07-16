from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("generic-secret", re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\|[A-Z]:/)(?:[^\s\]\[\)\(<>\"']+)")
HOME_PATH = re.compile(r"(?i)(?:C:\\Users\\|C:/Users/)[^\\/\s]+(?:[\\/][^\s\]\[\)\(<>\"']+)*")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
MOJIBAKE_RUN = re.compile(r"[^\x00-\x7f]+")
MOJIBAKE_MARKERS = ("Ã", "Â", "â", "ð", "×", "�")


@dataclass(frozen=True)
class Finding:
    kind: str
    location: str
    excerpt: str


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8-as-Windows-1252 damage without touching normal Unicode."""

    def repair_run(match: re.Match[str]) -> str:
        current = match.group(0)
        for _ in range(2):
            before = sum(current.count(marker) for marker in MOJIBAKE_MARKERS)
            if before == 0:
                break
            try:
                candidate = current.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            after = sum(candidate.count(marker) for marker in MOJIBAKE_MARKERS)
            if after >= before:
                break
            current = candidate
        return current

    return MOJIBAKE_RUN.sub(repair_run, text)


def sanitize_text(text: str, *, redact_email: bool = False, max_chars: int | None = None) -> str:
    sanitized = repair_mojibake(text).replace("\x00", "")
    for _name, pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    sanitized = HOME_PATH.sub("[LOCAL_PATH]", sanitized)
    sanitized = WINDOWS_PATH.sub("[LOCAL_PATH]", sanitized)
    if redact_email:
        sanitized = EMAIL.sub("[REDACTED_EMAIL]", sanitized)
    tokens = re.findall(r"[A-Za-z0-9_+/=-]{32,}", sanitized)
    for token in tokens:
        if _entropy(token) >= 4.2 and not re.fullmatch(r"[a-fA-F0-9]{32,64}", token):
            sanitized = sanitized.replace(token, "[REDACTED_HIGH_ENTROPY]")
    if max_chars is not None and len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars] + "\n[TRUNCATED]"
    return sanitized


def sanitize_packet(value: Any) -> Any:
    """Recursively sanitize model-bound evidence and drop machine-only fields."""
    if isinstance(value, str):
        return sanitize_text(value, max_chars=6000)
    if isinstance(value, list):
        return [sanitize_packet(item) for item in value[:500]]
    if isinstance(value, dict):
        denied = {
            "local_path",
            "path",
            "credentials",
            "environment",
            "raw_tool_output",
            "source_evidence_ids",
        }
        return {
            str(key): sanitize_packet(item)
            for key, item in value.items()
            if str(key).lower() not in denied and not str(key).startswith("_")
        }
    return value


def scan_text(text: str, location: str, *, block_paths: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Finding(name, location, match.group(0)[:80]))
    if block_paths:
        for pattern in (HOME_PATH, WINDOWS_PATH):
            for match in pattern.finditer(text):
                findings.append(Finding("absolute-path", location, match.group(0)[:120]))
    return findings


def scan_tree(root: Path, *, block_paths: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or {".git", ".venv", "__pycache__", ".pytest_cache"} & set(path.parts):
            continue
        if path.suffix.lower() not in {".md", ".json", ".toml", ".yaml", ".yml", ".py", ".ps1", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(text, path.relative_to(root).as_posix(), block_paths=block_paths))
    return findings
