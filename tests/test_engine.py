import json
import os
import tempfile
import unittest
from pathlib import Path

from brain.digest import signal, week_bounds
from brain.knowledge import Knowledge, skill_standing
from brain.projects import scan
from brain.sources import antigravity, claude_code, codex
from brain.text import clip, is_injected, redact


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
        if os.name == "nt":  # Windows paths are case-insensitive; POSIX paths are not
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

    def test_returns_after_a_break(self) -> None:
        for week in ("2026-W10", "2026-W11", "2026-W20", "2026-W30"):
            self.k.record_project(week, "A", "A", None, "project", [], {"attention": 4.0})
        self.assertEqual(self.k.returns("A"), 2)


def skill(topic: str, level: str, reinforces: str = "") -> dict:
    return {"topic": topic, "level": level, "evidence": "q", "reinforces": reinforces}


def said(kind: str, text: str, wrong: str = "") -> dict:
    return {"kind": kind, "text": text, "wrong": wrong, "evidence": "q"}


class SkillsAndSaidTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Knowledge(Path(self.tmp.name) / "knowledge.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _week(self, week: str, pid: str = "A", learn: bool = False, **result) -> None:
        if learn:
            self.k.begin_learn(week)
        else:
            self.k.begin_week(week)
            self.k.record_project(week, pid, pid, None, "project", [], {"attention": 5.0})
        base = {"blurb": "", "week_summary": f"summary {week}", "project_notes": [], "observations": []}
        self.k.apply_project_result(week, pid, {**base, **result}, learn_only=learn)
        if learn:
            self.k.promote()
        else:
            self.k.apply_synthesis(week, {"headline": "", "summary": "", "highlights": [], "merges": []},
                                   "m", False, {})

    def _skill(self, topic: str) -> dict:
        return next(i for i in self.k.items.values() if i["kind"] == "skill" and i["text"] == topic)

    def test_skill_standing_follows_history(self) -> None:
        self._week("2026-W30", skills=[skill("Docker", "learning"), skill("Git", "directs"), skill("Rust", "directs")])
        self._week("2026-W31", skills=[skill("docker", "directs"), skill("Git", "directs")])  # topic case is ignored
        self.assertEqual(skill_standing(self._skill("Docker"))["level"], "growing")
        self.assertIn("asked about it in 2026-W30", skill_standing(self._skill("Docker"))["trend"])
        self.assertEqual(skill_standing(self._skill("Git"))["level"], "strong")
        self.assertEqual(skill_standing(self._skill("Rust"))["level"], "shown")
        self._week("2026-W32", skills=[skill("Docker", "directs"), skill("Rust", "struggled")])
        self.assertEqual(skill_standing(self._skill("Docker"))["level"], "strong")
        self.assertEqual(skill_standing(self._skill("Rust"))["level"], "struggled")

    def test_a_week_counts_at_its_worst_level(self) -> None:
        self._week("2026-W30", skills=[skill("Docker", "directs")])
        self._week("2026-W30", pid="B", skills=[skill("Docker", "learning")])  # same week, another project
        self.assertEqual(skill_standing(self._skill("Docker"))["level"], "learning")

    def test_stated_counts_at_once_and_corrections_strike(self) -> None:
        self._week("2026-W30", observations=[{"scope": "me", "kind": "preference", "text": "Likes dense layouts",
                                               "evidence": "q", "reinforces": ""}])
        self._week("2026-W31", said=[said("stated", "Photography is their main hobby"),
                                     said("correction", "Likes spacious layouts", wrong="Likes dense layouts"),
                                     said("correction", "Works alone", wrong="Works in a team")])
        by_text = {i["text"]: i for i in self.k.items.values()}
        self.assertEqual(by_text["Photography is their main hobby"]["status"], "active")
        self.assertEqual(by_text["Likes spacious layouts"]["status"], "active")
        self.assertEqual(by_text["Likes dense layouts"]["status"], "removed")
        self.assertEqual(by_text["Works in a team"]["status"], "removed")
        # a struck belief is never proposed again
        self._week("2026-W32", observations=[{"scope": "me", "kind": "work-style", "text": "Works in a team",
                                               "evidence": "q", "reinforces": ""}])
        self.assertEqual([i["status"] for i in self.k.items.values() if i["text"] == "Works in a team"], ["removed"])

    def test_goals_open_and_settle(self) -> None:
        self._week("2026-W30", said=[said("goal", "Wants to find a side income")])
        goal_id = next(k for k, v in self.k.items.items() if v["kind"] == "goal")
        self.assertTrue(self.k.items[goal_id]["open"])
        self._week("2026-W31", resolved_goals=[goal_id])
        self.assertFalse(self.k.items[goal_id]["open"])
        self._week("2026-W31", resolved_goals=[])  # re-running the week undoes it
        self.assertTrue(self.k.items[goal_id]["open"])

    def test_rerun_keeps_stated_items_active(self) -> None:
        self._week("2026-W30", said=[said("stated", "Fact")])
        self._week("2026-W31", said=[said("stated", "Fact")])
        self._week("2026-W31", said=[said("stated", "Fact")])
        (item,) = self.k.items.values()
        self.assertEqual(item["status"], "active")
        self.assertEqual(len(item["evidence"]), 2)

    def test_learn_pass_leaves_logs_alone(self) -> None:
        self._week("2026-W30", observations=[{"scope": "me", "kind": "preference", "text": "Likes previews",
                                               "evidence": "q", "reinforces": ""}])
        self._week("2026-W30", learn=True, week_summary="REWRITTEN", project_notes=[{"kind": "goal", "text": "n"}],
                   observations=[{"scope": "me", "kind": "preference", "text": "Another pref", "evidence": "q",
                                  "reinforces": ""},
                                 {"scope": "me", "kind": "communication", "text": "Writes tersely", "evidence": "q",
                                  "reinforces": ""}],
                   skills=[skill("Docker", "directs")])
        self.assertEqual(self.k.weeks["2026-W30"]["projects"]["A"]["summary"], "summary 2026-W30")
        self.assertEqual(self.k.projects["A"]["notes"], [])
        texts = sorted(i["text"] for i in self.k.items.values())
        self.assertEqual(texts, ["Docker", "Likes previews", "Writes tersely"])
        self._week("2026-W30", learn=True)  # learning the week again replaces what it learned
        self.assertEqual(sorted(i["text"] for i in self.k.items.values()), ["Likes previews"])

    def test_skills_never_merge_with_observations(self) -> None:
        self._week("2026-W30", skills=[skill("Docker", "directs")],
                   observations=[{"scope": "me", "kind": "interest", "text": "Containers", "evidence": "q",
                                  "reinforces": ""}])
        ids = {v["text"]: k for k, v in self.k.items.items()}
        self.k.apply_synthesis("2026-W30", {"headline": "", "summary": "", "highlights": [],
                                            "merges": [{"id": ids["Docker"], "same_as": ids["Containers"]}]},
                               "m", False, {})
        self.assertEqual(len(self.k.items), 2)

    def test_themes_keep_known_projects_only(self) -> None:
        for pid in ("A", "B"):
            self.k.record_project("2026-W30", pid, pid, None, "project", [], {"attention": 5.0})
        self.k.set_themes("2026-W30", [{"name": "Both", "summary": "s", "projects": ["a", "B", "Nope"]},
                                       {"name": "One", "summary": "s", "projects": ["A"]}])
        self.assertEqual(self.k.themes["items"], [{"name": "Both", "summary": "s", "projects": ["A", "B"]}])


class RenderTest(unittest.TestCase):
    def test_new_pages(self) -> None:
        from brain.projects import Catalog
        from brain.render import render_all
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            k = Knowledge(vault / "brain" / "knowledge.json")
            for week in ("2026-W30", "2026-W31"):
                k.begin_week(week)
                k.record_project(week, "A", "App", None, "project", [], {"attention": 5.0})
                k.apply_project_result(week, "A", {
                    "blurb": "", "week_summary": "s", "project_notes": [], "observations": [],
                    "skills": [skill("Docker", "learning" if week == "2026-W30" else "directs")],
                    "said": [said("goal", "Wants a side income"), said("stated", "Loves tinkering")],
                    "resolved_goals": []})
                k.apply_synthesis(week, {"headline": "h", "summary": "", "highlights": [], "merges": []}, "m", False, {})
            render_all(vault, k, {}, Catalog(vault), "Sam")
            skills = (vault / "me" / "skills.md").read_text(encoding="utf-8")
            self.assertIn("## Growing", skills)
            self.assertNotIn("## Asked about once", skills)
            self.assertIn("**Docker**: asked about it in 2026-W30, directing it by 2026-W31", skills)
            self.assertIn("Wants a side income", (vault / "me" / "open-questions.md").read_text(encoding="utf-8"))
            learned = (vault / "me" / "learned.md").read_text(encoding="utf-8")
            self.assertIn("## Stated by Sam", learned)
            self.assertNotIn("Docker", learned)
            self.assertIn("**Directed:** Docker", (vault / "log" / "2026-W31.md").read_text(encoding="utf-8"))
            self.assertTrue((vault / "me" / "themes.md").exists())


class TextAndSignalTest(unittest.TestCase):
    def test_signals(self) -> None:
        self.assertEqual(signal("Gj seems to work perfectly! commit"), "praise")
        self.assertEqual(signal("nope still not working"), "correction")
        self.assertEqual(signal("why did u choose to integrate it?"), "correction")
        self.assertIsNone(signal("add a toggle, and don't show it in menus instead of the HUD"))
        self.assertIsNone(signal("how exactly would we do it?"))
        self.assertEqual(signal("(interrupted the assistant)"), "interrupt")

    def test_learning_signal(self) -> None:
        self.assertEqual(signal("what is a docker volume?"), "learning")
        self.assertEqual(signal("how does the turnstile check work"), "learning")
        self.assertEqual(signal("i dont understand why it needs a token"), "learning")
        self.assertEqual(signal("what do u mean by worktree"), "learning")
        self.assertIsNone(signal("how do we split this into phases?"))
        self.assertIsNone(signal("what do u think, which one is better?"))

    def test_attachments_and_pastes(self) -> None:
        from brain.text import strip_injected
        text = ("<!-- attach: Terminal | tab:0 -->\n> PS D:\\x> git push\n> error: failed\nagain errors, fix it\n"
                "<pasted_content id=\"ab\">\nreport from another agent\n</pasted_content id=\"ab\">")
        self.assertEqual(strip_injected(text), "[attached Terminal]\nagain errors, fix it\n"
                                               "[pasted text]\nreport from another agent\n[end of pasted text]")

    def test_handoff_briefs_are_not_the_owner_speaking(self) -> None:
        self.assertTrue(is_injected("# Handoff: finish the backfill\n\nYou are continuing work..."))

    def test_conversations_about_the_owner_keep_more(self) -> None:
        from brain.digest import ABOUT_MAX, DigestExchange, MAX_PER_PROJECT, _select
        exchanges = [DigestExchange(at=f"2026-09-21T10:{n:02d}", tool="t", session="s", user="u", reply="",
                                    tool_calls=0) for n in range(100)]
        self.assertEqual(len(_select(exchanges)), 22)  # session edges plus an even sample
        self.assertEqual(len(_select(exchanges, ABOUT_MAX)), 100)
        self.assertLess(MAX_PER_PROJECT, ABOUT_MAX)

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


class AgentSessionDigestTest(unittest.TestCase):
    def test_agent_prompts_count_as_activity_not_words(self) -> None:
        from brain.config import Config
        from brain.digest import build

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "P", "App/package.json")
            app = str(root / "P" / "App")

            def rollout(name, source, day, text):
                return jsonl(root / "sessions" / f"rollout-{name}.jsonl", [
                    {"timestamp": f"2026-09-{day}T10:00:00Z", "type": "session_meta",
                     "payload": {"id": name, "cwd": app, "source": source}},
                    {"timestamp": f"2026-09-{day}T10:00:01Z", "type": "response_item",
                     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}},
                ])

            rollout("owner", "vscode", "15", "make the login page faster")
            rollout("sub", {"subagent": {"thread_spawn": {}}}, "17", "Audit the parser and report every defect.")
            cfg = Config(vault=root / "vault", projects_root=root / "P", sources={"codex": root / "sessions"})
            digest = build(cfg, "2026-W38")
            pw = next(p for p in digest.projects if p.name == "App")
            self.assertEqual([e.user for e in pw.exchanges], ["make the login page faster"])
            self.assertEqual(pw.exchange_count, 1)
            self.assertEqual(pw.sessions, {"codex": 1})
            self.assertEqual(pw.active_days, ["2026-09-15", "2026-09-17"])
