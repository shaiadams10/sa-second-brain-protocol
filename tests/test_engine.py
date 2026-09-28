import json
import tempfile
import unittest
from pathlib import Path

from brain.digest import signal, week_bounds
from brain.knowledge import Knowledge
from brain.projects import scan
from brain.sources import antigravity, claude_code, codex
from brain.text import clip, redact


def touch(root: Path, *paths: str) -> None:
    for rel in paths:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records), encoding="utf-8")
    return path


class ProjectScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        touch(
            self.root,
            "App/package.json", "App/api/package.json", "App/web/package.json",
            "App.worktrees/feature/package.json",
            "Tools/Clipper/pyproject.toml", "Tools/Resizer/README.md", "Tools/notes/todo.txt",
            "Split/src/main.c", "Split/docs/guide.txt",
            "Boards/README.md", "Boards/weather/platformio.ini", "Boards/clock/platformio.ini",
            "Homework/essay.docx",
        )
        (self.root / "Empty").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_classification(self) -> None:
        catalog = scan(self.root)
        self.assertEqual(catalog.groups, {"Tools"})
        self.assertEqual(catalog.projects["Tools/Clipper"].group, "Tools")
        self.assertEqual(catalog.projects["Tools/notes"].kind, "loose")
        self.assertEqual(catalog.projects["Split"].kind, "project")  # parts, not separate projects
        self.assertEqual(catalog.projects["Homework"].kind, "loose")
        self.assertNotIn("App/api", catalog.projects)  # a project's subfolders are parts of it
        self.assertNotIn("Empty", catalog.projects)
        self.assertIsNotNone(catalog.projects["Boards"].needs_review)
        self.assertIsNone(catalog.projects["App"].needs_review)  # has a manifest: a monorepo

    def test_copies_and_resolution(self) -> None:
        catalog = scan(self.root)
        self.assertNotIn("App.worktrees", catalog.projects)
        self.assertEqual(catalog.resolve(self.root / "App.worktrees" / "feature" / "x.js"), "App")
        self.assertEqual(catalog.resolve(str(self.root / "tools" / "clipper").upper()), "Tools/Clipper")
        self.assertEqual(catalog.resolve(self.root / "Tools"), "Tools")
        self.assertIsNone(catalog.resolve("Z:/elsewhere"))

    def test_history_outlives_folder_layout(self) -> None:
        catalog = scan(self.root, {"OldApp": "part-of:App"})
        # moved into a group since: matched by name
        self.assertEqual(catalog.resolve(self.root / "Clipper" / "main.py"), "Tools/Clipper")
        # deleted and merged by the owner's rule
        self.assertEqual(catalog.resolve(self.root / "OldApp" / "src"), "App")
        # deleted, unknown: a historical project that keeps its original casing
        self.assertEqual(catalog.resolve(str(self.root / "Retired Thing" / "a.txt")), "Retired Thing")
        self.assertEqual(catalog.projects["Retired Thing"].kind, "historical")

    def test_extra_places(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            catalog = scan(self.root, {}, {"Vault": other})
            self.assertEqual(catalog.resolve(Path(other) / "notes.md"), "Vault")

    def test_decisions_override_guesses(self) -> None:
        catalog = scan(self.root, {"Boards": "collection", "Homework": "ignore"})
        self.assertIn("Boards", catalog.groups)
        self.assertIn("Boards/weather", catalog.projects)
        self.assertNotIn("Homework", catalog.projects)


class SourceParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_codex(self) -> None:
        def msg(ts, role, text, **extra):
            kind = "input_text" if role == "user" else "output_text"
            return {"timestamp": ts, "type": "response_item",
                    "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}], **extra}}
        path = jsonl(self.root / "rollout-x.jsonl", [
            {"timestamp": "2026-09-21T10:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "D:\\P\\App"}},
            msg("2026-09-21T10:00:01Z", "user", "# AGENTS.md instructions for D:\\P\\App\n..."),
            msg("2026-09-21T10:00:02Z", "user", "<environment_context>x</environment_context>"),
            msg("2026-09-21T10:00:03Z", "user", "build the login page"),
            msg("2026-09-21T10:00:04Z", "assistant", "Working on it", phase="commentary"),
            {"timestamp": "2026-09-21T10:00:05Z", "type": "response_item", "payload": {"type": "function_call"}},
            msg("2026-09-21T10:00:06Z", "assistant", "Done: login page added", phase="final_answer"),
            msg("2026-09-21T10:00:07Z", "assistant", "one more note", phase="commentary"),
            msg("2026-09-21T10:01:00Z", "user", "gj"),
        ])
        session = codex.parse(path)
        self.assertEqual(session.cwd, "D:\\P\\App")
        self.assertEqual([e.user for e in session.exchanges], ["build the login page", "gj"])
        self.assertEqual(session.exchanges[0].reply, "Done: login page added")
        self.assertEqual(session.exchanges[0].tool_calls, 1)

    def test_codex_strips_ide_context(self) -> None:
        user = "# Context from my IDE setup:\n\n## Active file: src/app.py\n\n## My request for Codex:\nFix the login form"
        path = jsonl(self.root / "rollout-ide.jsonl", [
            {"timestamp": "2026-09-21T10:00:00Z", "type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": user}]}},
        ])
        self.assertEqual([e.user for e in codex.parse(path).exchanges], ["Fix the login form"])

    def test_claude_code(self) -> None:
        path = jsonl(self.root / "proj" / "s2.jsonl", [
            {"type": "user", "cwd": "D:\\P\\App", "timestamp": "2026-09-21T10:00:00Z",
             "message": {"content": "fix the header"}},
            {"type": "assistant", "timestamp": "2026-09-21T10:00:01Z",
             "message": {"content": [{"type": "thinking", "thinking": "..."}, {"type": "tool_use"}]}},
            {"type": "user", "timestamp": "2026-09-21T10:00:02Z", "message": {"content": [{"type": "tool_result"}]}},
            {"type": "user", "isMeta": True, "timestamp": "2026-09-21T10:00:02Z", "message": {"content": "injected"}},
            {"type": "assistant", "isSidechain": True, "timestamp": "2026-09-21T10:00:03Z",
             "message": {"content": [{"type": "text", "text": "subagent chatter"}]}},
            {"type": "assistant", "timestamp": "2026-09-21T10:00:04Z",
             "message": {"content": [{"type": "text", "text": "Header fixed."}]}},
            {"type": "user", "timestamp": "2026-09-21T10:01:00Z", "message": {"content": "[Request interrupted by user]"}},
        ])
        session = claude_code.parse(path)
        self.assertEqual(session.cwd, "D:\\P\\App")
        self.assertEqual([e.user for e in session.exchanges], ["fix the header", "(interrupted the assistant)"])
        self.assertEqual(session.exchanges[0].reply, "Header fixed.")
        self.assertEqual(session.exchanges[0].tool_calls, 1)

    def test_antigravity(self) -> None:
        path = jsonl(self.root / "conv1" / ".system_generated" / "logs" / "transcript.jsonl", [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "created_at": "2026-09-21T10:00:00Z",
             "content": "<USER_REQUEST>\nadd a speedometer\n</USER_REQUEST>\n<ADDITIONAL_METADATA>time</ADDITIONAL_METADATA>"},
            {"type": "PLANNER_RESPONSE", "source": "MODEL", "created_at": "2026-09-21T10:00:01Z",
             "tool_calls": [{"name": "grep_search", "args": {"SearchPath": '"d:\\\\Projects\\\\App"'}}]},
            {"type": "PLANNER_RESPONSE", "source": "MODEL", "created_at": "2026-09-21T10:00:02Z",
             "content": "Here is the speedometer proposal."},
        ])
        session = antigravity.parse(path)
        self.assertEqual(session.id, "conv1")
        self.assertEqual(session.exchanges[0].user, "add a speedometer")
        self.assertEqual(session.exchanges[0].reply, "Here is the speedometer proposal.")
        self.assertEqual(session.path_hints.most_common(1)[0][0], "d:\\Projects\\App")


class KnowledgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Knowledge(Path(self.tmp.name) / "knowledge.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _week(self, week: str, results: dict[str, list[dict]]) -> None:
        self.k.begin_week(week)
        for pid, observations in results.items():
            self.k.record_project(week, pid, pid, None, "project", [], {"attention": 5.0})
            self.k.apply_project_result(week, pid, {"blurb": "", "week_summary": "", "project_notes": [],
                                                    "observations": observations})
        self.k.apply_synthesis(week, {"headline": "", "summary": "", "highlights": [], "merges": []},
                               "m", False, {})

    @staticmethod
    def obs(text: str, scope: str = "me", reinforces: str = "") -> dict:
        return {"scope": scope, "kind": "preference", "text": text, "evidence": "q", "reinforces": reinforces}

    def test_promotion_needs_recurrence(self) -> None:
        self._week("2026-W38", {"A": [self.obs("Likes previews")], "B": [self.obs("One-off", "ephemeral")]})
        (item_id, item), = self.k.items.items()
        self.assertEqual(item["status"], "candidate")
        self._week("2026-W39", {"B": [self.obs("Likes previews again", reinforces=item_id)]})
        self.assertEqual(self.k.items[item_id]["status"], "active")

    def test_rerun_does_not_double_count(self) -> None:
        self._week("2026-W39", {"A": [self.obs("Likes previews")]})
        item_id = next(iter(self.k.items))
        self._week("2026-W39", {"A": [self.obs("Likes previews", reinforces=item_id)]})
        self.assertNotIn(item_id, self.k.items)  # its only evidence was the re-run week
        self.assertEqual(len(self.k.items), 1)
        self.assertEqual(next(iter(self.k.items.values()))["status"], "candidate")

    def test_removed_items_stay_removed(self) -> None:
        self._week("2026-W38", {"A": [self.obs("Wrong claim")]})
        item_id = next(iter(self.k.items))
        self.k.remove_item(item_id)
        self._week("2026-W39", {"B": [self.obs("Wrong claim"), self.obs("x", reinforces=item_id)]})
        self.assertEqual(self.k.items[item_id]["status"], "removed")
        self.assertEqual([i["text"] for i in self.k.items.values()], ["Wrong claim"])

    def test_status(self) -> None:
        from datetime import date
        for week in ("2026-W36", "2026-W37", "2026-W38"):
            self.k.record_project(week, "A", "A", None, "project", [], {"attention": 6.0, "active_days": []})
        self.assertEqual(self.k.status("A", {}, date(2026, 9, 24))["state"], "ongoing")
        self.assertEqual(self.k.status("A", {}, date(2026, 11, 20))["state"], "on-hold")
        self.assertEqual(self.k.status("A", {"unmarked": ["A"]}, date(2026, 9, 24))["state"], "exploring")
        self.assertEqual(self.k.status("Z", {"marked": ["Z"]}, date(2026, 9, 24))["state"], "ongoing")


class TextAndSignalTest(unittest.TestCase):
    def test_signals(self) -> None:
        self.assertEqual(signal("Gj seems to work perfectly! commit"), "praise")
        self.assertEqual(signal("nope still not working"), "correction")
        self.assertEqual(signal("why did u choose to integrate it?"), "correction")
        self.assertIsNone(signal("add a toggle, and don't show it in menus instead of the HUD"))
        self.assertIsNone(signal("how exactly would we do it?"))
        self.assertEqual(signal("(interrupted the assistant)"), "interrupt")

    def test_redact_and_clip(self) -> None:
        text = redact("key sk-ant-abcdefghijklmnopqrstuv and api_key=supersecretvalue ok")
        self.assertNotIn("abcdefghijklmnop", text)
        self.assertNotIn("supersecretvalue", text)
        self.assertIn("ok", text)
        clipped = clip("a" * 50 + "b" * 50, 20)
        self.assertTrue(clipped.startswith("a") and clipped.endswith("b"))

    def test_week_bounds(self) -> None:
        start, end = week_bounds("2026-W39")
        self.assertEqual((start.date().isoformat(), end.date().isoformat()), ("2026-09-21", "2026-09-28"))


if __name__ == "__main__":
    unittest.main()
