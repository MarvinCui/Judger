import copy
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

from trigger_agent.codex_bridge import CodexBridge, CodexError
from trigger_agent.config import Config
from trigger_agent.engine import Agent
from trigger_agent.state import Node, State


class FakeRPC:
    def __init__(self, *_):
        self.events = queue.Queue()
        self.calls = []
        self.sent = []
        self.turn = 0
        self.on_start = None
        self.race = False
        self.fail = False
        self.pause = False

    def request(self, method, params, timeout=45):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "model/list":
            return {"data": [{"model": "cli-default", "isDefault": True}]}
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "thread-real"}}
        if method == "turn/start":
            self.turn += 1
            if self.on_start:
                self.on_start(self.turn)
            if not self.pause:
                self.complete()
            return {"turn": {"id": str(self.turn)}}
        if method == "turn/steer":
            if self.race:
                raise CodexError("No active turn")
            return {"turnId": str(self.turn)}
        return {}

    def complete(self):
        self.events.put({"method": "turn/completed", "params": {
            "threadId": "thread-real", "turn": {"id": str(self.turn),
            "status": "failed" if self.fail else "completed", "error": {"message": "failed"}}}})

    def send(self, message):
        self.sent.append(message)

    def close(self):
        pass


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = State(auto_submit=2)
        self.state.task = 'Build a webpage'
        self.rpc = FakeRPC()
        self.bridge = CodexBridge(Config(self.root), self.state, lambda *a: self.rpc)

    def tearDown(self):
        self.state.stop()
        self.bridge.close()
        self.temp.cleanup()

    def test_codex_mode_sends_work_to_codex_instead_of_writing_itself(self):
        agent = Agent(Config(self.root, executor="codex"), self.state)
        names = set(agent.schema_by_name)
        self.assertIn("execute_codex", names)
        self.assertFalse(names & {"write_file", "edit_file", "run_command"})

    def test_cli_catalog_default_and_canonical_answers_are_sent(self):
        self.state._record("main", 'Build a webpage?', "=", 'Yes', 'Main', {})
        result = self.bridge.run('Write index.html')
        self.assertTrue(result["codex_completed"])
        params = dict(self.rpc.calls)["thread/start"]
        self.assertEqual(params["model"], "cli-default")
        self.assertEqual(params["sandbox"], "workspace-write")
        self.assertEqual(params["approvalPolicy"], "on-request")
        self.assertIn('Build a webpage?', dict(self.rpc.calls)["turn/start"]["input"][0]["text"])

    def test_live_feedback_steers_the_active_turn(self):
        def started(turn):
            if turn == 1:
                with self.state.cv:
                    self.state._enqueue("feedback", {"events": [{"effect": 'Add an image'}]})
        self.rpc.on_start = started
        result = self.bridge.run('Finish it')
        steers = [p for m, p in self.rpc.calls if m == "turn/steer"]
        self.assertEqual(len(steers), 1)
        self.assertEqual(steers[0]["expectedTurnId"], "1")
        self.assertIn('Add an image', steers[0]["input"][0]["text"])
        self.assertEqual(result["completed_feedback"], ["feedback-1"])
        self.assertFalse(self.state.outstanding)

    def test_completion_race_queues_exactly_one_followup_turn(self):
        self.rpc.race = True
        self.rpc.on_start = lambda turn: self.state.submit_text('Change the title') if turn == 1 else None
        self.bridge.run('Finish it')
        starts = [p for m, p in self.rpc.calls if m == "turn/start"]
        self.assertEqual(len(starts), 2)
        self.assertIn('Change the title', starts[1]["input"][0]["text"])
        self.assertFalse(self.state.inbox)

    def test_last_unsent_side_answer_gets_its_own_followup_before_completion(self):
        self.state.nodes = {"": Node('Add an image?', 'Homepage', 'Add an image', 'No image')}
        self.rpc.on_start = lambda turn: self.state.trigger("=") if turn == 1 else None
        result = self.bridge.run('Finish it')
        self.assertEqual(self.rpc.turn, 2)
        self.assertFalse(self.state.pending)
        self.assertFalse(self.state.outstanding)
        self.assertIn("feedback-1", result["completed_feedback"])

    def test_failed_turn_does_not_claim_feedback_was_completed(self):
        self.rpc.fail = True
        with self.state.cv:
            self.state._enqueue("revision", {"answers": []})
        self.state.drain()
        with self.assertRaisesRegex(CodexError, "failed"):
            self.bridge.run('Finish it')
        self.assertIn("feedback-1", self.state.outstanding)

    def test_existing_project_resumes_its_codex_thread(self):
        self.bridge.connect()
        rpc = FakeRPC()
        second = CodexBridge(Config(self.root), self.state, lambda *a: rpc)
        second.connect()
        self.assertIn("thread/resume", dict(rpc.calls))
        self.assertEqual(dict(rpc.calls)["thread/resume"]["threadId"], "thread-real")
        second.close()

    def test_approval_waits_for_a_real_user_decision(self):
        self.bridge.connect()
        event = {"id": 90, "method": "item/commandExecution/requestApproval", "params": {"command": "npm install", "reason": 'Dependencies need to be downloaded'}}
        worker = threading.Thread(target=self.bridge._approval, args=(event,))
        worker.start()
        deadline = time.monotonic() + 2
        while self.state.mode != "question" and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(any(m.get("id") == 90 for m in self.rpc.sent))
        self.state.trigger("-")
        worker.join(1)
        self.assertEqual(self.rpc.sent[-1], {"id": 90, "result": {"decision": "decline"}})


if __name__ == "__main__":
    unittest.main()
