import json
import tempfile
import threading
import unittest
from pathlib import Path

from trigger_agent.config import Config
from trigger_agent.engine import Agent, demo_tree, parse_main_plan
from trigger_agent.projects import create_project
from trigger_agent.state import MainNode, State


class WorkflowTests(unittest.TestCase):
    def cached(self, auto_submit=0):
        state = State(lookahead=4, auto_submit=auto_submit)
        state.task = 'Write an article'
        snapshot = state.planning_snapshot()
        state.install_plan(snapshot, demo_tree(snapshot))
        return state

    def start_ask(self, state, question='Is it for other people?'):
        results = []
        worker = threading.Thread(target=lambda: results.append(state.ask(
            {"question": question, "on_accept": 'Yes', "on_reject": 'No'})), daemon=True)
        self.addCleanup(state.stop)
        worker.start()
        return worker, results

    def wait_question(self, state, path=None, text=None):
        with state.cv:
            self.assertTrue(state.cv.wait_for(lambda: state.question is not None
                and (path is None or state.question.get("main_path") == path)
                and (text is None or state.question["question"] == text), timeout=2))
            return state.question.copy()

    def install_main(self, state, early=False):
        snapshot = state.main_planning_snapshot()
        self.assertEqual(len(snapshot["paths"]), 127)
        nodes = {path: MainNode('Is it for you?', "ask", "ask") for path in snapshot["paths"]}
        nodes[""] = MainNode(snapshot["root"]["question"], "work" if early else "ask", "ask")
        state.install_main_plan(snapshot, nodes)
        return snapshot, nodes

    def test_main_seven_cached_answers_without_a_model_roundtrip(self):
        state = State(main_lookahead=7)
        worker, results = self.start_ask(state)
        self.install_main(state)
        for depth in range(7):
            self.wait_question(state, "=" * depth)
            state.trigger("=")
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["answer"], "batch")
        self.assertEqual(len(results[0]["answers"]), 7)
        self.assertEqual(state.requests, 0)

    def test_main_returns_to_work_as_soon_as_the_answer_is_enough(self):
        state = State(main_lookahead=7)
        worker, results = self.start_ask(state)
        self.install_main(state, early=True)
        self.wait_question(state, "")
        state.trigger("=")
        worker.join(2)
        self.assertEqual(results[0]["answer"], "accept")
        self.assertEqual(len(state.records), 1)

    def test_main_back_replaces_a_cached_answer(self):
        state = State(main_lookahead=7)
        worker, _ = self.start_ask(state)
        self.install_main(state)
        for path in ("", "="):
            self.wait_question(state, path)
            state.trigger("=")
        self.wait_question(state, "==")
        state.go_back()
        self.wait_question(state, "=")
        state.trigger("-")
        self.wait_question(state, "=-")
        active = state.active_answers()
        self.assertEqual([item["key"] for item in active], ["=", "-"])
        state.stop()
        worker.join(2)

    def test_main_plan_is_strictly_complete(self):
        state = State(main_lookahead=7)
        worker, _ = self.start_ask(state)
        snapshot = state.main_planning_snapshot()
        raw = {p: ['Is it for you?', "ask", "ask"] for p in snapshot["paths"]}
        raw[""][0] = snapshot["root"]["question"]
        self.assertEqual(len(parse_main_plan(json.dumps({"nodes": raw}), snapshot)), 127)
        raw.pop("======")
        with self.assertRaises(ValueError):
            parse_main_plan(json.dumps({"nodes": raw}), snapshot)
        state.stop()
        worker.join(2)

    def test_auto_submit_keeps_the_next_cached_question(self):
        state = self.cached(auto_submit=2)
        state.trigger("=")
        self.assertEqual(state.inbox, [])
        state.trigger("-")
        self.assertEqual(state.pending, [])
        self.assertEqual(state.cursor, "=-")
        self.assertIsNotNone(state.snapshot().node)
        self.assertEqual([e["key"] for e in state.inbox[0]["events"]], ["=", "-"])

    def test_every_single_answer_can_be_sent_automatically(self):
        state = self.cached(auto_submit=1)
        state.trigger("=")
        self.assertEqual(len(state.inbox), 1)
        self.assertEqual(state.pending, [])

    def test_side_back_retracts_only_the_latest_unsent_answer(self):
        state = self.cached()
        state.trigger("=")
        original = state.snapshot().node
        state.trigger("-")
        state.go_back()
        self.assertEqual(state.snapshot().node, original)
        self.assertEqual(state.cursor, "=")
        state.trigger("=")
        state.trigger("0")
        self.assertEqual([e["key"] for e in state.drain()[0]["events"]], ["=", "="])

    def test_back_also_retracts_an_automatically_sent_answer(self):
        state = self.cached(auto_submit=2)
        state.trigger("=")
        second = state.snapshot().node
        state.trigger("-")
        state.go_back()
        self.assertEqual(state.snapshot().node, second)
        self.assertEqual(state.records[1]["status"], "withdrawn")
        self.assertEqual([item["kind"] for item in state.inbox], ["revision"])

    def test_any_old_answer_can_be_changed_and_later_answers_are_withdrawn(self):
        state = self.cached(auto_submit=2)
        for key in "=-=":
            state.trigger(key)
        state.revise_record(1, "-")
        self.assertEqual([r["status"] for r in state.records], ["sent", "withdrawn", "withdrawn"])
        self.assertEqual(state.active_answers()[0]["key"], "-")
        self.assertEqual(state.pending, [])
        self.assertEqual(len(state.outstanding), 1)
        self.assertEqual(state.drain()[0]["kind"], "revision")

    def test_any_old_answer_can_be_withdrawn_without_replacing_it(self):
        state = self.cached()
        state.trigger("=")
        state.revise_record(1, None)
        self.assertEqual(state.active_answers(), [])
        self.assertEqual(state.inbox[0]["updated"]["status"], "withdrawn")

    def test_finish_flushes_unsent_answers_and_requires_all_feedback_handled(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.cached(auto_submit=2)
            agent = Agent(Config(Path(directory)), state)
            state.trigger("=")
            result = agent.dispatch("finish", {"summary": 'Done', "artifacts": []})
            self.assertTrue(result["deferred"])
            self.assertNotEqual(state.mode, "done")
            agent.apply_inbox()
            result = agent.dispatch("finish", {"summary": 'Done', "artifacts": []})
            self.assertEqual(result["unhandled_feedback"], ["feedback-1"])
            result = agent.dispatch("finish", {"summary": 'Done', "artifacts": [],
                                               "completed_feedback": ["feedback-1"]})
            self.assertTrue(result["finished"])
            self.assertEqual(state.outstanding, {})

    def test_new_projects_always_use_different_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workbench"
            first = create_project(root, 'Write articles')
            second = create_project(root, 'Write articles')
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, root.resolve())
            self.assertTrue(first.is_dir() and second.is_dir())


if __name__ == "__main__":
    unittest.main()
