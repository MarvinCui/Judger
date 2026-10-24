"""Optional real-API check: change a completed main answer and revise its artifact."""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trigger_agent.config import Config, load_key
from trigger_agent.engine import Agent
from trigger_agent.state import State


def main():
    with tempfile.TemporaryDirectory(prefix="trigger-revision-") as directory:
        root = Path(directory)
        state = State(lookahead=0)
        state.task = ('This tests history corrections. First ask "Is the article for other people?". If yes, write "Yes" to audience.txt; if no, write "No", then finish. If an earlier answer is corrected, read and update that same file using the new answer. Handle the matching feedback id before finishing. No other questions or deliverables are needed.')
        agent = Agent(Config(root, api_key=load_key(), lookahead=0, max_steps=20), state)
        worker = threading.Thread(target=agent.run, daemon=True)
        worker.start()
        revised = False
        answered = False
        deadline = time.monotonic() + 150
        try:
            while time.monotonic() < deadline:
                view = state.snapshot()
                if view.mode == "error":
                    raise RuntimeError("\n".join(view.lines[-5:]))
                if view.mode == "question" and view.question and not answered:
                    state.trigger("=")
                    answered = True
                if view.mode == "done":
                    if not revised:
                        assert (root / "audience.txt").read_text().strip() == 'Yes'
                        question = next(r for r in state.records if r["kind"] == "main")
                        state.revise_record(question["id"], "-")
                        revised = True
                    elif not state.outstanding and not state.inbox:
                        assert (root / "audience.txt").read_text().strip() == 'No'
                        print("PASS: historical main answer changed from yes to no; file rewritten; all corrections handled.", flush=True)
                        return
                time.sleep(0.03)
            raise RuntimeError("Historical revision test timed out")
        finally:
            state.stop()
            worker.join(2)


if __name__ == "__main__":
    main()
