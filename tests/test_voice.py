import json
import os
import tempfile
import unittest
from pathlib import Path

from brain import corpus, voice
from brain.config import Config
from brain.sources import codex, web


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class CorpusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_clean_removes_pastes_and_commands(self) -> None:
        text, pasted = corpus.clean("what do u think\n[pasted text]You are an expert.[end of pasted text]")
        self.assertEqual(text, "what do u think\n[pasted: 18 characters]")
        self.assertEqual(pasted, 18)
        self.assertEqual(corpus.clean("/clear")[0], "")
        self.assertEqual(corpus.clean("/review check the login flow pls")[0], "check the login flow pls")
        self.assertEqual(corpus.clean("(interrupted the assistant)")[0], "")
        self.assertEqual(corpus.clean("(answering: Which one?) Option A")[0], "")

    def test_ids_ignore_case_and_spacing(self) -> None:
        self.assertEqual(corpus.message_id("Fix  the header"), corpus.message_id("fix the header"))

    def test_codex_subagent_sessions_are_flagged(self) -> None:
        lines = [
            {"timestamp": "2026-09-21T10:00:00Z", "type": "session_meta",
             "payload": {"id": "s9", "source": {"subagent": {"thread_spawn": {}}}}},
            {"timestamp": "2026-09-21T10:00:01Z", "type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "Audit the parser."}]}},
        ]
        path = self.root / "rollout-s9.jsonl"
        path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in lines), encoding="utf-8")
        self.assertTrue(codex.parse(path).by_agent)

    def test_web_exports(self) -> None:
        chatgpt = write_json(self.root / "gpt" / "conversations.json", [{
            "id": "c1", "title": "t", "mapping": {
                "a": {"message": {"author": {"role": "system"}, "content": {"content_type": "text", "parts": ["sys"]}}},
                "b": {"message": {"author": {"role": "user"}, "create_time": 1767225600,
                                  "content": {"content_type": "text", "parts": ["how does this work"]}}},
                "c": {"message": {"author": {"role": "user"}, "create_time": 1767225601,
                                  "content": {"content_type": "user_editable_context", "parts": ["about me"]}}},
                "d": {"message": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["x"]}}},
            }}])
        sessions, kind = web.parse_file(chatgpt)
        self.assertEqual(kind, "chatgpt")
        self.assertEqual([e.user for e in sessions[0].exchanges], ["how does this work"])

        claude = write_json(self.root / "claude" / "conversations.json", [{
            "uuid": "u1", "name": "n", "chat_messages": [
                {"sender": "human", "text": "make it shorter", "created_at": "2026-01-02T10:00:00Z"},
                {"sender": "assistant", "text": "ok", "created_at": "2026-01-02T10:00:01Z"}]}])
        sessions, kind = web.parse_file(claude)
        self.assertEqual(kind, "claude-ai")
        self.assertEqual([e.user for e in sessions[0].exchanges], ["make it shorter"])

        gemini = write_json(self.root / "takeout" / "My Activity.json", [
            {"header": "Gemini Apps", "title": "Prompted what is a vector", "time": "2026-01-03T10:00:00Z"},
            {"header": "Gemini Apps", "title": "Used an extension", "time": "2026-01-03T10:01:00Z"}])
        sessions, kind = web.parse_file(gemini)
        self.assertEqual(kind, "gemini")
        self.assertEqual([e.user for e in sessions[0].exchanges], ["what is a vector"])

        unknown = write_json(self.root / "other.json", {"settings": True})
        self.assertEqual(web.parse_file(unknown), ([], ""))

    def test_collect_dedupes_and_skips_agents(self) -> None:
        vault = self.root / "vault"
        cfg = Config(vault=vault, projects_root=self.root, sources={})
        write_json(self.root / "exports" / "conversations.json", [
            {"uuid": "u1", "chat_messages": [
                {"sender": "human", "text": "fix the header", "created_at": "2026-01-02T10:00:00Z"},
                {"sender": "human", "text": "Fix the  header", "created_at": "2026-01-03T10:00:00Z"},
                {"sender": "human", "text": "also make it blue", "created_at": "2026-01-03T10:00:05Z"}]}])
        collected = corpus.collect(cfg, [self.root / "exports"])
        corpus.write(cfg, collected)
        records = corpus.load(cfg)
        self.assertEqual(len(records), 2)
        first = next(r for r in records if r["text"] == "fix the header")
        self.assertEqual(first["repeats"], 2)
        blue = next(r for r in records if r["text"] == "also make it blue")
        self.assertEqual(blue["prev"], "Fix the  header")


class VoiceTest(unittest.TestCase):
    def test_batches_respect_limits(self) -> None:
        records = [{"id": str(i), "chars": 10000} for i in range(5)]
        self.assertEqual([len(b) for b in voice.batches(records)], [2, 2, 1])
        small = [{"id": str(i), "chars": 10} for i in range(90)]
        self.assertEqual([len(b) for b in voice.batches(small)], [40, 40, 10])

    def test_owner_text_uses_only_the_owners_words(self) -> None:
        record = {"text": "what do u think [pasted: 900 characters]"}
        self.assertEqual(voice.owner_text(record, {"authorship": "typed", "language": "english"}), "what do u think")
        self.assertEqual(voice.owner_text(record, {"authorship": "mixed", "language": "english",
                                                   "owner_text": "what do u think"}), "what do u think")
        self.assertIsNone(voice.owner_text(record, {"authorship": "pasted", "language": "english"}))
        self.assertIsNone(voice.owner_text(record, {"authorship": "typed", "language": "first_language"}))


if __name__ == "__main__":
    unittest.main()


class GuideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel, text in {"README.md": "# Vault\n", "me/profile.md": "# Profile\n", "log/2026-W01.md": "# W01\n",
                          "career/opportunities/x.md": "# Private\n", ".brain/voice/findings.md": "# Findings\n"}.items():
            path = self.vault / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.cfg = Config(vault=self.vault, projects_root=self.vault, sources={})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_documents_are_an_allowlist(self) -> None:
        from brain import guide
        paths = {d["path"] for d in guide.documents(self.cfg)}
        self.assertEqual(paths, {"README.md", "me/profile.md", ".brain/voice/findings.md"})
        self.assertEqual(guide.read_document(self.cfg, "me/profile.md")["title"], "Profile")
        for outside in ("log/2026-W01.md", "career/opportunities/x.md", "../secret.md", "brain/config.toml"):
            with self.assertRaises(KeyError):
                guide.read_document(self.cfg, outside)

    def test_voice_status_reads_the_log(self) -> None:
        from brain import guide
        folder = self.vault / ".brain" / "voice"
        (folder / "corpus-summary.json").write_text(json.dumps({"messages": 100}), encoding="utf-8")
        (folder / "marks.jsonl").write_text("{}\n" * 40, encoding="utf-8")
        (folder / "voice.log").write_text(
            "2026-10-03 02:32:40 mark: 0 of 100 messages already marked; 100 to go in 4 batches, codex m max, 2 in parallel\n"
            "2026-10-03 02:40:00 mark: batch 1 done. 1/4 batches, 40/100 messages marked (40%), 8m so far, about 24m left; "
            "tokens 1,000 in / 2,000 out\n", encoding="utf-8")
        status = guide.voice_status(self.cfg)
        self.assertFalse(status["run"]["running"])  # the log alone is not proof: no live process
        self.assertEqual(status["run"]["interrupted"], "mark")
        (folder / "active.json").write_text(json.dumps({"pid": os.getpid(), "step": "mark"}), encoding="utf-8")
        status = guide.voice_status(self.cfg)
        self.assertEqual((status["marked"], status["total"]), (40, 100))
        self.assertTrue(status["run"]["running"])
        self.assertEqual(status["run"]["eta"], "24m")
        (folder / "active.json").unlink()
        with (folder / "voice.log").open("a", encoding="utf-8") as fh:
            fh.write("2026-10-03 02:50:00 mark: stopped, the model account is out of usage or signed out: limit\n")
        self.assertFalse(guide.voice_status(self.cfg)["run"]["running"])
        self.assertIn("account_stop", guide.voice_status(self.cfg)["run"])


class BlindTestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.cfg = Config(vault=self.vault, projects_root=self.vault, sources={}, auto_commit=False)
        from brain import voicetest
        self.vt = voicetest
        voicetest.tests_dir(self.cfg).mkdir(parents=True)
        self.test = {"id": "t1", "kind": "weekly", "created": "2026-10-04T10:00:00+00:00", "profile": "me/writing-style.md",
                     "profile_version": "abc", "model": "m", "items": [
                         {"id": "a", "mode": "quick_question", "situation": "s", "real": "is it up", "generated": "is it up now",
                          "generated_clean": "", "real_side": "A"},
                         {"id": "b", "mode": "frustration", "situation": "s", "real": "still bad", "generated": "it looks awful",
                          "generated_clean": "", "real_side": "B"}]}
        (voicetest.tests_dir(self.cfg) / "t1.json").write_text(json.dumps(self.test), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_results_are_logged_without_message_text(self) -> None:
        self.assertEqual(self.vt.summary(self.cfg)["open"], "t1")
        r = self.vt.save_result(self.cfg, {"test": "t1", "note": "too tidy",
                                           "picks": [{"id": "a", "picked": "A"}, {"id": "b", "picked": "A", "note": "blank lines"}]})
        self.assertEqual((r["pairs"], r["fooled"]), (2, 1))
        log = (self.vault / self.vt.RESULTS).read_text(encoding="utf-8")
        self.assertNotIn("still bad", log)  # the owner's own message stays out of the vault log
        self.assertIn("it looks awful", log)  # the model's message is kept beside the note about it
        self.assertIn("| 2 | 1 | 50% |", (self.vault / self.vt.HISTORY).read_text(encoding="utf-8"))
        self.assertIsNone(self.vt.summary(self.cfg)["open"])
        self.assertIn("blank lines", self.vt.past_notes(self.cfg))
        self.assertEqual(self.vt.tested_ids(self.cfg), {"a", "b"})

    def test_bad_requests(self) -> None:
        with self.assertRaises(KeyError):
            self.vt.save_result(self.cfg, {"test": "../x", "picks": []})
        with self.assertRaises(ValueError):
            self.vt.save_result(self.cfg, {"test": "t1", "picks": [{"id": "a", "picked": "C"}]})

    def test_page_escapes_messages(self) -> None:
        self.test["items"][0]["real"] = "<script>x</script>"
        page = self.vt.page(self.cfg, self.test)
        self.assertNotIn("x</script>", page)  # cannot close the page's script early
        self.assertIn("x<\/script>", page)


class WebExportTest(unittest.TestCase):
    def test_zip_exports_are_read_in_place(self) -> None:
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chatgpt-export.zip"
            convs = [{"id": "c1", "mapping": {
                "u": {"message": {"author": {"role": "user"}, "create_time": 1767225600,
                                  "content": {"content_type": "text", "parts": ["write me a short bio"]}}},
                "a": {"message": {"author": {"role": "assistant"},
                                  "content": {"content_type": "text", "parts": ["Here is a bio you can use."]}}}}}]
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("conversations.json", json.dumps(convs))
                z.writestr("user.json", json.dumps({"email": "x"}))
            sessions, kind = web.parse_file(path)
            self.assertEqual(kind, "chatgpt")
            self.assertEqual([e.user for e in sessions[0].exchanges], ["write me a short bio"])
            self.assertEqual(list(web.assistant_texts(path)), ["Here is a bio you can use."])

    def test_web_replies_pasted_into_agents_are_stripped(self) -> None:
        reply = ("You are a careful assistant. Read every file in the project, list the defects you find, "
                 "and propose one fix for each defect before you change anything at all.")
        typed = "can u do this please " + reply + " and tell me when done"
        collected = corpus.Collected()
        rec = {"id": corpus.message_id(typed), "source": "codex", "session": "s", "cwd": None, "at": "2026-10-01T10:00:00+00:00",
               "text": typed, "chars": len(typed), "tagged_paste_chars": 0, "non_latin": 0.0, "prev": "", "repeats": 1, "sessions": ["s"]}
        collected.records[rec["id"]] = rec
        self.assertEqual(corpus.strip_web_replies(collected, [reply]), 1)
        (kept,) = collected.records.values()
        self.assertTrue(kept["text"].startswith("can u do this please [pasted: "))
        self.assertTrue(kept["text"].endswith("and tell me when done"))
        self.assertTrue(kept["web_paste"])
