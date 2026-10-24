import os
import tempfile
import threading
import unittest
from pathlib import Path

from trigger_agent.config import Config
from trigger_agent.tools import Workspace


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.workspace = Workspace(Config(self.root))

    def tearDown(self):
        self.directory.cleanup()

    def test_actual_write_edit_read_and_backup(self):
        self.workspace.execute("write_file", {"path": "draft/article.md", "content": 'First paragraph\nSecond paragraph\n'})
        result = self.workspace.execute("edit_file", {"path": "draft/article.md", "old_text": 'Second paragraph', "new_text": 'New paragraph'})
        self.assertEqual((self.root / "draft/article.md").read_text(), 'First paragraph\nNew paragraph\n')
        self.assertEqual(Path(result["backup"]).read_text(), 'First paragraph\nSecond paragraph\n')
        result = self.workspace.execute("read_file", {"path": "draft/article.md", "start_line": 2})
        self.assertIn('2: New paragraph', result["content"])

    def test_ambiguous_edit_does_not_change_file(self):
        (self.root / "a.txt").write_text("same same")
        with self.assertRaises(ValueError):
            self.workspace.execute("edit_file", {"path": "a.txt", "old_text": "same", "new_text": "new"})
        self.assertEqual((self.root / "a.txt").read_text(), "same same")

    def test_path_escape_and_symlink_escape_blocked(self):
        with self.assertRaises(ValueError):
            self.workspace.execute("write_file", {"path": "../escape.txt", "content": "no"})
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.workspace.execute("write_file", {"path": "escape/escape.txt", "content": "no"})

    def test_credentials_are_not_exposed(self):
        (self.root / ".env").write_text("SECRET=private")
        (self.root / "visible.txt").write_text("hello")
        with self.assertRaises(ValueError):
            self.workspace.execute("read_file", {"path": ".env"})
        (self.root / "alias.txt").symlink_to(self.root / ".env")
        files = self.workspace.execute("list_files", {"path": "."})["files"]
        self.assertEqual(files, ["visible.txt"])

    def test_synced_sources_read_only(self):
        (self.root / "AGENTS.md").write_text("Treat every file under `sources/` as read-only reference material.\nDo not edit synced project files.")
        (self.root / "sources").mkdir()
        (self.root / "sources/a.txt").write_text("reference")
        workspace = Workspace(Config(self.root))
        self.assertIn("reference", workspace.execute("read_file", {"path": "sources/a.txt"})["content"])
        with self.assertRaises(ValueError):
            workspace.execute("write_file", {"path": "sources/a.txt", "content": "bad"})

    def test_search_returns_real_line_numbers(self):
        (self.root / "a.txt").write_text("first\nneedle here\nlast")
        result = self.workspace.execute("search_files", {"query": "needle"})
        self.assertEqual(result["matches"][0]["line"], 2)

    def test_shell_opt_in_and_execution(self):
        with self.assertRaises(ValueError):
            self.workspace.execute("run_command", {"command": "pwd"})
        workspace = Workspace(Config(self.root, allow_shell=True))
        result = workspace.execute("run_command", {"command": "python3 -c 'print(2 + 3)'"})
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["output"].strip(), "5")

    def test_shell_timeout_and_secret_redaction(self):
        workspace = Workspace(Config(self.root, allow_shell=True, api_key="private-test-value"))
        result = workspace.execute("run_command", {"command": "python3 -c 'import time; time.sleep(5)'", "timeout": 1})
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)
        result = workspace.execute("run_command", {"command": "printf private-test-value"})
        self.assertNotIn("private-test-value", result["output"])

    def test_stopped_task_cannot_write(self):
        stopped = threading.Event()
        stopped.set()
        workspace = Workspace(Config(self.root), stopped=stopped)
        with self.assertRaises(ValueError):
            workspace.execute("write_file", {"path": "late.txt", "content": "no"})
        self.assertFalse((self.root / "late.txt").exists())


if __name__ == "__main__":
    unittest.main()
