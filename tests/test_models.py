import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from brain import config as config_mod
from brain import llm
from brain.dashboard import run_active, run_choice
from brain.run import usage_since

EVENTS = "\n".join(json.dumps(e) for e in [
    {"type": "thread.started", "thread_id": "t"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
    {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 30,
                                         "reasoning_output_tokens": 10}},
    {"type": "turn.completed", "usage": {"input_tokens": 800, "cached_input_tokens": 0, "output_tokens": 20}},
])


class CodexUsageTest(unittest.TestCase):
    def test_sums_every_turn(self) -> None:
        usage, error = llm.parse_codex_events(EVENTS + "\nnot json\n")
        self.assertEqual(usage, {"calls": 0, "input_tokens": 2000, "cached_input_tokens": 200, "output_tokens": 50})
        self.assertEqual(error, "")

    def test_keeps_the_error(self) -> None:
        _, error = llm.parse_codex_events(json.dumps({"type": "turn.failed", "error": {"message": "quota"}}))
        self.assertIn("quota", error)

    def test_ask_counts_tokens_and_reads_the_answer(self) -> None:
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"], seen["input"] = command, kwargs["input"]
            answer = Path(command[command.index("-o") + 1])
            answer.write_text(json.dumps({"headline": "ok"}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=EVENTS, stderr="")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(llm.subprocess, "run", fake_run):
            model = llm.CodexModel(Path(tmp), name="gpt-test", effort="low", codex="codex")
            result = model.ask("Summarize.", {"week.md": "hello"}, {"type": "object"})
            self.assertEqual(list(Path(tmp).iterdir()), [])  # the call folder is cleaned up
        self.assertEqual(result, {"headline": "ok"})
        self.assertEqual(model.usage["input_tokens"], 2000)
        self.assertEqual(model.usage["output_tokens"], 50)
        self.assertEqual(model.usage["calls"], 1)
        self.assertIn("model_reasoning_effort=low", seen["command"])
        self.assertIn("--ephemeral", seen["command"])
        self.assertEqual(seen["command"][-1], "-")
        self.assertIn("===== week.md =====\nhello", seen["input"])


class RunUsageTest(unittest.TestCase):
    def test_week_share_of_running_totals(self) -> None:
        before = {"calls": 3, "input_tokens": 100, "output_tokens": 10}
        now = {"calls": 5, "input_tokens": 350, "output_tokens": 30}
        self.assertEqual(usage_since(before, now), {"calls": 2, "input_tokens": 250, "output_tokens": 20})

    def test_manual_flag_survives(self) -> None:
        self.assertEqual(usage_since({"calls": 1, "measured": False}, {"calls": 4, "measured": False}),
                         {"calls": 3, "measured": False})


class ModelChoiceTest(unittest.TestCase):
    def test_config_model_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "brain").mkdir()
            (Path(tmp) / "brain" / "config.toml").write_text(
                '[model]\ncli = "codex"\nname = "gpt-6-sol"\neffort = "medium"\n', encoding="utf-8")
            cfg = config_mod.load(Path(tmp))
            self.assertEqual((cfg.model_cli, cfg.model_name, cfg.model_effort), ("codex", "gpt-6-sol", "medium"))
            (Path(tmp) / "brain" / "config.toml").write_text('owner = "X"\n', encoding="utf-8")
            cfg = config_mod.load(Path(tmp))
            self.assertEqual((cfg.model_cli, cfg.model_name, cfg.model_effort), ("agy", None, None))

    def test_run_now_choice_is_checked(self) -> None:
        self.assertEqual(run_choice({"cli": "codex", "model": "gpt-6-sol", "effort": "high"}),
                         {"cli": "codex", "model_name": "gpt-6-sol", "effort": "high"})
        self.assertEqual(run_choice({}), {"cli": None, "model_name": None, "effort": None})
        for bad in ({"cli": "bash"}, {"model": "manual-me"}, {"model": "x; rm -rf"}, {"effort": "HIGH!"}):
            with self.assertRaises(ValueError):
                run_choice(bad)

    def test_edits_wait_for_a_running_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_mod.Config(vault=Path(tmp), projects_root=Path(tmp), sources={})
            self.assertFalse(run_active(cfg))
            cfg.work_dir.mkdir()
            lock = cfg.work_dir / "run.lock"
            import os
            lock.write_text(str(os.getpid()), encoding="utf-8")  # held by a live process
            self.assertTrue(run_active(cfg))
            lock.write_text("999999", encoding="utf-8")  # its process was killed: nothing holds it
            self.assertFalse(run_active(cfg))
            lock.write_text(str(os.getpid()), encoding="utf-8")
            old = time.time() - 25 * 3600
            os.utime(lock, (old, old))
            self.assertFalse(run_active(cfg))


if __name__ == "__main__":
    unittest.main()
