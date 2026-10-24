from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .api import APIError, DeepSeek, Interrupted
from .config import Config, redact
from .questions import validate_question
from .state import Focus, MainNode, Node, State
from .tools import Workspace, tool_schemas, function, S

PROMPTS = Path(__file__).parent / "prompts"


def validate_arguments(schema: dict, arguments: dict) -> None:
    if not isinstance(arguments, dict):
        raise ValueError('Tool arguments must be a JSON object.')
    parameters = schema["function"]["parameters"]
    missing = set(parameters["required"]) - arguments.keys()
    if missing:
        raise ValueError('Missing arguments: ' + ", ".join(sorted(missing)))
    extra = arguments.keys() - parameters["properties"].keys()
    if extra:
        raise ValueError('Unknown arguments: ' + ", ".join(sorted(extra)))
    types = {"string": str, "integer": int, "array": list}
    for key, value in arguments.items():
        kind = parameters["properties"][key]["type"]
        if not isinstance(value, types[kind]) or (kind == "integer" and isinstance(value, bool)):
            raise ValueError(f'Argument {key} must be {kind}.')
        if kind == "array" and not all(isinstance(item, str) for item in value):
            raise ValueError(f'Argument {key} must contain strings only.')


class Agent:
    def __init__(self, config: Config, state: State, session_dir: Path | None = None,
                 api: DeepSeek | None = None):
        self.config, self.state, self.session_dir = config, state, session_dir
        self.api = api or DeepSeek(config)
        self.workspace = Workspace(config, self.api, state.stopped)
        self.schemas = tool_schemas(config.allow_shell)
        self.codex = None
        if config.executor == "codex":
            from .codex_bridge import CodexBridge
            self.codex = CodexBridge(config, state)
            self.schemas = [s for s in self.schemas if s["function"]["name"] not in
                            {"write_file", "edit_file", "run_command"}]
            self.schemas.append(function("execute_codex", 'Once intent is clear, automatically delegate an actionable brief to Codex. It writes real files and runs checks while receiving side answers and live-control updates. Call this tool again for later changes in the same Codex conversation.',
                                         {"prompt": S}, ["prompt"]))
        if getattr(state, "execution", None):
            self.schemas.append(function("offer_execution", 'Offer a split control view inside Terminal when the task is ready for concrete adjustments. All questions and actions stay in Terminal; the browser only previews the project webpage. Never put chat panels or software controls in that webpage. Press C in Terminal to enter, brackets to switch categories, and - or = to adjust. The HTML file may not exist yet.',
                                         {"entry": S, "title": S}, ["entry", "title"]))
        self.schema_by_name = {t["function"]["name"]: t for t in self.schemas}
        context = f'Absolute project path: {config.workspace.resolve()}\nLocal command execution: {config.allow_shell}\n'
        if self.workspace.instructions:
            context += 'Project AGENTS.md:\n' + self.workspace.instructions
        if self.codex:
            context += ('\nCodex execution is selected. Discover intent with short questions. Once there is enough information, call execute_codex with an actionable brief containing the goal, confirmed constraints, deliverables, and validation. Do not ask for redundant permission to begin. Delegate all file changes and command execution to Codex. Side questions and live controls remain active and send updates to the same Codex conversation. After the tool returns, finish based on its real results; call execute_codex again for later changes. For webpages, prefer index.html and call offer_execution first.')
        self.messages = [{"role": "system", "content": (PROMPTS / "system.md").read_text(encoding="utf-8")},
                         {"role": "user", "content": context},
                         {"role": "user", "content": state.task}]

    def checkpoint(self) -> None:
        if self.session_dir is None:
            return
        self.session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.session_dir / "history.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(redact(json.dumps(self.messages, ensure_ascii=False, indent=2),
                                    self.config.api_key), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target)

    def apply_inbox(self) -> bool:
        items = self.state.drain()
        for item in items:
            if item["kind"] == "feedback":
                content = ('The user submitted side answers during execution. These are ordered, real choices bound to the question and target visible at each keypress. Adjust the plan before continuing. Do not cancel opposing keys arithmetically or override other confirmed requirements.\n'
                           + json.dumps({"id": item["id"], "events": item["events"]}, ensure_ascii=False)
                           + '\nAfter actually completing this feedback, include its id in finish.completed_feedback.')
                self.state.say('Got it. I will use those answers.', "AI: ")
            elif item["kind"] == "revision":
                content = ('The user withdrew or changed an earlier answer. Later answers have been withdrawn and no longer apply. Reinterpret the task using the complete valid answer set below. Read and revise existing files if necessary; previously executed operations do not roll back automatically.\n'
                           + json.dumps(item, ensure_ascii=False)
                           + '\nAfter handling this correction, include its id in finish.completed_feedback.')
                self.state.say('Correction received. I will use the updated answers.', "AI: ")
            elif item["kind"] == "design":
                content = ('The user adjusted the live controls. Apply these settings to the actual webpage and preserve trigger-style.css.\n'
                           + json.dumps(item, ensure_ascii=False)
                           + '\nAfter handling this item, include its id in finish.completed_feedback.')
            else:
                content = item["text"]
            self.messages.append({"role": "user", "content": content})
        if items:
            self.checkpoint()
        return bool(items)

    def dispatch(self, name: str, arguments: dict) -> dict:
        if name not in self.schema_by_name:
            raise ValueError('That tool is not available in this session.')
        validate_arguments(self.schema_by_name[name], arguments)
        if name == "execute_codex":
            return self.codex.run(arguments["prompt"])
        if name == "offer_execution":
            self.state.execution.offer(**arguments)
            return {"offered": True, "key": "C", "entry": arguments["entry"]}
        if name == "set_focus":
            if any(not v.strip() or len(v) > 240 for v in arguments.values()):
                raise ValueError('Focus fields must be nonempty and at most 240 characters each.')
            self.state.set_focus(Focus(**arguments))
            return {"focus_set": True, **arguments}
        if name == "ask_user":
            if any(not v.strip() for v in arguments.values()):
                raise ValueError('The question and both outcomes must be nonempty.')
            arguments = {**arguments, "question": validate_question(arguments["question"])}
            return self.state.ask(arguments)
        if name == "finish":
            execution = getattr(self.state, "execution", None)
            if execution:
                execution.flush()
            artifacts = []
            for item in arguments["artifacts"]:
                path = self.workspace.path(item)
                if not path.is_file():
                    raise ValueError(f'Deliverable does not exist: {item}')
                artifacts.append(str(path))
            with self.state.cv:
                if self.state.pending:
                    self.state.send_pending(automatic=True)
                if self.state.inbox:
                    return {"deferred": True, "reason": 'New feedback just arrived. Address it before finishing.'}
                completed = set(arguments.get("completed_feedback", []))
                missing = set(self.state.outstanding) - completed
                if missing:
                    return {"deferred": True, "unhandled_feedback": sorted(missing),
                            "reason": 'Complete these outstanding requests and corrections, then acknowledge them in completed_feedback.'}
                self.state.outstanding.clear()
                self.state.artifacts = artifacts
                self.state.mode = "done"
                self.state.notice = 'Done. Files are listed above. Keep answering to make changes.'
            self.state.say(arguments["summary"], 'Done: ')
            for path in artifacts:
                self.state.say(path, 'File: ')
            return {"finished": True, "artifacts": artifacts}
        label = {"list_files": 'Listing files', "read_file": 'Reading', "search_files": 'Searching',
                 "write_file": 'Saving', "edit_file": 'Editing', "run_command": 'Running command',
                 "web_search": 'Searching the web', "fetch_url": 'Reading webpage'}[name]
        detail = arguments.get("path") or arguments.get("query") or arguments.get("url") or arguments.get("command", "")
        if name in {"write_file", "edit_file"}:
            path = self.workspace.path(arguments["path"], writing=True)
            target = str(path.relative_to(self.workspace.root))
            # Models occasionally forget to move the focus between artifacts.
            # Re-anchor automatically so cached questions cannot target the last file.
            if target not in self.state.focus.target and str(path) not in self.state.focus.target:
                if path.suffix.lower() in {".md", ".txt", ".rst"}:
                    focus = Focus(target, 'How to write this article', 'Yes', 'No')
                else:
                    focus = Focus(target, 'What this app should do', 'Yes', 'No')
                self.state.set_focus(focus)
        self.state.say(f"{label}: {detail[:140]}")
        result = self.workspace.execute(name, arguments)
        execution = getattr(self.state, "execution", None)
        if execution and name in {"write_file", "edit_file"} and str(arguments.get("path", "")).lower().endswith((".html", ".htm")):
            execution.offer(arguments["path"])
        if name in {"write_file", "edit_file"}:
            result["active_focus"] = asdict(self.state.focus)
        if name == "run_command":
            self.state.say('That took too long, so it was stopped.' if result["timed_out"] else
                           ('Run succeeded.' if result["exit_code"] == 0 else f"Run failed ({result['exit_code']})."))
            if result["output"]:
                self.state.say(result["output"][-1600:])
        self.state.log("tool", name=name, arguments=arguments, result=result)
        return result

    def run(self) -> None:
        self.state.say(self.state.task, 'You: ')
        self.checkpoint()
        steps = 0
        while not self.state.stopped.is_set():
            try:
                if self.apply_inbox():
                    steps = 0
                if steps >= self.config.max_steps:
                    raise APIError(f'This round reached its limit of {self.config.max_steps} requests. Press i and type "continue" to start another round.')
                steps += 1
                self.state.count_request()
                self.state.say("", "AI: ")
                message, usage = self.api.stream(
                    self.messages, self.schemas, on_text=self.state.chunk,
                    cancelled=lambda: self.state.interrupt.is_set() or self.state.stopped.is_set())
                self.state.add_usage(usage)
                self.messages.append(message)
                self.state.log("assistant", message=message)
                calls = message.get("tool_calls") or []
                finished = False
                for call in calls:
                    name = call["function"]["name"]
                    try:
                        if len(calls) > 1:
                            raise ValueError('Call one tool at a time. None of this batch was executed; split it into separate steps.')
                        if self.state.stopped.is_set():
                            result = {"cancelled": True}
                        elif self.state.interrupt.is_set():
                            result = {"skipped": True, "reason": 'The user submitted new feedback. The old plan was not executed; plan again.'}
                        else:
                            arguments = json.loads(call["function"]["arguments"])
                            result = self.dispatch(name, arguments)
                        finished = bool(result.get("finished"))
                    except (ValueError, TypeError, KeyError, OSError, APIError) as error:
                        result = {"error": redact(str(error), self.config.api_key)}
                        self.state.say(result["error"], 'Tool notice: ')
                    self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                          "content": json.dumps(result, ensure_ascii=False)})
                self.checkpoint()
                if finished:
                    if not self.state.wait_for_work():
                        break
                    steps = 0
                elif not calls:
                    self.messages.append({"role": "user", "content":
                                          'Continue doing the actual work. Call ask_user when a user decision is needed, and finish when complete.'})
            except Interrupted:
                if self.state.stopped.is_set():
                    break
                self.state.say('Got it. I will adjust.', "AI: ")
            except Exception as error:
                # Keep the UI alive and history intact; retries are explicit user input.
                text = redact(str(error), self.config.api_key)
                self.state.say(text, 'Error: ')
                self.state.log("error", message=text)
                with self.state.cv:
                    self.state.mode = "error"
                    self.state.notice = 'Paused. Press i to explain, or type "continue" to retry.'
                if not self.state.wait_for_work():
                    break
                steps = 0


def parse_plan(content: str, snapshot: dict) -> dict[str, Node]:
    data = json.loads(content)
    raw = data.get("nodes")
    if not isinstance(raw, dict) or set(raw) != set(snapshot["paths"]):
        raise ValueError('The generated nodes do not match the requested paths.')
    nodes = {}
    for path, values in raw.items():
        if not isinstance(values, dict) or set(values) != {"question", "target", "positive_effect", "negative_effect"}:
            raise ValueError('Invalid question node structure.')
        if not all(isinstance(v, str) and 0 < len(v) <= 240 for v in values.values()):
            raise ValueError('Question nodes need short, nonempty text.')
        validate_question(values["question"])
        if path in snapshot["existing"] and values != snapshot["existing"][path]:
            raise ValueError('Cached questions must not change.')
        nodes[path] = Node(**values)
    return nodes


def planner_loop(config: Config, state: State, api: DeepSeek | None = None) -> None:
    api = api or DeepSeek(config)
    prompt = (PROMPTS / "planner.md").read_text(encoding="utf-8")
    while not state.stopped.is_set():
        snapshot = state.planning_snapshot()
        if snapshot is None:
            return
        try:
            state.count_request()
            message, usage = api.stream(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}],
                json_mode=True, max_tokens=8192,
                cancelled=lambda: state.stopped.is_set() or snapshot["generation"] != state.generation)
            state.add_usage(usage)
            state.install_plan(snapshot, parse_plan(message["content"] or "", snapshot))
        except Interrupted:
            with state.cv:
                state.planning = False
        except Exception as error:
            with state.cv:
                state.planning = False
                state.plan_state = 'Not ready for the next question. Press i to tell me directly.'
                state.plan_retry_at = time.monotonic() + 3
                state.log("planner_error", message=redact(str(error), config.api_key))
                # No automatic retry storm. Another keypress/focus change can request it.


def parse_main_plan(content: str, snapshot: dict) -> dict[str, MainNode]:
    raw = json.loads(content).get("nodes")
    if not isinstance(raw, dict) or set(raw) != set(snapshot["paths"]):
        raise ValueError('The main question tree must cover every path across seven levels.')
    result = {}
    for path, item in raw.items():
        if not isinstance(item, list) or len(item) != 3 or item[1] not in {"ask", "work"} or item[2] not in {"ask", "work"}:
            raise ValueError('Each node needs a short question and ask/work transitions.')
        result[path] = MainNode(validate_question(item[0]), item[1], item[2])
    if result[""].question != snapshot["root"]["question"]:
        raise ValueError('The root question must not change.')
    return result


def main_planner_loop(config: Config, state: State, api: DeepSeek | None = None) -> None:
    api = api or DeepSeek(config)
    prompt = (PROMPTS / "main_planner.md").read_text(encoding="utf-8")
    while not state.stopped.is_set():
        snapshot = state.main_planning_snapshot()
        if snapshot is None:
            return
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}]
        for attempt in range(2):
            try:
                state.count_request()
                message, usage = api.stream(messages, json_mode=True, max_tokens=20000,
                    cancelled=lambda: state.stopped.is_set() or snapshot["generation"] != state.main_generation)
                state.add_usage(usage)
                state.install_main_plan(snapshot, parse_main_plan(message["content"] or "", snapshot))
                break
            except Interrupted:
                break
            except Exception as error:
                if attempt == 0 and isinstance(error, (ValueError, TypeError)):
                    messages.append({"role": "user", "content": 'Fix the structure and return complete JSON again: ' + str(error)})
                    continue
                with state.cv:
                    if snapshot["generation"] == state.main_generation:
                        state.main_plan_error = str(error)
                        state.log("main_plan_error", message=redact(str(error), config.api_key))
                        state.cv.notify_all()
                break


def demo_main_planner(state: State) -> None:
    while not state.stopped.is_set():
        snapshot = state.main_planning_snapshot()
        if snapshot is None:
            return
        source = {**snapshot, "existing": {}, "focus": {"target": 'Demo'}}
        tree = demo_tree(source)
        nodes = {path: MainNode(node.question, "ask", "ask") for path, node in tree.items()}
        nodes[""] = MainNode(snapshot["root"]["question"], "work", "work")
        state.install_main_plan(snapshot, nodes)


def demo_tree(snapshot: dict) -> dict[str, Node]:
    questions = {
        "": 'Is it for other people?', "=": 'Will you post it online?', "-": 'Is it just for you?',
        "==": 'Is it for social media?', "=-": 'Is it for someone you know?',
        "-=": 'Is it for taking notes?', "--": 'Is it for work?',
        "===": 'Should it sound more relaxed?', "==-": 'Are the readers beginners?',
        "=-=": 'Is it for your friends?', "=--": 'Is it for a teacher?',
        "-==": 'Will you come back to it later?', "-=-": 'Is it about your own experience?',
        "--=": 'Is it for your coworkers?', "---": 'Is it for studying?',
    }
    later = ['Should we start with an example?', 'Should it sound conversational?', 'Want a short version first?',
             'Does it need step-by-step instructions?', 'Is there a word limit?']
    nodes = {}
    for path in snapshot["paths"]:
        if path in snapshot["existing"]:
            nodes[path] = Node(**snapshot["existing"][path])
            continue
        question = questions.get(path, later[(len(path) - 4) % len(later)])
        nodes[path] = Node(question, snapshot["focus"]["target"],
                           'Yes: ' + question[:-1], 'No: ' + question[:-1])
    return nodes


def demo_planner(state: State) -> None:
    while not state.stopped.is_set():
        snapshot = state.planning_snapshot()
        if snapshot is None:
            return
        state.install_plan(snapshot, demo_tree(snapshot))


def demo_agent(config: Config, state: State) -> None:
    """A labelled offline walkthrough, never presented as model output."""
    tools = Workspace(config, stopped=state.stopped)
    state.say('This is an offline example. No AI requests are made.')
    state.say(state.task, 'You: ')
    if any(word in state.task.casefold() for word in ('webpage', 'website', 'web demo', 'preview')) and getattr(state, "execution", None):
        source = (Path(__file__).parent.parent / "examples" / "web-demo.html").read_text(encoding="utf-8")
        tools.execute("write_file", {"path": "index.html", "content": source})
        state.execution.offer("index.html", 'Make it feel right')
        state.say('Webpage demo ready. Press C to preview, then adjust in Terminal.')
        with state.cv:
            state.mode = "done"
            state.artifacts = [str(config.workspace / "index.html")]
        while state.wait_for_work():
            for item in state.drain():
                with state.cv:
                    state.outstanding.pop(item.get("id"), None)
                state.say('Updated. The preview now reflects the change.', 'Example: ')
            with state.cv:
                state.mode = "done"
        return
    answer = state.ask({"question": 'Would you like to write something?',
                        "on_accept": 'Write a short English article and save it as demo-article.md.',
                        "on_reject": 'Keep the goal open and try another demo task.'})
    code_demo = False
    while answer.get("answer") in {"unpack", "reject"} and not state.stopped.is_set():
        if answer["answer"] == "reject":
            code_demo = not code_demo
        question = 'Would you like to build a small app?' if code_demo else 'Would you like to write a short article?'
        answer = state.ask({"question": question, "on_accept": 'Create the matching demo file.',
                            "on_reject": 'Explore another task, or press i to explain what you want.'})
        if answer.get("answer") == "revise":
            code_demo = 'small app' in answer["question"]["question"]
            answer = {**answer, "answer": answer["new_answer"]}
    if state.stopped.is_set():
        return
    if code_demo:
        state.set_focus(Focus('Demo counter', 'How much explanation to include', 'Add usage instructions', 'Keep the instructions minimal'))
        source = ('"""Offline demo: type a key and press Enter; q exits."""\n'
                  'counts = {"=": 0, "-": 0, "0": 0}\n'
                  'while True:\n'
                  '    key = input("= / - / 0 (q exits): ").strip()\n'
                  '    if key == "q":\n'
                  '        break\n'
                  '    if key in counts:\n'
                  '        counts[key] += 1\n'
                  '        print(counts)\n')
        tools.execute("write_file", {"path": "demo-counter.py", "content": source})
        state.say('Demo program saved: demo-counter.py')
        with state.cv:
            state.artifacts = [str(config.workspace / "demo-counter.py")]
            state.mode = "done"
            state.notice = 'Saved. Keep answering to continue, or press q to quit.'
        while state.wait_for_work():
            for item in state.drain():
                state.say('Got it.', 'Example: ')
                with state.cv:
                    state.outstanding.pop(item.get("id"), None)
            with state.cv:
                state.mode = "done"
        return
    state.set_focus(Focus('Demo article', 'What the article is for', 'Yes', 'No'))
    paragraphs = ['# Let AI discover what you want to do\n', 'A blank prompt is not the only starting point. Let AI ask.\n',
                  'Answer yes or no: press = for yes, or - for no.\n',
                  'If a question is unclear, press 0 to rephrase it.\n',
                  'You can keep answering while work continues. Press 0 to send.\n']
    content = ""
    for paragraph in paragraphs:
        if state.stopped.is_set():
            return
        state.say(paragraph, 'Demo: ')
        content += paragraph + "\n"
        for _ in range(25):
            if state.stopped.wait(0.1):
                return
            for item in state.drain():
                if item["kind"] == "feedback":
                    content += '\n<!-- Ordered feedback received by the demo: ' + "; ".join(e["effect"] for e in item["events"]) + " -->\n"
                    state.say('Answers received. I will use them.', 'Example: ')
                elif item["kind"] == "revision":
                    content += '\n<!-- The offline demo received a correction to earlier answers. -->\n'
                    state.say('Correction received.', 'Example: ')
                with state.cv:
                    state.outstanding.pop(item.get("id"), None)
    with state.cv:
        if state.pending:
            state.send_pending(automatic=True)
        for item in state.drain():
            content += '\n<!-- The offline demo recorded the final answers. -->\n'
            state.outstanding.pop(item.get("id"), None)
    result = tools.execute("write_file", {"path": "demo-article.md", "content": content})
    state.say('Article saved: ' + result["written"])
    with state.cv:
        state.artifacts = [str(config.workspace / "demo-article.md")]
        state.mode = "done"
        state.notice = 'Saved. Keep answering to continue, or press q to quit.'
    while state.wait_for_work():
        for item in state.drain():
            state.say('Got it.', 'Example: ')
            with state.cv:
                state.outstanding.pop(item.get("id"), None)
        with state.cv:
            state.mode = "done"
