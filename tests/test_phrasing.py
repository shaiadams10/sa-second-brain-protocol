import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from brain import phrasing
from brain.config import Config
from brain.model import ExchangeBuilder


def jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records), encoding="utf-8")
    return path


def rollout(root: Path, name: str, day: str, turns: list[tuple[str, str]]) -> None:
    """A Codex session on 2026-09-<day> with (owner, assistant) turns."""
    records = [{"timestamp": f"2026-09-{day}T10:00:00Z", "type": "session_meta",
                "payload": {"id": name, "cwd": str(root / "P" / "App"), "source": "vscode"}}]
    for i, (user, reply) in enumerate(turns):
        records.append({"timestamp": f"2026-09-{day}T10:{i:02d}:01Z", "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": user}]}})
        records.append({"timestamp": f"2026-09-{day}T10:{i:02d}:30Z", "type": "response_item",
                        "payload": {"type": "message", "role": "assistant",
                                    "content": [{"type": "output_text", "text": reply}]}})
    jsonl(root / "sessions" / f"rollout-{name}.jsonl", records)


class FakeModel:
    cli, name, effort = "fake", "fake-1", None

    def __init__(self, answer: dict) -> None:
        self.answer, self.files, self.usage = answer, None, {}

    def ask(self, task, files, schema, **_):
        self.files = files
        return self.answer


def answer(cards=(), used=(), rounds=0) -> dict:
    return {"cards": list(cards), "terms_used": list(used), "rounds_lost": rounds, "patterns": []}


HEIGHT = {"id": "p1", "kind": "term", "said": "make the header a little wider",
          "better": "Give the header a little more height.", "why": "Height is top to bottom.",
          "agent_put_it": "I'll give the header more height", "terms": [{"term": "height", "meaning": "size from top to bottom", "area": "ui"}]}


class ExchangeTest(unittest.TestCase):
    def test_opening_is_the_first_reply_and_reply_the_last(self) -> None:
        b = ExchangeBuilder()
        b.user(datetime(2026, 9, 14, tzinfo=timezone.utc), "fix the header")
        b.assistant("I'll give the header more height.")
        b.assistant("Done: the header is 64px tall now.")
        ex = b.finish()[0]
        self.assertEqual(ex.opening, "I'll give the header more height.")
        self.assertEqual(ex.reply, "Done: the header is 64px tall now.")


class PhrasingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "P" / "App").mkdir(parents=True)
        (self.root / "P" / "App" / "package.json").write_text("{}", encoding="utf-8")
        self.cfg = Config(vault=self.root / "vault", projects_root=self.root / "P", sources={"codex": self.root / "sessions"})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, week: str, model: FakeModel) -> dict | None:
        with mock.patch("brain.phrasing.make_model", return_value=model):
            return phrasing.build(self.cfg, week, log=lambda _l: None)

    def test_week_builds_cards_and_later_weeks_check_terms(self) -> None:
        rollout(self.root, "a", "15", [
            ("can you make the header a little wider so the title fits, it looks squished", "I'll give the header more height so the title fits."),
            ("perfect thanks", "Glad it works."),
        ])
        model = FakeModel(answer([HEIGHT, {**HEIGHT, "id": "p99"}], rounds=1))
        record = self.build("2026-W38", model)
        self.assertIn("I'll give the header more height", model.files["prompts.md"])
        self.assertIn("Owner's next message:\nperfect thanks", model.files["prompts.md"])
        self.assertEqual(len(record["cards"]), 1)  # the card for an unknown id is dropped
        card = record["cards"][0]
        self.assertTrue(card["id"].startswith("2026-W38-"))
        self.assertEqual((card["source"], card["project"], record["stats"]["rounds_lost"]), ("codex", "App", 1))
        self.assertFalse(record["partial"])
        self.assertIn("make the header a little wider", phrasing.full_prompts(self.cfg, "2026-W38")[card["prompt"]])
        self.assertIn("Give the header a little more height.", (self.cfg.vault / phrasing.HISTORY).read_text(encoding="utf-8"))

        rollout(self.root, "b", "22", [("the sidebar needs more height on small screens please", "I'll raise its height.")])
        later = FakeModel(answer(used=[{"term": "Height", "id": "p1", "quote": "the sidebar needs more height"}]))
        self.build("2026-W39", later)
        self.assertIn("- height (size from top to bottom)", later.files["terms-to-check.md"])
        self.assertIn("make the header a little wider", later.files["earlier.md"])
        [term] = phrasing.terms(self.cfg)
        self.assertEqual((term["week"], term["used"]["week"]), ("2026-W38", "2026-W39"))

    def test_short_messages_are_not_candidates(self) -> None:
        rollout(self.root, "a", "15", [("ok go", "Starting."), ("yes", "Done.")])
        self.assertIsNone(self.build("2026-W38", FakeModel(answer())))
        items, stats, typed = phrasing.collect(self.cfg, "2026-W38")
        self.assertEqual((items, stats["prompts"], typed), ([], 2, ["ok go", "yes"]))

    def test_feedback_is_saved_cleared_and_checked(self) -> None:
        rollout(self.root, "a", "15", [("can you make the header a little wider so the title fits", "I'll add height.")])
        card = self.build("2026-W38", FakeModel(answer([HEIGHT])))["cards"][0]["id"]
        phrasing.save_feedback(self.cfg, {"card": card, "verdict": "knew", "note": "I know this one"})
        self.assertEqual(phrasing.load_feedback(self.cfg)[card]["verdict"], "knew")
        phrasing.save_feedback(self.cfg, {"card": card, "verdict": ""})
        fb = phrasing.load_feedback(self.cfg)[card]
        self.assertEqual((fb["verdict"], fb["note"]), ("", "I know this one"))
        with self.assertRaises(KeyError):
            phrasing.save_feedback(self.cfg, {"card": "nope", "verdict": "got"})
        with self.assertRaises(ValueError):
            phrasing.save_feedback(self.cfg, {"card": card, "verdict": "maybe"})
        summary = phrasing.summary(self.cfg)
        self.assertEqual((summary["unread"], summary["open"], summary["open_weeks"], summary["cards"]), (1, 1, 1, 1))
        phrasing.save_feedback(self.cfg, {"card": card, "verdict": "got"})
        summary = phrasing.summary(self.cfg)
        self.assertEqual((summary["open"], summary["weeks"]["2026-W38"]["open"]), (0, 0))

    def test_weeks_to_build_catches_up_and_rebuilds_partial_weeks(self) -> None:
        self.assertEqual(phrasing.weeks_to_build(self.cfg, date(2026, 10, 14)), ["2026-W41"])
        phrasing._write_jsonl(self.cfg.vault / phrasing.RESULTS, [
            {"week": "2026-W39", "cards": []}, {"week": "2026-W41", "cards": [], "partial": True}])
        self.assertEqual(phrasing.weeks_to_build(self.cfg, date(2026, 10, 14)), ["2026-W40", "2026-W41"])

    def test_corrected_prompts_come_first_within_the_budget(self) -> None:
        def item(n, words, corrected=False):
            return {"mid": str(n), "at": f"2026-09-15T10:0{n}", "source": "codex", "project": "App", "text": "w " * words,
                    "words": words, "opening": "", "reply": "", "next": "", "corrected": corrected}
        items = [item(1, 10), item(2, 200), item(3, 12, corrected=True)]
        with mock.patch.object(phrasing, "BUDGET", 700):
            _, ids = phrasing._render_prompts(items, "2026-W38")
        self.assertEqual([c["mid"] for c in ids.values()], ["1", "3"])

    def test_old_cards_return_until_their_term_is_used(self) -> None:
        old = {"id": "c1", "kind": "term", "said": "x", "better": "y", "terms": [{"term": "height"}]}
        records = [{"week": "2026-W36", "cards": [old]}, {"week": "2026-W40"}]
        self.assertEqual(phrasing._returning(records, "2026-W40", {}, set()), ["c1"])
        self.assertEqual(phrasing._returning(records, "2026-W40", {}, {"height"}), [])
        self.assertEqual(phrasing._returning(records, "2026-W40", {"c1": {"verdict": "knew"}}, set()), [])
        self.assertEqual(phrasing._returning([{"week": "2026-W39", "cards": [old]}, {"week": "2026-W40"}], "2026-W40", {}, set()), [])


if __name__ == "__main__":
    unittest.main()
