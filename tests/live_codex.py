"""Opt-in integration: real installed Codex, project files, and an in-flight follow-up."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trigger_agent.codex_bridge import CodexBridge
from trigger_agent.config import APP_ROOT, Config
from trigger_agent.execution import Execution
from trigger_agent.projects import create_project
from trigger_agent.state import State


def agent_flow():
    from trigger_agent.engine import Agent
    from trigger_agent.config import load_key
    root = create_project(APP_ROOT / ".local" / "qa", "deepseek-to-codex")
    state = State(lookahead=0)
    state.task = 'Build a single-page English introduction to Python for beginners in index.html with three learning cards and no dependencies. No further questions: delegate directly to Codex and check the file.'
    execution = Execution(root, state)
    agent = Agent(Config(root, api_key=load_key(), executor="codex", lookahead=0), state)
    worker = threading.Thread(target=agent.run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 210
    followed = False
    try:
        while time.monotonic() < deadline:
            if agent.codex.active and not followed:
                state.submit_text('Set the main webpage heading to "Learning Python with Three Keys".')
                followed = True
            if state.mode == "question" and state.question and state.answer is None:
                state.trigger("=")
            if state.mode == "done":
                break
            if state.mode == "error":
                raise AssertionError(state.lines[-12:])
            time.sleep(.05)
        assert state.mode == "done", state.lines[-12:]
        assert followed
        assert 'Learning Python with Three Keys' in (root / "index.html").read_text()
        assert state.artifacts
        assert not state.outstanding
        assert execution.entry
        print(json.dumps({"passed": True, "automatic_handoff": True, "followup": True,
                          "finish": True, "project": str(root), "artifacts": state.artifacts,
                          "deepseek_requests": state.requests}, ensure_ascii=False, indent=2))
    finally:
        state.stop()
        worker.join(2)
        agent.codex.close()
        execution.close()


def main():
    if "--agent" in sys.argv:
        return agent_flow()
    if "--models" in sys.argv:
        from trigger_agent.codex_bridge import RPC
        rpc = RPC("codex", APP_ROOT)
        try:
            rpc.request("initialize", {"clientInfo": {"name": "trigger_agent", "title": "Judger", "version": "0.2.0"}})
            rpc.send({"method": "initialized", "params": {}})
            result = rpc.request("model/list", {"limit": 100})
            print(json.dumps([{k: m.get(k) for k in ("model", "isDefault", "hidden")} for m in result["data"]], indent=2))
        finally:
            rpc.close()
        return
    root = create_project(APP_ROOT / ".local" / "qa", "codex-live")
    state = State(lookahead=0)
    state.task = 'Create a single English index.html test page with three cards and the title Draft. No external dependencies.'
    execution = Execution(root, state)
    bridge = CodexBridge(Config(root, executor="codex"), state)
    result, errors = [], []

    def work():
        try:
            result.append(bridge.run('Complete the test webpage and mark cards with data-trigger-card. Work only on this task; do not invoke other tasks or subagents.'))
        except Exception as error:
            errors.append(str(error))

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    deadline = time.monotonic() + 150
    try:
        while not bridge.active and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if bridge.active:
            state.submit_text('Update: change the main page heading to "An Update Received While Working". Apply it to index.html.')
        while worker.is_alive() and time.monotonic() < deadline:
            worker.join(.1)
        if worker.is_alive():
            raise TimeoutError("Codex live test exceeded 150s")
        if errors:
            raise RuntimeError(errors[0])
        page = (root / "index.html").read_text(encoding="utf-8")
        assert 'An Update Received While Working' in page, state.lines[-15:]
        assert not state.outstanding
        assert result[0]["codex_completed"]
        url = execution.open(browser=False)
        execution.key("=")
        assert execution.values["color"] == .6
        assert "saturate(1.20)" in (root / "trigger-style.css").read_text()
        print(json.dumps({"passed": True, "project": str(root), "thread_id": bridge.thread_id,
                          "preview": url.split('/#')[0], "artifacts": result[0]["artifacts"],
                          "inflight_followup": True}, ensure_ascii=False, indent=2))
    finally:
        state.stop()
        worker.join(2)
        bridge.close()
        execution.close()


if __name__ == "__main__":
    main()
