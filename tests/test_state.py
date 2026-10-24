import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from trigger_agent.config import Journal
from trigger_agent.engine import demo_tree, parse_plan
from trigger_agent.state import Focus, State


class StateTests(unittest.TestCase):
    def cached(self):
        state = State(lookahead=4)
        state.task = 'Write an article'
        snapshot = state.planning_snapshot()
        state.install_plan(snapshot, demo_tree(snapshot))
        return state

    def test_four_levels_immediate_without_network(self):
        state = self.cached()
        questions = []
        for key in "=-==":
            questions.append(state.snapshot().node.question)
            state.trigger(key)
        self.assertEqual(len(set(questions)), 4)
        self.assertEqual(len(state.pending), 4)
        self.assertEqual(state.cursor, "=-==")
        self.assertEqual(state.drain(), [])
        state.trigger("0")
        batch = state.drain()
        self.assertEqual([e["key"] for e in batch[0]["events"]], list("=-=="))

    def test_feedback_keeps_order_and_original_focus(self):
        state = self.cached()
        original = state.focus
        state.trigger("=")
        state.set_focus(Focus('Another object', 'Another dimension', 'More detail', 'Less detail'))
        state.trigger("=")
        state.trigger("-")
        state.trigger("0")
        events = state.drain()[0]["events"]
        self.assertEqual([e["key"] for e in events], ["=", "=", "-"])
        self.assertTrue(all(e["focus"]["target"] == original.target for e in events))
        self.assertEqual(state.side_focus.target, original.target)

    def test_question_zero_is_unpack_and_not_submit(self):
        state = self.cached()
        state.trigger("=")
        result = []
        worker = threading.Thread(target=lambda: result.append(state.ask(
            {"question": 'Make it shorter?', "on_accept": 'Shorter', "on_reject": 'Keep it open'})))
        worker.start()
        deadline = time.monotonic() + 1
        while state.mode != "question" and time.monotonic() < deadline:
            time.sleep(0.001)
        state.trigger("0")
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["answer"], "unpack")
        self.assertEqual(len(state.pending), 1)
        self.assertEqual(state.drain(), [])

    def test_question_reject_does_not_infer_opposite(self):
        state = State(lookahead=0)
        state.mode = "question"
        state.question = {"id": "a", "question": 'Use plan A?'}
        state.trigger("-")
        state.trigger("=")  # queued repeat must not turn into running feedback
        self.assertEqual(state.answer["answer"], "reject")
        self.assertEqual(state.pending, [])

    def test_refill_preserves_existing_questions(self):
        state = self.cached()
        state.trigger("=")
        state.trigger("-")
        old = state.nodes[state.cursor]
        snapshot = state.planning_snapshot()
        state.install_plan(snapshot, demo_tree(snapshot))
        self.assertEqual(state.nodes[state.cursor], old)
        for key in "=-==":
            self.assertIsNotNone(state.snapshot().node)
            state.trigger(key)

    def test_stale_plan_discarded_after_focus_change(self):
        state = State()
        snapshot = state.planning_snapshot()
        state.set_focus(Focus('New object', 'Length', 'Longer', 'Shorter'))
        self.assertFalse(state.install_plan(snapshot, demo_tree(snapshot)))
        self.assertEqual(state.nodes, {})

    def test_plan_cannot_rewrite_visible_node(self):
        state = self.cached()
        state.trigger("=")
        state.trigger("=")
        snapshot = state.planning_snapshot()
        nodes = {path: vars(node) for path, node in demo_tree(snapshot).items()}
        nodes[state.cursor]["question"] = 'Ask a different question?'
        with self.assertRaises(ValueError):
            parse_plan(json.dumps({"nodes": nodes}), snapshot)

    def test_no_question_means_no_invented_answer(self):
        state = State()
        snapshot = state.planning_snapshot()
        state.trigger("-")
        self.assertEqual(state.pending, [])
        self.assertEqual(state.notice, 'Wait for the question before answering.')
        self.assertTrue(state.install_plan(snapshot, demo_tree(snapshot)))

    def test_equals_is_the_positive_key_and_plus_is_ignored(self):
        state = self.cached()
        state.trigger("+")
        self.assertEqual(state.pending, [])
        state.trigger("=")
        self.assertEqual(state.pending[0]["key"], "=")

    def test_committed_answers_are_available_to_future_questions(self):
        state = self.cached()
        question = state.snapshot().node.question
        state.trigger("=")
        state.trigger("0")
        state.plan_requested = True
        snapshot = state.planning_snapshot()
        self.assertEqual(snapshot["answered"][0]["question"], question)
        self.assertEqual(snapshot["answered"][0]["answer"], "accept")

    def test_free_text_can_contain_trigger_characters(self):
        state = State(lookahead=0)
        state.submit_text('Implement x + y - 0')
        self.assertEqual(state.drain()[0]["text"], 'Implement x + y - 0')
        self.assertEqual(state.pending, [])

    def test_logs_redact_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            journal = Journal(path, "private-test-value")
            journal.write({"text": "private-test-value sk-abcdefghijklmnop123456"})
            text = path.read_text()
            self.assertNotIn("private-test-value", text)
            self.assertNotIn("sk-abcdefghijklmnop123456", text)


if __name__ == "__main__":
    unittest.main()
