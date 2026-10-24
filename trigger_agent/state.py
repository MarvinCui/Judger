from __future__ import annotations

import copy
import itertools
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from .config import Journal


@dataclass(frozen=True)
class Focus:
    target: str = 'Your task'
    label: str = 'What to make'
    positive_effect: str = 'Yes'
    negative_effect: str = 'No'


@dataclass(frozen=True)
class Node:
    question: str
    target: str
    positive_effect: str
    negative_effect: str


@dataclass(frozen=True)
class MainNode:
    question: str
    yes_next: str = "ask"
    no_next: str = "ask"


@dataclass
class View:
    mode: str
    focus: Focus
    question: dict | None
    pending: list[dict]
    node: Node | None
    plan_state: str
    notice: str
    lines: list[str]
    cursor: str
    requests: int
    usage: dict
    records: list[dict]
    outstanding: list[str]
    artifacts: list[str]
    auto_submit: int


class State:
    """All mode decisions happen under one lock, at keypress time."""

    def __init__(self, journal: Journal | None = None, lookahead: int = 4,
                 main_lookahead: int = 0, auto_submit: int = 0):
        self.cv = threading.Condition(threading.RLock())
        self.stopped = threading.Event()
        self.interrupt = threading.Event()
        self.mode = "running"
        self.executor_status = ""
        self.focus = Focus()
        self.side_focus = self.focus
        self.question: dict | None = None
        self.answer: dict | None = None
        self.pending: list[dict] = []
        self.inbox: list[dict] = []
        self.answer_history: list[dict] = []
        self.main_history: list[dict] = []
        self.main_retracted: list[dict] = []
        self.feedback_commits = 0
        self.side_revisiting = False
        self.side_path: list[dict] = []
        self.records: list[dict] = []
        self.record_revision = 0
        self.outstanding: dict[str, dict] = {}
        self.batch_seq = 0
        self.artifacts: list[str] = []
        self.auto_submit = auto_submit
        self.main_lookahead = main_lookahead
        self.main_generation = 0
        self.main_plan_requested = False
        self.main_plan_ready = False
        self.main_plan_error = ""
        self.main_nodes: dict[str, MainNode] = {}
        self.main_root: dict = {}
        self.lines: list[str] = []
        self.notice = 'Thinking of the next question.'
        self.nodes: dict[str, Node] = {}
        self.cursor = ""
        self.generation = 0
        self.plan_requested = bool(lookahead)
        self.planning = False
        self.plan_retry_at = 0.0
        self.plan_state = 'Almost ready' if lookahead else ""
        self.lookahead = lookahead
        self.seq = 0
        self.requests = 0
        self.usage: dict[str, int] = {}
        self.task = ""
        self.journal = journal or Journal(None)

    def log(self, kind: str, **fields: Any) -> None:
        self.journal.write({"event": kind, **fields})

    def say(self, text: str, prefix: str = "") -> None:
        with self.cv:
            for line in (prefix + text).split("\n"):
                self.lines.append(line)
            self.lines = self.lines[-1600:]
            self.log("display", text=prefix + text)

    def chunk(self, text: str) -> None:
        with self.cv:
            if not self.lines:
                self.lines.append("")
            parts = text.split("\n")
            self.lines[-1] += parts[0]
            self.lines.extend(parts[1:])
            self.lines = self.lines[-1600:]

    def count_request(self) -> None:
        with self.cv:
            self.requests += 1

    def add_usage(self, usage: dict) -> None:
        with self.cv:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self.usage[key] = self.usage.get(key, 0) + int(usage.get(key) or 0)

    def _reset_tree(self) -> None:
        review_path = self.cursor
        review_node = self.nodes.get(review_path) if self.side_revisiting else None
        self.nodes.clear()
        self.cursor = review_path if review_node else ""
        if review_node:
            self.nodes[review_path] = review_node
        self.generation += 1
        self.plan_requested = bool(self.lookahead)
        self.plan_state = 'Thinking of what to ask next' if self.lookahead else ""
        self.cv.notify_all()

    def set_focus(self, focus: Focus) -> None:
        with self.cv:
            if focus == self.focus:
                return
            self.focus = focus
            # A pending path retains its original anchor even if the main agent moves on.
            if not self.pending and not self.side_path and not self.side_revisiting:
                self.side_focus = focus
                self._reset_tree()
            self.log("focus", focus=asdict(focus))

    def trigger(self, key: str) -> None:
        if key not in {"=", "-", "0"}:
            return
        with self.cv:
            self.seq += 1
            self.log("key", key=key, mode=self.mode, seq=self.seq,
                     generation=self.generation, cursor=self.cursor,
                     question_id=(self.question or {}).get("id"))
            if self.mode == "question":
                if self.question is None:
                    self.notice = 'Preparing the next questions. One moment.'
                    return
                # Keep this state until the worker consumes the answer; rapid extra keys
                # must not accidentally become feedback on an unrelated running task.
                if self.answer is not None:
                    self.notice = 'Got it.'
                    return
                self.answer = {"answer": {"=": "accept", "-": "reject", "0": "unpack"}[key],
                               "question": copy.deepcopy(self.question)}
                self.notice = {"=": 'Okay.', "-": 'Got it.', "0": 'Let me rephrase.'}[key]
                self.cv.notify_all()
                return
            if key == "0":
                self.send_pending()
                return
            if len(self.pending) >= 64:
                self.notice = 'Lots of answers waiting. Press 0 to send them.'
                return
            node = self.nodes.get(self.cursor)
            if node is None:
                self.notice = 'Wait for the question before answering.' if self.lookahead else 'Press i to tell me what you want to change.'
                if self.lookahead and not self.planning and time.monotonic() >= self.plan_retry_at:
                    self._maybe_plan()
                    self.plan_retry_at = time.monotonic() + 3
                return
            focus = self.side_focus
            event = {"seq": self.seq, "key": key, "time": time.monotonic(),
                     "path": self.cursor, "generation": self.generation,
                     "focus": asdict(focus),
                     "question": node.question,
                     "node": asdict(node),
                     "target": node.target,
                     "effect": node.positive_effect if key == "=" else node.negative_effect,
                     "observed_output": "\n".join(self.lines[-5:])[-800:]}
            self.pending.append(event)
            event["record_id"] = self._record("side", node.question, key,
                                              event["effect"], event["target"], event)
            self.side_path.append(event)
            self.side_revisiting = False
            self.cursor += key
            self.notice = f"Recorded: {'Yes' if key == '=' else 'No'}."
            self.log("feedback_pending", **event)
            self._maybe_plan()
            if self.auto_submit and len(self.pending) >= self.auto_submit:
                self.send_pending(automatic=True)
            self.cv.notify_all()

    def _record(self, kind: str, question: str, key: str, effect: str,
                target: str, detail: dict) -> int:
        identity = len(self.records) + 1
        self.records.append({"id": identity, "kind": kind, "question": question,
                             "key": key, "effect": effect, "target": target,
                             "status": "pending" if kind == "side" else "answered",
                             "detail": copy.deepcopy(detail)})
        return identity

    def active_answers(self) -> list[dict]:
        return [{k: r[k] for k in ("id", "kind", "question", "key", "effect", "target")}
                for r in self.records if r["status"] != "withdrawn"]

    def _enqueue(self, kind: str, payload: dict) -> str:
        self.batch_seq += 1
        identity = f"feedback-{self.batch_seq}"
        item = {"kind": kind, "id": identity, **payload}
        self.inbox.append(item)
        self.outstanding[identity] = copy.deepcopy(item)
        self.interrupt.set()
        self.cv.notify_all()
        return identity

    def send_pending(self, automatic: bool = False) -> str | None:
        with self.cv:
            if not self.pending:
                self.notice = 'Your answers were already sent automatically.' if self.feedback_commits else 'No new answers yet.'
                return None
            events = copy.deepcopy(self.pending)
            self.pending.clear()
            self.feedback_commits += 1
            for event in events:
                self.records[event["record_id"] - 1]["status"] = "sent"
            self.answer_history.extend({"question": e["question"], "target": e["target"],
                                        "answer": "accept" if e["key"] == "=" else "reject",
                                        "effect": e["effect"]} for e in events)
            identity = self._enqueue("feedback", {"events": events})
            self.notice = 'Answers sent automatically.' if automatic else 'Answers sent.'
            self.log("feedback_commit", id=identity, automatic=automatic, events=events)
            # Retain the current path so automatic sending never removes the next question.
            return identity

    def revise_record(self, identity: int, key: str | None) -> None:
        with self.cv:
            if not 1 <= identity <= len(self.records) or key not in {"=", "-", "0", None}:
                return
            record = self.records[identity - 1]
            if record["kind"] == "design" and key == "0":
                key = None
            previous = copy.deepcopy(record)
            for later in self.records[identity:]:
                later["status"] = "withdrawn"
            record["status"] = "withdrawn" if key is None else "sent"
            if key is not None:
                record["key"] = key
                detail = record["detail"]
                if record["kind"] == "side":
                    node = detail["node"]
                    record["effect"] = (node["positive_effect"] if key == "=" else
                                        node["negative_effect"] if key == "-" else 'Rephrase')
                else:
                    record["effect"] = {"=": 'Yes', "-": 'No', "0": 'Rephrase'}[key]
            self.pending.clear()
            self.side_path = [e for e in self.side_path if e["record_id"] < identity]
            self.answer_history.clear()
            self.main_history = [e for e in self.main_history if e.get("record_id", identity) < identity]
            # Replace queued/outstanding requirements with one canonical correction.
            self.inbox = [item for item in self.inbox if item["kind"] == "text"]
            self.outstanding.clear()
            self._enqueue("revision", {"previous": previous, "updated": copy.deepcopy(record),
                                       "answers": self.active_answers(),
                                       "withdrawn_after": identity})
            self.main_generation += 1
            self.record_revision += 1
            if self.mode == "question":
                self.answer = {"answer": "history_revision"}
            self.side_revisiting = False
            self.side_focus = self.focus
            self._reset_tree()
            self.notice = 'Later answers withdrawn. I will use your correction.'
            self.log("history_revision", id=identity, key=key, answers=self.active_answers())
            self.cv.notify_all()

    def go_back(self) -> None:
        """Reopen a question in the visible conversation, without undoing tools."""
        with self.cv:
            self.seq += 1
            self.log("key", key="back", mode=self.mode, seq=self.seq,
                     question_id=(self.question or {}).get("id"))
            if self.mode == "question":
                if self.question is None:
                    self.notice = 'Questions are still preparing. Press v to edit an earlier answer.'
                    return
                if self.answer is not None:
                    # A fast '=, Backspace' can cancel an answer before the worker
                    # consumes it. Stay on this same visible question.
                    self.answer = None
                    self.notice = 'Answer this question again.'
                    self.log("answer_withdrawn", question=self.question)
                    return
                if not self.main_history:
                    self.notice = 'This is the first main question.'
                    return
                previous = self.main_history.pop()
                self.main_retracted.append(previous)
                old_id = previous["question"]["id"]
                if previous.get("record_id"):
                    self.record_revision += 1
                    self.records[previous["record_id"] - 1]["status"] = "withdrawn"
                    for record in self.records[previous["record_id"]:]:
                        record["status"] = "withdrawn"
                    self.side_path = [e for e in self.side_path if e["record_id"] < previous["record_id"]]
                    if any(item["kind"] != "text" for item in self.inbox) or self.outstanding:
                        self.inbox = [item for item in self.inbox if item["kind"] == "text"]
                        self.outstanding.clear()
                        if not self.inbox:
                            self.interrupt.clear()
                self.answer_history = [item for item in self.answer_history
                                       if not (isinstance(item.get("question"), dict)
                                               and item["question"].get("id") == old_id)]
                self.question = {**copy.deepcopy(previous["question"]),
                                 "id": f"q-{time.monotonic_ns()}", "revises": old_id}
                # Unsent side answers may depend on the assumption being revised.
                self.pending.clear()
                self.side_revisiting = False
                self.side_focus = self.focus
                self._reset_tree()
                self.notice = 'Back to the previous question. Please answer again.'
                self.log("main_back", question=self.question, retracted=previous)
                self.cv.notify_all()
                return
            if not self.pending:
                if self.side_path:
                    previous = copy.deepcopy(self.side_path[-1])
                    self.revise_record(previous["record_id"], None)
                    self.nodes = {previous["path"]: Node(**previous["node"])}
                    self.cursor = previous["path"]
                    self.side_focus = Focus(**previous["focus"])
                    self.side_revisiting = True
                    self.notice = 'Back to the previous question. Answer it again.'
                    return
                self.notice = ('Your side answers were sent. Press i to correct them.'
                               if self.feedback_commits else 'No previous side question yet.')
                return
            previous = self.pending.pop()
            if previous.get("record_id"):
                self.records[previous["record_id"] - 1]["status"] = "withdrawn"
                self.side_path = [e for e in self.side_path if e["record_id"] != previous["record_id"]]
            path = previous["path"]
            node = Node(**previous["node"])
            if self.nodes.get(path) != node:
                # A main question may have replaced the speculative tree. Rebuild
                # the actual visited path from its immutable question snapshots.
                self.nodes = {item["path"]: Node(**item["node"]) for item in self.pending}
            self.nodes[path] = node
            self.cursor = path
            self.side_focus = Focus(**previous["focus"])
            self.side_revisiting = True
            # Invalidate a refill started after the answer that was just withdrawn.
            self.generation += 1
            self.plan_requested = False
            self._maybe_plan()
            self.notice = 'Back to the previous question. Your last answer was withdrawn.'
            self.log("side_back", question=asdict(node), withdrawn=previous)
            self.cv.notify_all()

    def _maybe_plan(self) -> None:
        if not self.lookahead:
            return
        # Refill while two full questions are still cached on every branch.
        frontier = [self.cursor]
        for _ in range(min(3, self.lookahead)):
            if any(path not in self.nodes for path in frontier):
                self.plan_requested = True
                self.cv.notify_all()
                return
            frontier = [path + key for path in frontier for key in ("=", "-")]

    def submit_text(self, text: str, question_id: str | None = None) -> None:
        if not text.strip():
            return
        with self.cv:
            if question_id and self.mode == "question" and self.question and self.question["id"] == question_id:
                if self.answer is None:
                    self.answer = {"answer": "text", "text": text, "question": copy.deepcopy(self.question)}
                    self.cv.notify_all()
            else:
                self.inbox.append({"kind": "text", "text": text})
                self.interrupt.set()
                self.cv.notify_all()
            self.say(text, 'You: ')

    def ask(self, question: dict) -> dict:
        if not self.main_lookahead or question.get("immediate") == "yes":
            return self._ask_once(question)
        with self.cv:
            self.mode = "question"
            self.question = None
            self.main_root = copy.deepcopy(question)
            self.main_generation += 1
            generation = self.main_generation
            session = f"main-{generation}"
            self.main_plan_requested = True
            self.main_plan_ready = False
            self.main_plan_error = ""
            self.main_nodes = {}
            self.notice = 'Thinking ahead to the next questions.'
            self.cv.notify_all()
            while not self.main_plan_ready and not self.main_plan_error and not self.stopped.is_set():
                if self.main_generation != generation:
                    return {"answer": "history_revision"}
                self.cv.wait(0.2)
            if self.stopped.is_set():
                return {"answer": "cancelled"}
            if self.main_plan_error:
                self.notice = 'The next questions are not ready. Asking one at a time for now.'
                return self._ask_once(question)
        path = ""
        answers = []
        while not self.stopped.is_set():
            with self.cv:
                node = self.main_nodes.get(path)
                if node is None:
                    break
                current = {"question": node.question, "on_accept": 'Yes', "on_reject": 'No',
                           "main_path": path, "main_session": session}
            result = self._ask_once(current)
            if result["answer"] in {"cancelled", "history_revision"}:
                return result
            if result["answer"] == "revise":
                if any(r["question"].get("main_session") != session for r in result["retracted"]):
                    return result
                path = result["question"]["main_path"]
                answers = [a for a in answers if len(a["path"]) < len(path)]
                node = self.main_nodes[path]
                result = {**result, "answer": result["new_answer"]}
            answers.append({"path": path, **result})
            if result["answer"] not in {"accept", "reject"}:
                break
            step = "=" if result["answer"] == "accept" else "-"
            transition = node.yes_next if step == "=" else node.no_next
            path += step
            if transition == "work" or path not in self.main_nodes:
                break
        if len(answers) == 1:
            return answers[0]
        return {"answer": "batch", "answers": answers,
                "instruction": 'These are consecutive main questions actually answered by the user. Read them in order before continuing.'}

    def _ask_once(self, question: dict) -> dict:
        with self.cv:
            self.mode = "question"
            self.question = {"id": f"q-{time.monotonic_ns()}", **question}
            original_question = copy.deepcopy(self.question)
            self.main_retracted = []
            self.answer = None
            self.notice = '= Yes    - No    0 Rephrase'
            self.log("question", **self.question)
            self.cv.notify_all()
            while self.answer is None and not self.stopped.is_set():
                self.cv.wait(0.2)
            answer = self.answer or {"answer": "cancelled"}
            if answer["answer"] not in {"cancelled", "history_revision"}:
                self.answer_history.append(copy.deepcopy(answer))
                key = {"accept": "=", "reject": "-", "unpack": "0", "text": "text"}[answer["answer"]]
                identity = self._record("main", self.question["question"], key,
                                        answer.get("text", key), 'Main', self.question)
                self.main_history.append({"question": copy.deepcopy(self.question),
                                          "response": copy.deepcopy(answer), "record_id": identity})
            if self.main_retracted and answer["answer"] not in {"cancelled", "history_revision"}:
                answer = {"answer": "revise", "new_answer": answer["answer"],
                          "question": copy.deepcopy(self.question),
                          "retracted": copy.deepcopy(self.main_retracted),
                          "valid_answers": self.active_answers(),
                          "cancelled_question": original_question,
                          **({"text": answer["text"]} if "text" in answer else {})}
            self.log("answer", **answer)
            self.main_retracted = []
            self.mode = "running"
            self.question = None
            self.answer = None
            # A new answer changes what is still worth asking. Keep unsent answers,
            # but discard speculative questions based on the old understanding.
            self._reset_tree()
            return answer

    def main_planning_snapshot(self) -> dict | None:
        with self.cv:
            while not self.main_plan_requested and not self.stopped.is_set():
                self.cv.wait(0.2)
            if self.stopped.is_set():
                return None
            self.main_plan_requested = False
            return {"generation": self.main_generation, "task": self.task,
                    "root": copy.deepcopy(self.main_root), "answered": self.active_answers(),
                    "depth": self.main_lookahead,
                    "paths": ["".join(bits) for depth in range(self.main_lookahead)
                              for bits in itertools.product("=-", repeat=depth)]}

    def install_main_plan(self, snapshot: dict, nodes: dict[str, MainNode]) -> bool:
        with self.cv:
            if snapshot["generation"] != self.main_generation:
                return False
            self.main_nodes = nodes
            self.main_plan_ready = True
            self.log("main_plan_ready", depth=snapshot["depth"], count=len(nodes))
            self.cv.notify_all()
            return True

    def drain(self) -> list[dict]:
        with self.cv:
            items, self.inbox = self.inbox, []
            self.interrupt.clear()
            return items

    def wait_for_work(self) -> bool:
        with self.cv:
            while not self.inbox and not self.stopped.is_set():
                self.cv.wait(0.2)
            if self.stopped.is_set():
                return False
            self.mode = "running"
            return True

    def planning_snapshot(self) -> dict | None:
        with self.cv:
            while (not self.plan_requested or self.mode == "question") and not self.stopped.is_set():
                self.cv.wait(0.2)
            if self.stopped.is_set():
                return None
            self.plan_requested = False
            self.planning = True
            self.plan_state = 'Thinking of what to ask next'
            prefix = self.cursor
            paths = [prefix + "".join(bits) for length in range(self.lookahead)
                     for bits in itertools.product("=-", repeat=length)]
            existing = {path: asdict(self.nodes[path]) for path in paths if path in self.nodes}
            return {"generation": self.generation, "prefix": prefix, "paths": paths,
                    "existing": existing, "task": self.task[:2500],
                    "focus": asdict(self.side_focus), "events": copy.deepcopy(self.pending[-16:]),
                    "answered": copy.deepcopy(self.answer_history[-24:]),
                    "answers": self.active_answers(),
                    "recent_output": "\n".join(self.lines[-12:])[-3000:]}

    def install_plan(self, snapshot: dict, nodes: dict[str, Node]) -> bool:
        with self.cv:
            self.planning = False
            if snapshot["generation"] != self.generation or not self.cursor.startswith(snapshot["prefix"]):
                return False
            for path, node in nodes.items():
                if path in self.nodes and self.nodes[path] != node:
                    raise ValueError('The planner tried to change a question already displayed.')
            self.nodes.update(nodes)
            self.plan_state = ""
            self.log("plan_ready", generation=self.generation, prefix=snapshot["prefix"], count=len(nodes),
                     nodes={path: asdict(node) for path, node in nodes.items()})
            self._maybe_plan()
            self.cv.notify_all()
            return True

    def snapshot(self) -> View:
        with self.cv:
            return View(self.mode, self.side_focus, copy.deepcopy(self.question),
                        copy.deepcopy(self.pending), self.nodes.get(self.cursor),
                        self.plan_state, self.notice, self.lines.copy(), self.cursor,
                        self.requests, self.usage.copy(), copy.deepcopy(self.records),
                        list(self.outstanding), self.artifacts.copy(), self.auto_submit)

    def stop(self) -> None:
        with self.cv:
            self.stopped.set()
            self.interrupt.set()
            self.cv.notify_all()
