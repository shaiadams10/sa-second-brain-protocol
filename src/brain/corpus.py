"""Everything the owner typed, for studying how they write. No AI.

`sbrain corpus` reads every conversation the sources hold, from the first one on, plus any web
chat exports, and writes <vault>/.brain/voice/corpus.jsonl: one record per unique message.

Kept out:
- sessions another agent started (subagent threads, scripted `codex exec` runs),
- injected context, attached files and terminals, slash commands with no words of their own,
  interruptions, and answers picked from an agent's multiple-choice questions,
- the contents of tagged pastes; a placeholder with the pasted length stays in their place.

Untagged pastes stay in: the marking step (`sbrain voice mark`) judges authorship from context
and from the quality of the English, which code cannot do reliably.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from brain.config import Config
from brain.sources import PARSERS
from brain.sources import web
from brain.text import clip, redact

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
PREV_LIMIT = 300

_PASTE = re.compile(r"\[pasted text\](.*?)(?:\[end of pasted text\]|$)", re.S)
_ATTACHED = re.compile(r"^\[attached [^\]]*\]\s*$", re.M)
_ANSWERING = re.compile(r"^\(answering: .*$", re.M)
_SLASH = re.compile(r"^/[\w:.-]+(?:\s+|$)")
_NON_LATIN = re.compile(r"[^\W\d_A-Za-zÀ-ɏ]")  # letters outside the Latin alphabets
_LETTER = re.compile(r"[^\W\d_]")


def voice_dir(cfg: Config) -> Path:
    return cfg.work_dir / "voice"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def message_id(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]


def clean(text: str) -> tuple[str, int]:
    """The owner's words in one message, and how many characters of tagged paste were removed."""
    pasted = 0

    def placeholder(match: re.Match) -> str:
        nonlocal pasted
        size = len(match.group(1).strip())
        pasted += size
        return f"[pasted: {size} characters]"

    if text == "(interrupted the assistant)":
        return "", 0
    text = _PASTE.sub(placeholder, text)
    text = _ATTACHED.sub("", text)
    text = _ANSWERING.sub("", text)
    slash = _SLASH.match(text)
    if slash:
        rest = text[slash.end():].strip()
        text = rest if len(rest.split()) >= 3 else ""
    text = redact(text).strip()
    if not _LETTER.search(re.sub(r"\[pasted: \d+ characters\]", "", text)):
        return "", pasted
    return text, pasted


def non_latin_share(text: str) -> float:
    letters = _LETTER.findall(text)
    return round(len(_NON_LATIN.findall(text)) / len(letters), 2) if letters else 0.0


@dataclass
class Collected:
    records: dict[str, dict] = field(default_factory=dict)
    excluded: Counter = field(default_factory=Counter)
    sessions: Counter = field(default_factory=Counter)
    unknown_files: list[str] = field(default_factory=list)
    web_pastes: int = 0


def _add(out: Collected, session, origin: str) -> None:
    out.sessions[origin] += 1
    prev = ""
    for exchange in session.exchanges:
        text, pasted = clean(exchange.user)
        if not text:
            out.excluded["no words of the owner's own"] += 1
            continue
        mid = message_id(text)
        record = out.records.get(mid)
        if record:
            record["repeats"] += 1
            if session.id not in record["sessions"]:
                record["sessions"].append(session.id)
            if exchange.at.isoformat() < record["at"]:
                record["at"] = exchange.at.isoformat()
        else:
            out.records[mid] = {
                "id": mid, "source": origin, "session": session.id, "cwd": session.cwd,
                "at": exchange.at.isoformat(), "text": text, "chars": len(text),
                "tagged_paste_chars": pasted, "non_latin": non_latin_share(text),
                "prev": clip(prev, PREV_LIMIT), "repeats": 1, "sessions": [session.id],
            }
        prev = text


def _add_samples(out: Collected, folder: Path) -> None:
    """Writing the owner did for people (posts, emails, messages), one file each or several split by a
    line of ---. Dated by the file's time; never pasted, so it is the strongest evidence of the voice."""
    from brain.model import Exchange, Session

    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in (".md", ".txt") or path.name.lower() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        at = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        session = Session(tool="samples", id=f"sample:{path.relative_to(folder).as_posix()}", source_file=str(path))
        session.exchanges = [Exchange(at=at, user=part.strip()) for part in re.split(r"^\s*---\s*$", text, flags=re.M)
                             if part.strip()]
        _add(out, session, "samples")


def collect(cfg: Config, exports: list[Path] | None = None) -> Collected:
    out = Collected()
    for name, root in cfg.sources.items():
        parser = PARSERS.get(name)
        if not parser or not root.exists():
            continue
        for path in parser.session_files(root, EPOCH):
            try:
                session = parser.parse(path)
            except (OSError, ValueError):
                out.excluded["unreadable session file"] += 1
                continue
            if session.by_agent:
                out.excluded["session started by another agent"] += len(session.exchanges)
                continue
            _add(out, session, session.tool)
    samples = cfg.vault / cfg.voice.get("samples", "brain/voice-samples")
    if samples.is_dir():
        _add_samples(out, samples)
    export_paths = [path for folder in exports or [] for path in web.export_files(folder)]
    for path in export_paths:
        sessions, kind = web.parse_file(path)
        if not kind:
            out.unknown_files.append(str(path))
            continue
        for session in sessions:
            _add(out, session, session.tool)
    if export_paths:
        out.web_pastes = strip_web_replies(out, (t for path in export_paths for t in web.assistant_texts(path)))
    return out


SHINGLE = 12  # words in a row that must match a web reply before the stretch counts as pasted
_TOKEN = re.compile(r"\S+")
_NORM = re.compile(r"[^\w]+")


def strip_web_replies(collected: Collected, replies) -> int:
    """Replace, in messages from local sessions, every stretch of 12+ words that also appears in an
    assistant's reply from the web exports with a pasted placeholder: the owner had a web chat write
    it and pasted it into a coding agent. Returns how many messages changed."""
    local = {k: r for k, r in collected.records.items() if r["source"] in ("codex", "claude-code", "antigravity")}
    index: dict[int, list[tuple[str, int]]] = {}
    tokens: dict[str, list] = {}
    for key, record in local.items():
        spans = [(m.start(), m.end(), _NORM.sub("", m.group().lower())) for m in _TOKEN.finditer(record["text"])]
        tokens[key] = spans
        words = [w for _, _, w in spans]
        for i in range(len(words) - SHINGLE + 1):
            index.setdefault(hash(tuple(words[i:i + SHINGLE])), []).append((key, i))
    if not index:
        return 0
    hits: dict[str, set] = {}
    for reply in replies:
        words = [_NORM.sub("", w.lower()) for w in _TOKEN.findall(reply or "")]
        for i in range(len(words) - SHINGLE + 1):
            for key, at in index.get(hash(tuple(words[i:i + SHINGLE])), ()):
                hits.setdefault(key, set()).update(range(at, at + SHINGLE))
    changed = 0
    for key, covered in hits.items():
        record, spans = local[key], tokens[key]
        text, out, pos, k = record["text"], [], 0, 0
        while k < len(spans):
            if k in covered:
                j = k
                while j + 1 < len(spans) and j + 1 in covered:
                    j += 1
                start, end = spans[k][0], spans[j][1]
                out.append(text[pos:start])
                out.append(f"[pasted: {end - start} characters]")
                record["tagged_paste_chars"] += end - start
                pos, k = end, j + 1
            else:
                k += 1
        out.append(text[pos:])
        new = "".join(out).strip()
        del collected.records[key]
        if not _LETTER.search(re.sub(r"\[pasted: \d+ characters\]", "", new)):
            collected.excluded["pasted from a web chat"] += 1
            continue
        record.update(text=new, chars=len(new), id=message_id(new), web_paste=True)
        collected.records.setdefault(record["id"], record)
        changed += 1
    return changed


def write(cfg: Config, collected: Collected) -> Path:
    folder = voice_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "corpus.jsonl"
    records = sorted(collected.records.values(), key=lambda r: (r["source"], r["session"], r["at"]))
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    by_source = Counter(r["source"] for r in records)
    summary = {
        "built": datetime.now().astimezone().isoformat(timespec="seconds"),
        "messages": len(records),
        "by_source": dict(by_source),
        "sessions": dict(collected.sessions),
        "first": min((r["at"] for r in records), default=None),
        "last": max((r["at"] for r in records), default=None),
        "characters": sum(r["chars"] for r in records),
        "tagged_paste_characters_removed": sum(r["tagged_paste_chars"] for r in records),
        "mostly_non_latin": sum(1 for r in records if r.get("non_latin", 0) >= 0.5),
        "excluded": dict(collected.excluded),
        "unrecognized_export_files": collected.unknown_files,
        "local_messages_with_web_pastes_removed": collected.web_pastes,
    }
    (folder / "corpus-summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    return path


def load(cfg: Config) -> list[dict]:
    path = voice_dir(cfg) / "corpus.jsonl"
    if not path.exists():
        raise SystemExit("No corpus yet. Run `sbrain corpus` first.")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
