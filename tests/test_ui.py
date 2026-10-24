import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from trigger_agent.config import Config
from trigger_agent.state import Node, State
from trigger_agent.engine import demo_tree
from trigger_agent.ui import Terminal
from trigger_agent.execution import Execution


class Screen:
    def __init__(self):
        self.lines = {}

    def getmaxyx(self):
        return 34, 112

    def erase(self):
        self.lines.clear()

    def addstr(self, row, column, text, attr):
        self.lines[row] = text

    def refresh(self):
        pass


class UITests(unittest.TestCase):
    def render(self, state):
        screen = Screen()
        terminal = Terminal(Config(Path("/tmp")), state)
        with patch("trigger_agent.ui.curses.has_colors", return_value=False):
            terminal.draw(screen)
        return "\n".join(screen.lines.values())

    def assert_plain(self, text):
        for old in ('focus', 'degree of elaboration', 'adopt', 'exclude', 'unpack', 'cached', 'Model requests', "token"):
            self.assertNotIn(old, text)
        self.assertNotIn("+ ", text)

    def test_loading_has_no_fake_options_or_debug_stats(self):
        state = State()
        text = self.render(state)
        self.assertIn('One moment. Next question coming up.', text)
        self.assertNotIn('= Yes', text)
        self.assert_plain(text)
        self.assertIn('SIDE QUESTIONS / Work continues', text)

    def test_main_question_hides_long_internal_explanations(self):
        state = State()
        state.mode = "question"
        state.question = {"id": "q", "question": 'Is it for other people?',
                          "on_accept": 'expand more fully along the current direction', "on_reject": 'contract the scope'}
        text = self.render(state)
        self.assertIn('Is it for other people?', text)
        self.assertIn('= Yes', text)
        self.assertIn('0 Rephrase', text)
        self.assertNotIn(state.question["on_accept"], text)
        self.assertNotIn(state.question["on_reject"], text)
        self.assert_plain(text)
        self.assertIn('MAIN QUESTION', text)

    def test_cached_question_uses_yes_no_and_send(self):
        state = State()
        state.nodes = {"": Node('Will you post it online?', 'Article', 'expand more fully along the current direction', 'contract the scope')}
        state.pending = [{"key": "="}]
        text = self.render(state)
        self.assertIn('Will you post it online?', text)
        self.assertIn('= Yes', text)
        self.assertIn('0 Send now', text)
        self.assert_plain(text)

    def test_equals_in_text_editor_is_literal(self):
        state = State()
        terminal = Terminal(Config(Path("/tmp")), state)
        terminal.editing = True
        for character in "x = y - 0":
            terminal.edit_key(character)
        terminal.edit_key("\n")
        self.assertEqual(state.drain()[0]["text"], "x = y - 0")
        self.assertEqual(state.pending, [])

    def test_back_and_backspace_restore_the_previous_side_question(self):
        for key in ("b", "\x7f", "\b"):
            with self.subTest(key=key):
                state = State(lookahead=4)
                snapshot = state.planning_snapshot()
                state.install_plan(snapshot, demo_tree(snapshot))
                question = state.snapshot().node.question
                state.trigger("=")
                terminal = Terminal(Config(Path("/tmp")), state)
                terminal.handle_key(key)
                self.assertEqual(state.snapshot().node.question, question)
                self.assertEqual(state.pending, [])

    def test_backspace_edits_text_instead_of_rewinding(self):
        state = State()
        terminal = Terminal(Config(Path("/tmp")), state)
        terminal.editing = True
        terminal.buffer = "abc"
        terminal.handle_key("\x7f")
        self.assertEqual(terminal.buffer, "ab")
        self.assertEqual(state.seq, 0)

    def test_history_buttons_change_an_arbitrarily_selected_question(self):
        state = State(lookahead=4)
        snapshot = state.planning_snapshot()
        state.install_plan(snapshot, demo_tree(snapshot))
        for key in "=-=":
            state.trigger(key)
        terminal = Terminal(Config(Path("/tmp")), state)
        terminal.handle_key("v")
        terminal.history_index = 0
        terminal.handle_key("e")
        terminal.handle_key("-")
        self.assertFalse(terminal.history_open)
        self.assertEqual(state.records[0]["key"], "-")
        self.assertEqual([r["status"] for r in state.records[1:]], ["withdrawn", "withdrawn"])

    def test_history_retract_button_reopens_the_selected_question(self):
        state = State(lookahead=4)
        snapshot = state.planning_snapshot()
        state.install_plan(snapshot, demo_tree(snapshot))
        state.trigger("=")
        terminal = Terminal(Config(Path("/tmp")), state)
        terminal.handle_key("v")
        terminal.handle_key("r")
        self.assertEqual(state.records[0]["status"], "withdrawn")
        self.assertEqual(terminal.edit_record, 1)
        terminal.handle_key("=")
        self.assertEqual(state.records[0]["key"], "=")
        self.assertEqual(state.records[0]["status"], "sent")

    def test_history_overlay_keeps_the_question_short_and_shows_actions(self):
        state = State()
        terminal = Terminal(Config(Path("/tmp")), state)
        terminal.history_open = True
        screen = Screen()
        with patch("trigger_agent.ui.curses.has_colors", return_value=False):
            terminal.draw(screen)
        text = "\n".join(screen.lines.values())
        self.assertIn('Question history', text)
        self.assertIn('e Edit', text)
        self.assertIn('r Withdraw', text)

    def test_done_screen_shows_where_to_find_the_project(self):
        state = State()
        state.mode = "done"
        text = self.render(state)
        self.assertIn('Files saved in: /tmp', text)
        self.assertIn('SIDE QUESTIONS / This round is done', text)

    def test_concrete_mode_keeps_all_controls_in_terminal_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text('<html><head></head><body>Project</body></html>')
            state = State()
            execution = Execution(root, state)
            execution.offer("index.html")
            execution.enabled = True
            terminal = Terminal(Config(root), state)
            screen = Screen()
            calls = []
            screen.addstr = lambda row, col, text, attr: calls.append((row, col, text))
            try:
                terminal.handle_key("]")
                terminal.handle_key("=")
                with patch("trigger_agent.ui.curses.has_colors", return_value=False):
                    terminal.draw(screen)
                text = "\n".join(item[2] for item in calls)
                self.assertIn('Boldness', text)
                self.assertIn("60%", text)
                self.assertIn('All controls stay in Terminal', text)
                self.assertTrue(any(col == 2 and 'Color' in value for _, col, value in calls))
                self.assertTrue(any(col > 25 and 'Main' in value for _, col, value in calls))
                terminal.handle_key("b")
                self.assertEqual(execution.values["bold"], .5)
            finally:
                state.stop()
                execution.close()


if __name__ == "__main__":
    unittest.main()
