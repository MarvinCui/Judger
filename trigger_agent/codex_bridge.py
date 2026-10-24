"""A small, persistent Codex app-server client (newline-delimited JSON RPC)."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import Config, redact
from .state import State


class CodexError(RuntimeError):
    pass


class RPC:
    def __init__(self, binary: str, cwd: Path):
        if not shutil.which(binary):
            raise CodexError('Codex was not found. Install Codex CLI and run codex login.')
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        self.process = subprocess.Popen([binary, "app-server", "--listen", "stdio://"],
                                        cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", bufsize=1)
        self.events = queue.Queue()
        self.lock = threading.RLock()
        self.pending: dict[int, queue.Queue] = {}
        self.sequence = 0
        self.stderr: list[str] = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.errors = threading.Thread(target=self._errors, daemon=True)
        self.reader.start()
        self.errors.start()

    def send(self, message: dict) -> None:
        with self.lock:
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                raise CodexError('The Codex connection closed.') from error

    def request(self, method: str, params: dict, timeout: float = 45) -> dict:
        with self.lock:
            self.sequence += 1
            identity = self.sequence
            reply = self.pending[identity] = queue.Queue(maxsize=1)
            self.send({"id": identity, "method": method, "params": params})
        try:
            message = reply.get(timeout=timeout)
        except queue.Empty:
            raise CodexError(f'Codex did not respond in time ({method}).') from None
        finally:
            with self.lock:
                self.pending.pop(identity, None)
        if "error" in message:
            raise CodexError(message["error"].get("message", 'Codex request failed.'))
        return message.get("result", {})

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "method" in message:
                    self.events.put(message)
                elif "id" in message:
                    with self.lock:
                        reply = self.pending.get(message["id"])
                        if reply:
                            reply.put(message)
        finally:
            failure = {"error": {"message": 'The Codex process exited.'}}
            with self.lock:
                for reply in self.pending.values():
                    if reply.empty():
                        reply.put(failure)
            self.events.put({"method": "connection/closed", "params": {}})

    def _errors(self) -> None:
        for line in self.process.stderr:
            self.stderr.append(redact(line.rstrip()))
            self.stderr = self.stderr[-25:]

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=1)
        self.errors.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


def requirement_text(items: list[dict]) -> str:
    return ('The user added or corrected requirements. The answers in a revision are the complete valid answer set; withdrawn answers no longer apply. Modify the actual files and address every item below before finishing.\n'
            + json.dumps(items, ensure_ascii=False, indent=2))


class CodexBridge:
    def __init__(self, config: Config, state: State, rpc_factory=RPC):
        self.config, self.state = config, state
        self.rpc_factory = rpc_factory
        self.rpc = None
        self.thread_id = ""
        self.turn_id = ""
        self.active = False
        self.changed: set[str] = set()
        self.meta = config.workspace / ".trigger-agent" / "codex.json"

    def connect(self) -> None:
        if self.rpc:
            return
        self.state.say('Connecting to Codex.')
        rpc = self.rpc_factory(self.config.codex_bin, self.config.workspace)
        try:
            rpc.request("initialize", {"clientInfo": {"name": "judger",
                        "title": "Judger", "version": "0.3.0"}})
            rpc.send({"method": "initialized", "params": {}})
            catalog = rpc.request("model/list", {"limit": 100}).get("data", [])
            default_model = next((m["model"] for m in catalog if m.get("isDefault")), None)
            params = {"cwd": str(self.config.workspace), "sandbox": "workspace-write",
                      "approvalPolicy": "on-request"}
            if default_model:
                params["model"] = default_model
            if self.meta.is_file():
                saved = json.loads(self.meta.read_text(encoding="utf-8"))
                params["threadId"] = saved["thread_id"]
                result = rpc.request("thread/resume", params)
            else:
                result = rpc.request("thread/start", params)
            self.thread_id = result["thread"]["id"]
            self.meta.parent.mkdir(parents=True, exist_ok=True)
            self.meta.write_text(json.dumps({"thread_id": self.thread_id}, indent=2), encoding="utf-8")
            self.meta.chmod(0o600)
            self.rpc = rpc
        except Exception:
            rpc.close()
            raise

    def _start(self, text: str) -> None:
        result = self.rpc.request("turn/start", {"threadId": self.thread_id,
                                  "input": [{"type": "text", "text": text}]})
        self.turn_id = result["turn"]["id"]
        self.active = True

    def _approval(self, event: dict) -> None:
        method, params = event["method"], event.get("params", {})
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            self.state.say(params.get("reason") or 'Codex needs permission for this step.', "Codex: ")
            if params.get("command"):
                self.state.say(str(params["command"]), 'Action: ')
            if params.get("grantRoot"):
                self.state.say(params["grantRoot"], 'Location: ')
            answer = self.state.ask({"question": 'Allow Codex to do this?', "immediate": "yes",
                                     "on_accept": 'Allow this once', "on_reject": 'Decline this once'})
            while answer.get("answer") == "unpack":
                self.state.say(json.dumps(params, ensure_ascii=False), 'Details: ')
                answer = self.state.ask({"question": 'Allow Codex to do this?', "immediate": "yes"})
            self.rpc.send({"id": event["id"], "result": {
                "decision": "accept" if answer.get("answer") == "accept" else "decline"}})
        else:
            # Unsupported requests get a real RPC error so the agent can recover.
            self.rpc.send({"id": event["id"], "error": {"code": -32601,
                           "message": "This client supports command/file approvals only. Use a text message instead."}})

    def run(self, prompt: str) -> dict:
        self.connect()
        with self.state.cv:
            initial = list(self.state.outstanding.values())
            answers = self.state.active_answers()
        preview = getattr(self.state, "execution", None)
        design = preview.spec() if preview else {}
        text = (f"Complete this task in the current project: {self.state.task}\n\nExecution brief:\n{prompt}\n\nValid answers:\n{json.dumps(answers, ensure_ascii=False)}\nLive-control settings:\n{json.dumps(design, ensure_ascii=False)}\nWork only in the current project directory. Do not publish, push, or send external messages. Request additional permissions normally when needed. For webpages, prefer a directly previewable index.html with relative asset paths. This webpage is the user's actual project. All Judger questions, keys, categories, history, and controls live in the separate Terminal UI. Never add Judger software panels, chat boxes, adjustment controls, or an embedded terminal to the webpage. Use variables from trigger-style.css and data-trigger-card / data-trigger-accent attributes so color and spacing can update live. Do not overwrite trigger-style.css; the user controls it through keypresses. Report the actual files changed and validation performed.\n" + requirement_text(initial))
        archive = self.meta.parent / "codex-prompts"
        archive.mkdir(exist_ok=True)
        (archive / f"{time.time_ns()}.md").write_text(redact(text, self.config.api_key), encoding="utf-8")
        self.state.say('Codex has the brief. You can keep answering side questions.')
        self.state.executor_status = 'Codex is working'
        accepted = {item["id"] for item in initial}
        completed: set[str] = set()
        final = ""
        self._start(text)
        try:
            while not self.state.stopped.is_set():
                # Consume live feedback on this worker; the question planners and UI keep running.
                items = self.state.drain()
                if items:
                    update = requirement_text(items)
                    try:
                        self.rpc.request("turn/steer", {"threadId": self.thread_id,
                            "expectedTurnId": self.turn_id, "input": [{"type": "text", "text": update}]})
                        accepted.update(item["id"] for item in items if "id" in item)
                        self.state.say('Your update reached the active Codex task.')
                        self.state.log("codex_followup", items=items, turn_id=self.turn_id)
                    except CodexError:
                        # A turn may finish between draining and steering. Start a follow-up
                        # only after its completion event, preserving exactly-once submission.
                        with self.state.cv:
                            self.state.inbox = items + self.state.inbox
                try:
                    event = self.rpc.events.get(timeout=0.08)
                except queue.Empty:
                    continue
                method, params = event["method"], event.get("params", {})
                if "id" in event:
                    self._approval(event)
                elif params.get("threadId", self.thread_id) != self.thread_id:
                    continue
                elif method == "item/agentMessage/delta":
                    self.state.chunk(params.get("delta", ""))
                elif method == "item/started" and params.get("item", {}).get("type") == "agentMessage":
                    self.state.say("", "Codex: ")
                elif method == "item/completed":
                    item = params.get("item", {})
                    if item.get("type") == "agentMessage":
                        final = item.get("text", final)
                    elif item.get("type") == "fileChange" and item.get("status") == "completed":
                        for change in item.get("changes", []):
                            self.changed.add(change["path"])
                        if preview:
                            preview.discover()
                elif method == "turn/completed" and params.get("turn", {}).get("id") == self.turn_id:
                    turn = params["turn"]
                    self.active = False
                    if turn["status"] != "completed":
                        raise CodexError((turn.get("error") or {}).get("message") or 'Codex did not complete this round.')
                    with self.state.cv:
                        for identity in accepted:
                            self.state.outstanding.pop(identity, None)
                        completed.update(accepted)
                        self.state.send_pending(automatic=True) if self.state.pending else None
                        updates = self.state.drain()
                    if updates:
                        accepted = {item["id"] for item in updates if "id" in item}
                        self._start(requirement_text(updates))
                    else:
                        break
                elif method == "connection/closed":
                    raise CodexError('Codex disconnected. Check your local sign-in.')
            if self.state.stopped.is_set():
                return {"cancelled": True}
            if preview:
                preview.discover()
            artifacts = []
            for item in self.changed:
                path = (self.config.workspace / item).resolve()
                if path.is_relative_to(self.config.workspace) and path.is_file():
                    artifacts.append(str(path))
            self.state.executor_status = 'Codex finished this round'
            return {"codex_completed": True, "summary": final,
                    "artifacts": sorted(artifacts), "completed_feedback": sorted(completed),
                    "thread_id": self.thread_id}
        finally:
            if self.active and self.state.stopped.is_set():
                try:
                    self.rpc.request("turn/interrupt", {"threadId": self.thread_id,
                                     "turnId": self.turn_id}, timeout=2)
                except CodexError:
                    pass
            self.active = False

    def close(self) -> None:
        if self.rpc:
            self.rpc.close()
            self.rpc = None
