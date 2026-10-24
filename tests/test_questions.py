import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from trigger_agent.config import Config
from trigger_agent.engine import Agent, demo_tree, parse_plan
from trigger_agent.questions import validate_question
from trigger_agent.state import State


class QuestionTests(unittest.TestCase):
    def test_english_questions_keep_word_limits_and_reject_jargon_in_any_case(self):
        self.assertEqual(validate_question('Would you like to write something?'),
                         'Would you like to write something?')
        with self.assertRaises(ValueError):
            validate_question('Is it for a team of ten or more people to use together?')
        with self.assertRaises(ValueError):
            validate_question('Should we EXPAND MORE FULLY?')

    def test_direct_questions_are_accepted(self):
        for question in ('Would you like to write something?', 'Is it for other people?', 'Will you post it online?', 'Does this app need the internet?'):
            with self.subTest(question=question):
                self.assertEqual(validate_question(question), question)

    def test_long_abstract_and_multiple_questions_are_rejected(self):
        for question in ('Should we expand more fully along the current direction?', 'Would you like an article? Is it for a teacher?',
                         'Would you like an article with a detailed introduction and several examples that also covers every unusual edge case?'):
            with self.subTest(question=question), self.assertRaises(ValueError):
                validate_question(question)

    def test_bad_main_question_is_not_displayed(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(lookahead=0)
            agent = Agent(Config(Path(directory)), state)
            with self.assertRaises(ValueError):
                agent.dispatch("ask_user", {"question": 'Should we expand more fully along the current direction?',
                                            "on_accept": 'Yes', "on_reject": 'No'})
            self.assertIsNone(state.question)

    def test_bad_cached_question_is_not_installed(self):
        state = State(lookahead=2)
        snapshot = state.planning_snapshot()
        nodes = {path: asdict(node) for path, node in demo_tree(snapshot).items()}
        nodes[""]["question"] = 'Should we expand more fully along the current direction?'
        with self.assertRaises(ValueError):
            parse_plan(json.dumps({"nodes": nodes}), snapshot)
        self.assertEqual(state.nodes, {})

    def test_small_trees_do_not_refill_forever_without_input(self):
        for depth in (1, 2, 4):
            with self.subTest(depth=depth):
                state = State(lookahead=depth)
                snapshot = state.planning_snapshot()
                state.install_plan(snapshot, demo_tree(snapshot))
                self.assertFalse(state.plan_requested)


if __name__ == "__main__":
    unittest.main()
