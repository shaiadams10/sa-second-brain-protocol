from __future__ import annotations

import math
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private-key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
        ),
    ),
    ("openai-compatible-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-secret", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{24,}\b")),
    (
        "authorization-secret",
        re.compile(
            r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*"
            r"(?:bearer|basic)\s+[A-Za-z0-9_./+=:-]{12,}"
        ),
    ),
    (
        "generic-secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|"
            r"token|secret|password|passwd)\s*[:=]\s*['\"]?"
            r"[A-Za-z0-9_./+=:-]{12,}"
        ),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]
SENSITIVE_FILE_NAME = re.compile(
    r"(?i)(?:^|[\\/])(?:"
    r"\.env(?:\.[^\\/]*)?|"
    r"(?:credentials?|secrets?)(?:\.[^\\/]*)?|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.[^\\/]*)?|"
    r"[^\\/]*\.(?:pem|key|p12|pfx|jks|keystore)|"
    r"[^\\/]*(?:driver[_ -]?licen[cs]e|passport|identity[_ -]?card|id[_ -]?card)[^\\/]*"
    r")$"
)
SENSITIVE_IDENTITY_KEYS = {
    "driver_license",
    "driver_licence",
    "passport",
    "identity_card",
    "id_card",
}

WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\|[A-Z]:/)(?:[^\s\]\[\)\(<>\"']+)")
HOME_PATH = re.compile(r"(?i)(?:C:\\Users\\|C:/Users/)[^\\/\s]+(?:[\\/][^\s\]\[\)\(<>\"']+)*")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s\]\[\)\(<>\"']+")
IP_ADDRESS = re.compile(
    r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)"
)
PHONE_NUMBER = re.compile(
    r"(?<![\d-])(?:\+\d{1,3}[\s.-]?)?"
    r"(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}(?!\d)"
)
CODE_BLOCK = re.compile(r"```[\s\S]*?```", re.MULTILINE)
UNIX_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:Users|home|var|etc|opt|srv|mnt|Volumes)/"
    r"[^\s\]\[\)\(<>\"']+"
)
MOJIBAKE_RUN = re.compile(r"[^\x00-\x7f]+")
MOJIBAKE_MARKERS = ("Ã", "Â", "â", "ð", "×", "�")
EXTERNAL_SESSION_TEXT_CHARS = 1200
EXTERNAL_SESSION_TOTAL_CHARS = 6000
EXTERNAL_SESSION_MESSAGE_LIMIT = 12


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
        lowered = {str(key).lower(): item for key, item in value.items()}
        descriptors = [
            str(lowered.get(key) or "")
            for key in (
                "filename",
                "file_name",
                "name",
                "path",
                "local_path",
                "source_path",
                "artifact_path",
            )
        ]
        if any(SENSITIVE_FILE_NAME.search(item) for item in descriptors if item) or (
            SENSITIVE_IDENTITY_KEYS & set(lowered)
        ):
            return {"sensitive_file": "[REDACTED_SENSITIVE_FILE]"}
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


def assert_model_packet_safe(value: Any) -> None:
    """Fail closed if a model-bound packet still contains a recognized secret."""

    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    findings = scan_text(encoded, "external-model-packet", block_paths=True)
    if findings:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise ValueError(f"External model packet blocked by privacy preflight: {kinds}")


def sanitize_external_text(text: str, *, max_chars: int) -> str:
    """Apply the fail-closed redaction policy used for external model packets."""

    sanitized = sanitize_text(text, redact_email=True)
    sanitized = CODE_BLOCK.sub("[REDACTED_CODE_BLOCK]", sanitized)
    sanitized = URL.sub("[REDACTED_URL]", sanitized)
    sanitized = IP_ADDRESS.sub("[REDACTED_NETWORK]", sanitized)
    sanitized = PHONE_NUMBER.sub("[REDACTED_PHONE]", sanitized)
    sanitized = UNIX_PATH.sub("[LOCAL_PATH]", sanitized)
    return sanitize_text(sanitized, redact_email=True, max_chars=max_chars)


def _safe_session_messages(
    values: Any,
    *,
    remaining_chars: int,
) -> tuple[list[dict[str, str]], int, int]:
    if not isinstance(values, list) or remaining_chars <= 0:
        return [], 0, len(values) if isinstance(values, list) else 0
    result: list[dict[str, str]] = []
    used = 0
    considered = 0
    for value in values[:EXTERNAL_SESSION_MESSAGE_LIMIT]:
        if not isinstance(value, dict):
            continue
        considered += 1
        budget = min(EXTERNAL_SESSION_TEXT_CHARS, max(0, remaining_chars - used))
        if budget < 80:
            break
        text = sanitize_external_text(
            str(value.get("text") or ""),
            max_chars=budget,
        ).strip()
        if not text:
            continue
        result.append(
            {
                "occurred_at": re.sub(
                    r"[^0-9TZ:+.-]",
                    "",
                    str(value.get("occurred_at") or ""),
                )[:80],
                "text": text,
            }
        )
        used += len(text)
    omitted = max(0, len(values) - considered)
    return result, used, omitted


def sanitize_model_payload(kind: str, payload: Any) -> Any:
    """Allowlist session-digest fields before any external model sees them."""

    if kind == "owner_answer_draft" and isinstance(payload, dict):
        return {
            "question_id": str(payload.get("question_id") or "")[:80],
            "question": sanitize_external_text(
                str(payload.get("question") or ""),
                max_chars=2000,
            ),
            "answer": sanitize_external_text(
                str(payload.get("answer") or ""),
                max_chars=2000,
            ),
            "scope": (
                "project" if payload.get("scope") == "project" else "profile"
            ),
            "project_ids": [
                sanitize_external_text(str(value), max_chars=100)
                for value in payload.get("project_ids", [])[:4]
                if value
            ],
        }
    if kind != "session_digest" or not isinstance(payload, dict):
        return sanitize_packet(payload)

    safe: dict[str, Any] = {}
    for key in (
        "source",
        "project_ids",
        "analysis_lane",
        "attribution_status",
        "started_at",
        "ended_at",
        "record_counts",
        "tool_usage",
        "omitted_counts",
    ):
        if key in payload:
            safe[key] = sanitize_packet(payload[key])

    remaining = EXTERNAL_SESSION_TOTAL_CHARS
    user_messages, user_chars, user_omitted = _safe_session_messages(
        payload.get("user_messages"),
        remaining_chars=remaining,
    )
    remaining -= user_chars
    assistant_results, assistant_chars, assistant_omitted = _safe_session_messages(
        payload.get("assistant_results"),
        remaining_chars=remaining,
    )
    safe["user_messages"] = user_messages
    safe["assistant_results"] = assistant_results
    safe["privacy_summary"] = {
        "policy": "sanitized-session-digest-v3",
        "raw_session_id_removed": True,
        "artifact_text_removed": True,
        "user_messages_included": len(user_messages),
        "assistant_results_included": len(assistant_results),
        "additional_messages_omitted": user_omitted + assistant_omitted,
        "included_text_chars": user_chars + assistant_chars,
    }
    return safe


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
