import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from trigger_agent.config import Config
from trigger_agent.engine import Agent
from trigger_agent.state import Node, State


def call(name, arguments, identity="a"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class ScriptAPI:
    def __init__(self, state, responses):
        self.state, self.responses = state, iter(responses)
        self.requests = []

    def stream(self, messages, *args, **kwargs):
        self.requests.append(json.loads(json.dumps(messages)))
        response = next(self.responses)
        if callable(response):
            response = response()
        return response, {}


class AgentTests(unittest.TestCase):
    def test_writing_new_artifact_reanchors_focus_without_rebinding_pending_path(self):
        with tempfile.TemporaryDirectory() as directory:
            from trigger_agent.state import Focus
            state = State(lookahead=0)
            agent = Agent(Config(Path(directory)), state)
            agent.dispatch("set_focus", {"target": "calculator.py", "label": 'Scope',
                                        "positive_effect": 'More use cases', "negative_effect": 'Core use cases'})
            agent.dispatch("write_file", {"path": "article.md", "content": 'Body'})
            self.assertEqual(state.focus.target, "article.md")
            self.assertEqual(state.focus.label, 'How to write this article')
            state.nodes[""] = Node('Is it for other people?', "article.md", 'For other people', 'Not for other people')
            state.trigger("=")
            agent.dispatch("write_file", {"path": "calculator.py", "content": "print(5)"})
            self.assertEqual(state.focus.target, "calculator.py")
            self.assertEqual(state.side_focus.target, "article.md")
            self.assertEqual(state.pending[0]["target"], "article.md")

    def test_agent_creates_real_artifact_and_receives_unpack(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(lookahead=0)
            state.task = 'Write an article'
            api = ScriptAPI(state, [
                call("ask_user", {"question": 'Write a short article?', "on_accept": 'Write a short article', "on_reject": 'Explore other tasks'}),
                call("write_file", {"path": "article.md", "content": '# Article\nBody'}),
                call("finish", {"summary": 'Article saved.', "artifacts": ["article.md"]}),
            ])
            agent = Agent(Config(Path(directory)), state, api=api)
            worker = threading.Thread(target=agent.run)
            worker.start()
            deadline = time.monotonic() + 3
            while state.mode != "question" and time.monotonic() < deadline:
                time.sleep(0.005)
            state.trigger("0")
            while state.mode != "done" and time.monotonic() < deadline:
                time.sleep(0.005)
            state.stop()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual((Path(directory) / "article.md").read_text(), '# Article\nBody')
            results = [m for m in agent.messages if m["role"] == "tool"]
            self.assertEqual(json.loads(results[0]["content"])["answer"], "unpack")

    def test_new_feedback_skips_old_pending_write(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(lookahead=0)
            state.task = 'Create a file'

            def feedback_then_old_tool():
                state.nodes[""] = Node('Is it for you?', 'File', 'For you', 'Not for you')
                state.trigger("=")
                state.trigger("0")
                return call("write_file", {"path": "wrong.txt", "content": "must not execute"})

            api = ScriptAPI(state, [feedback_then_old_tool,
                                   call("write_file", {"path": "right.txt", "content": "new plan"}),
                                   call("finish", {"summary": 'Done', "artifacts": ["right.txt"],
                                                   "completed_feedback": ["feedback-1"]})])
            agent = Agent(Config(Path(directory)), state, api=api)
            worker = threading.Thread(target=agent.run)
            worker.start()
            deadline = time.monotonic() + 3
            while state.mode != "done" and time.monotonic() < deadline:
                time.sleep(0.005)
            state.stop()
            worker.join(1)
            self.assertFalse((Path(directory) / "wrong.txt").exists())
            self.assertTrue((Path(directory) / "right.txt").exists())
            self.assertTrue(any('The user submitted side answers during execution' in (m.get("content") or "")
                                for m in api.requests[1]))
            self.assertTrue(any('"skipped": true' in m.get("content", "") for m in agent.messages if m["role"] == "tool"))

    def test_multiple_calls_are_all_rejected_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(lookahead=0)
            first = call("write_file", {"path": "bad1", "content": "bad"})
            first["tool_calls"].extend(call("write_file", {"path": "bad2", "content": "bad"}, "b")["tool_calls"])
            api = ScriptAPI(state, [first, call("finish", {"summary": 'Done', "artifacts": []})])
            agent = Agent(Config(Path(directory)), state, api=api)
            worker = threading.Thread(target=agent.run)
            worker.start()
            deadline = time.monotonic() + 3
            while state.mode != "done" and time.monotonic() < deadline:
                time.sleep(0.005)
            state.stop()
            worker.join(1)
            self.assertFalse((Path(directory) / "bad1").exists())
            self.assertFalse((Path(directory) / "bad2").exists())
            results = [m for m in agent.messages if m["role"] == "tool"]
            self.assertEqual(len(results), 3)


if __name__ == "__main__":
    unittest.main()
