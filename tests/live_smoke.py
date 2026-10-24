"""Opt-in integration check. Calls the real API and uses a temporary workspace."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trigger_agent.config import Config, load_key
from trigger_agent.engine import Agent, main_planner_loop, planner_loop
from trigger_agent.state import State


def main():
    with tempfile.TemporaryDirectory(prefix="trigger-live-") as directory:
        root = Path(directory)
        config = Config(root, api_key=load_key(), allow_shell=True, lookahead=4, max_steps=25)
        state = State(lookahead=4, main_lookahead=7, auto_submit=2)
        state.task = (
            'This is a small integration test. First use ask_user to ask "Would you like an adder?". If I request a rephrase, ask "Add two and three?". After yes, create calculator.py with add(a,b) and a main entry that prints add(2,3), which is 5. Run python3 calculator.py to verify it. Also write article.md with about 120 English words explaining the three-key interaction: 0 sends side answers while working and rephrases main questions. Then finish. Revise the article if new answers arrive, while preserving calculator correctness.'
        )
        agent = Agent(config, state)
        main_thread = threading.Thread(target=agent.run, daemon=True)
        planner = threading.Thread(target=planner_loop, args=(config, state), daemon=True)
        main_planner = threading.Thread(target=main_planner_loop, args=(config, state), daemon=True)
        main_thread.start()
        planner.start()
        main_planner.start()
        answered = set()
        answers = []
        feedback_sent = False
        first_finish = False
        first_article = None
        selected_effects = []
        branch_latency = []
        deadline = time.monotonic() + 300
        last_print = 0
        try:
            while time.monotonic() < deadline:
                view = state.snapshot()
                if view.mode == "error":
                    raise RuntimeError("\n".join(view.lines[-10:]))
                if view.mode == "question" and view.question and view.question["id"] not in answered:
                    answered.add(view.question["id"])
                    key = "0" if not answers else "="
                    answers.append(key)
                    print("Main question:", view.question["question"], "->", key, flush=True)
                    state.trigger(key)
                if view.mode == "done":
                    if feedback_sent and state.requests > submitted_request_count:
                        break
                    first_finish = True
                    if not feedback_sent and view.node is not None:
                        first_article = (root / "article.md").read_text(encoding="utf-8")
                        for key in "=-=":
                            before = time.perf_counter()
                            state.trigger(key)
                            next_view = state.snapshot()
                            branch_latency.append((time.perf_counter() - before) * 1000)
                            if next_view.node is None:
                                raise RuntimeError("Expected cached next question")
                        selected_effects = [r["effect"] for r in state.records if r["kind"] == "side"]
                        submitted_request_count = state.requests
                        feedback_sent = True
                        print("Answered three cached side questions; automatic submission and final flush will handle them.", flush=True)
                if time.monotonic() - last_print > 12:
                    print("Progress:", view.mode, "requests=", view.requests, "planner=", view.plan_state, flush=True)
                    last_print = time.monotonic()
                time.sleep(0.03)
            else:
                raise RuntimeError("Live check timed out")
            if not first_finish or not feedback_sent or len(answers) < 2:
                raise RuntimeError("Did not exercise question, unpack, cached choices, and revision")
            for name in ("calculator.py", "article.md"):
                if not (root / name).is_file():
                    raise RuntimeError("Missing artifact: " + name)
            output = subprocess.check_output([sys.executable, str(root / "calculator.py")], text=True).strip()
            if output != "5":
                raise RuntimeError("Calculator returned " + output)
            final_article = (root / "article.md").read_text(encoding="utf-8")
            if first_article == final_article:
                raise RuntimeError("Feedback was delivered but the requested article revision was not saved")
            tool_results = [json.loads(m["content"]) for m in agent.messages if m["role"] == "tool"]
            if not any(r.get("exit_code") == 0 for r in tool_results):
                raise RuntimeError("Agent did not run a successful verification command")
            print(json.dumps({"status": "passed", "main_answers": answers,
                              "feedback_path": "=-=", "cached_next_question_ms": branch_latency,
                              "feedback_effects": selected_effects, "article_revised": True,
                              "main_cache_nodes": len(state.main_nodes),
                              "unhandled_feedback": list(state.outstanding),
                              "requests": state.requests, "usage": state.usage,
                              "calculator_stdout": output,
                              "article": final_article},
                             ensure_ascii=False, indent=2))
        finally:
            state.stop()
            main_thread.join(2)
            planner.join(0.1)
            main_planner.join(0.1)


if __name__ == "__main__":
    main()
