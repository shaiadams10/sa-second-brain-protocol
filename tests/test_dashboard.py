import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from brain.config import Config
from brain.dashboard import App


class SkillDashboardTest(unittest.TestCase):
    def test_state_exposes_skill_progress_without_changing_observations(self):
        evidence = [{"week": "2026-W30", "project": "Example", "level": "learning", "quote": "How does this work?"},
                    {"week": "2026-W31", "project": "Example", "level": "directs", "quote": "Use this option."}]
        skill = {"kind": "skill", "text": "Example topic", "status": "candidate", "evidence": evidence}
        fact = {"kind": "stated", "text": "A stated fact", "status": "active", "evidence": evidence[:1]}
        removed = {**skill, "status": "removed"}
        knowledge = SimpleNamespace(weeks={}, projects={}, items={"skill": skill, "fact": fact, "removed": removed})
        with tempfile.TemporaryDirectory() as folder:
            cfg = Config(vault=Path(folder), projects_root=Path(folder), sources={})
            app = App(cfg)
            with mock.patch("brain.dashboard.knowledge_for", return_value=knowledge), \
                 mock.patch("brain.dashboard.read_run_log", return_value=[]), \
                 mock.patch.object(app, "catalog", return_value=SimpleNamespace(projects={}, groups=[])), \
                 mock.patch.object(app, "schedule", return_value=None):
                items = {item["id"]: item for item in app.state()["items"]}
        self.assertEqual(items["skill"]["standing"]["level"], "growing")
        self.assertIn("2026-W30", items["skill"]["standing"]["trend"])
        self.assertEqual(items["skill"]["evidence"], evidence)
        self.assertNotIn("standing", items["fact"])
        self.assertEqual(items["removed"]["status"], "removed")
        self.assertEqual(items["removed"]["standing"], items["skill"]["standing"])
