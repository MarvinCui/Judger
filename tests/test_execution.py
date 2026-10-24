import json
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from trigger_agent.execution import DIMENSIONS, Execution
from trigger_agent.state import State


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = State()
        (self.root / "index.html").write_text('<html><head></head><body><h1>Test</h1><div data-trigger-card>Content</div></body></html>')
        self.execution = Execution(self.root, self.state)
        self.execution.offer("index.html")

    def tearDown(self):
        self.state.stop()
        self.execution.close()
        self.temp.cleanup()

    def test_navigation_adjusts_only_selected_dimension_and_persists_real_css(self):
        self.execution.enabled = True
        self.execution.key("=")
        self.execution.key("】")
        self.execution.key("-")
        self.assertEqual(self.execution.values["color"], .6)
        self.assertEqual(self.execution.values["bold"], .4)
        self.assertEqual(self.execution.values["density"], .5)
        self.assertIn("saturate(1.20)", (self.root / "trigger-style.css").read_text())
        self.assertIn('data-trigger-styles', (self.root / "index.html").read_text())
        self.assertEqual(json.loads(self.execution.meta.read_text())["bold"], .4)

    def test_controls_are_bounded_and_ascii_brackets_are_aliases(self):
        self.execution.enabled = True
        for _ in range(30):
            self.execution.key("-")
        self.assertEqual(self.execution.values["color"], 0)
        self.execution.key("[")
        self.assertEqual(self.execution.selected, len(DIMENSIONS)-1)
        self.execution.key("]")
        self.assertEqual(self.execution.selected, 0)

    def test_editing_a_design_answer_reverts_all_later_adjustments(self):
        self.execution.enabled = True
        self.execution.key("=")
        self.execution.key("]")
        self.execution.key("=")
        self.assertEqual(len(self.state.records), 2)
        self.state.revise_record(1, "-")
        snapshot = self.execution.snapshot()
        self.assertEqual(snapshot["values"]["color"], .4)
        self.assertEqual(snapshot["values"]["bold"], .5)
        self.assertIn("saturate(0.80)", (self.root / "trigger-style.css").read_text())

    def test_undo_restores_previous_design_values(self):
        self.execution.enabled = True
        self.execution.key("=")
        self.execution.undo()
        self.assertEqual(self.execution.values["color"], .5)
        self.assertEqual(self.state.records[0]["status"], "withdrawn")

    def test_burst_of_adjustments_sends_only_latest_settings_after_pause(self):
        self.execution.enabled = True
        self.execution.key("=")
        self.execution.key("=")
        self.execution.key("-")
        self.assertFalse(self.state.inbox)
        deadline = time.monotonic() + 2
        while not self.state.inbox and time.monotonic() < deadline:
            time.sleep(.02)
        items = self.state.drain()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "design")
        self.assertEqual(items[0]["design"]["values"]["color"], .6)
        self.execution.flush()
        self.assertFalse(self.state.inbox)

    def test_no_webpage_gives_a_plain_notice(self):
        (self.root / "index.html").unlink()
        self.execution.entry = ""
        self.assertEqual(self.execution.open(browser=False), "")
        self.assertIn('No webpage', self.state.notice)

    def test_main_question_keeps_priority_over_design_controls(self):
        self.execution.enabled = True
        self.state.mode = "question"
        self.state.question = {"id": "permission", "question": 'Allow this step?'}
        self.assertFalse(self.execution.key("="))
        self.state.trigger("=")
        self.assertEqual(self.state.answer["answer"], "accept")
        self.assertEqual(self.execution.values["color"], .5)
        self.assertFalse(self.execution.snapshot()["enabled"])

    def test_macos_preview_opens_in_background_so_terminal_keeps_focus(self):
        self.execution.url = "http://127.0.0.1:1234/project/index.html"
        with patch("trigger_agent.execution.sys.platform", "darwin"), patch(
                "trigger_agent.execution.subprocess.run", return_value=Mock(returncode=0)) as run:
            self.execution._open_browser()
        self.assertEqual(run.call_args.args[0][:2], ["open", "-g"])

    def test_preview_rejects_credentials_and_escaping_paths(self):
        for path in ("../elsewhere/index.html", ".env", ".local/key.html", ".trigger-agent/codex.json"):
            with self.assertRaises(ValueError):
                self.execution.safe_file(path)

    def test_http_preview_and_control_endpoints(self):
        self.execution.open(browser=False)
        parts = urlsplit(self.execution.url)
        base = parts.scheme + '://' + parts.netloc
        header = {"X-Trigger-Token": self.execution.token}
        with urllib.request.urlopen(base + "/project/index.html") as response:
            page = response.read()
            self.assertIn(b"data-trigger-preview", page)
            self.assertNotIn(b"iframe", page)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/_trigger/version")
        self.assertEqual(caught.exception.code, 403)
        request = urllib.request.Request(base + "/_trigger/version", headers=header)
        with urllib.request.urlopen(request) as response:
            self.assertEqual(set(json.load(response)), {"stamp", "css", "entry"})
        request = urllib.request.Request(base + "/api/action", data=json.dumps({"action":"key","key":"="}).encode(), headers={**header,"Content-Type":"application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(self.execution.values["color"], .5)
        for route in ("/project/../.local/key.html", "/project/%2E%2E/outside.html", "/project/.trigger-agent/design.json"):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + route)
        request = urllib.request.Request(base + "/_trigger/version", headers={**header, "Origin":"https://unrelated.example"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
