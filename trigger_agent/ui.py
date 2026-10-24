from __future__ import annotations

import curses
import time
import unicodedata

from .config import Config, redact
from .state import State


def columns(text: str) -> int:
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


def clean(text: str) -> str:
    # Do not interpret terminal control sequences from model or command output.
    return "".join(c if c >= " " and c != "\x7f" else " " for c in text)


def clip(text: str, width: int) -> str:
    result = ""
    used = 0
    for char in clean(text):
        size = columns(char)
        if used + size > width:
            break
        result += char
        used += size
    return result


def wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return [""]
    text = clean(text.expandtabs(4))
    lines, current, used = [], "", 0
    for char in text:
        size = columns(char)
        if used + size > width:
            lines.append(current)
            current, used = "", 0
        current += char
        used += size
    lines.append(current)
    return lines


class Terminal:
    def __init__(self, config: Config, state: State):
        self.config, self.state = config, state
        self.editing = False
        self.buffer = ""
        self.edit_question: str | None = None
        self.scroll = 0
        self.help = False
        self.history_open = False
        self.history_index = 0
        self.edit_record: int | None = None

    def run(self, screen) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.nodelay(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for number, color in enumerate((curses.COLOR_CYAN, curses.COLOR_GREEN,
                                           curses.COLOR_YELLOW, curses.COLOR_RED), 1):
                curses.init_pair(number, color, -1)
        while not self.state.stopped.is_set():
            self.draw(screen)
            try:
                key = screen.get_wch()
            except curses.error:
                time.sleep(0.04)
                continue
            self.handle_key(key)

    def handle_key(self, key) -> None:
        if key == "\x03":
            self.state.stop()
        elif self.history_open:
            self.history_key(key)
        elif self.editing:
            self.edit_key(key)
        elif key in {"q", "Q"}:
            self.state.stop()
        elif getattr(self.state, "execution", None) and self.state.execution.key(key):
            pass
        elif key in {"=", "-", "0"}:
            self.state.trigger(key)
        elif key in {"b", "B", curses.KEY_BACKSPACE, "\x7f", "\b"}:
            execution = getattr(self.state, "execution", None)
            if execution and execution.enabled and self.state.mode != "question":
                execution.undo()
            else:
                self.state.go_back()
        elif key in {"i", "I"}:
            self.editing = True
            view = self.state.snapshot()
            self.edit_question = (view.question or {}).get("id")
        elif key in {"h", "?"}:
            self.help = not self.help
        elif key in {"v", "V"}:
            self.history_open = True
            self.history_index = max(0, len(self.state.snapshot().records) - 1)
            self.edit_record = None
        elif key in {curses.KEY_PPAGE, curses.KEY_UP}:
            self.scroll += 5
        elif key in {curses.KEY_NPAGE, curses.KEY_DOWN}:
            self.scroll = max(0, self.scroll - 5)
        elif key == "g":
            self.scroll = 0

    def history_key(self, key) -> None:
        records = self.state.snapshot().records
        if key in {"\x1b", "q", "v"}:
            if self.edit_record is not None:
                self.edit_record = None
            else:
                self.history_open = False
            return
        if not records:
            return
        self.history_index = min(self.history_index, len(records) - 1)
        if self.edit_record is not None:
            if key in {"=", "-", "0"}:
                self.state.revise_record(self.edit_record, key)
                self.edit_record = None
                self.history_open = False
            elif key == "r":
                self.state.revise_record(self.edit_record, None)
            return
        if key in {curses.KEY_UP, curses.KEY_PPAGE}:
            self.history_index = max(0, self.history_index - 1)
        elif key in {curses.KEY_DOWN, curses.KEY_NPAGE}:
            self.history_index = min(len(records) - 1, self.history_index + 1)
        elif key in {"\n", "\r", curses.KEY_ENTER, "e"}:
            self.edit_record = records[self.history_index]["id"]
        elif key == "r":
            self.edit_record = records[self.history_index]["id"]
            self.state.revise_record(self.edit_record, None)

    def edit_key(self, key) -> None:
        if key in {"\n", "\r", curses.KEY_ENTER}:
            self.state.submit_text(self.buffer, self.edit_question)
            self.buffer = ""
            self.editing = False
            self.edit_question = None
        elif key == "\x1b":
            self.buffer = ""
            self.editing = False
            self.edit_question = None
        elif key in {curses.KEY_BACKSPACE, "\x7f", "\b"}:
            self.buffer = self.buffer[:-1]
        elif isinstance(key, str) and key.isprintable():
            self.buffer += key

    def draw(self, screen) -> None:
        rows, width = screen.getmaxyx()
        screen.erase()
        view = self.state.snapshot()

        def put_at(row: int, col: int, text: str, color: int = 0, bold: bool = False, limit: int | None = None):
            if not 0 <= row < rows or width < 2:
                return
            attr = curses.color_pair(color) if curses.has_colors() else 0
            if bold:
                attr |= curses.A_BOLD
            try:
                screen.addstr(row, col, clip(redact(text, self.config.api_key),
                                            min(width-col-1, limit if limit is not None else width)), attr)
            except curses.error:
                pass

        def put(row: int, text: str, color: int = 0, bold: bool = False):
            put_at(row, 1, text, color, bold)

        context = ('MAIN QUESTION / Waiting for your answer' if view.mode == "question" else
                   {"running": 'SIDE QUESTIONS / Work continues', "done": 'SIDE QUESTIONS / This round is done',
                    "error": 'SIDE QUESTIONS / Work paused'}[view.mode])
        execution = getattr(self.state, "execution", None)
        concrete = execution and execution.enabled and view.mode != "question"
        if concrete:
            context = 'LIVE CONTROLS / Side adjustments, instant preview'
        if rows < 23 or width < 64:
            put(0, 'Please make the terminal window a little larger.', 3)
            put(1, context, 3 if view.mode == "question" else 1, True)
            put(2, '= Yes    - No    b Previous    q Quit')
            put(3, '0 Rephrase' if view.mode == "question" else '0 Send side answers')
            put(4, view.notice)
            screen.refresh()
            return
        names = {"running": 'Working', "question": 'Waiting for your answer', "done": 'Done', "error": 'Something went wrong'}
        put(0, f"JUDGER   |   {names[view.mode]}"
               + ('   [OFFLINE DEMO]' if self.config.demo else ""), 1, True)
        put(1, ('Files saved in: ' + str(self.config.workspace)) if view.mode == "done" else 'You: ' + self.state.task)
        put(2, "─" * (width - 2), 1)
        bottom = rows - 13
        visible = []
        for line in view.lines:
            visible.extend(wrap(redact(line, self.config.api_key), width - 3))
        self.scroll = min(self.scroll, max(0, len(visible) - (bottom - 3)))
        end = max(0, len(visible) - self.scroll)
        for row, line in enumerate(visible[max(0, end - (bottom - 3)):end], 3):
            put(row, line)
        if concrete and not self.history_open:
            from .execution import DIMENSIONS
            for row in range(3, bottom):
                put(row, " " * (width - 2))
            split = 25 if width >= 90 else 19
            for row in range(3, bottom):
                put_at(row, split, "│", 1)
            put_at(3, 2, 'Categories', 1, True, split-3)
            spec = execution.spec()
            for index, dimension in enumerate(DIMENSIONS):
                selected = execution.selected == index
                row = 5 + index * (2 if bottom >= 16 else 1)
                put_at(row, 2, f"{'›' if selected else ' '} {dimension[1]}",
                       3 if selected else 0, selected, split-3)
            right = split + 3
            right_width = width - right - 2
            put_at(3, right, 'Main / ' + (self.state.executor_status or 'Webpage preview is open'), 1, True)
            available = max(0, bottom - 7)
            recent = []
            for line in view.lines[-10:]:
                recent.extend(wrap(line, right_width))
            for row, line in enumerate(recent[-available:], 5):
                put_at(row, right, line, limit=right_width)
        if self.history_open:
            for row in range(3, bottom):
                put(row, " " * (width - 2))
            if not view.records:
                put(4, 'No answers yet. Press Esc to return.')
            else:
                self.history_index = min(self.history_index, len(view.records) - 1)
                first = max(0, self.history_index - (bottom - 5) // 2)
                for row, record in enumerate(view.records[first:first + bottom - 4], 3):
                    selected = first + row - 3 == self.history_index
                    answer = 'Withdrawn' if record["status"] == "withdrawn" else {"=": 'Yes', "-": 'No', "0": 'Rephrase'}.get(record["key"], 'Text')
                    kind = {"main": 'Main', "side": 'Side', "design": 'Live controls'}[record["kind"]]
                    put(row, f"{'>' if selected else ' '} {record['id']}. [{kind}] {record['question']}  {answer}", 3 if selected else 0)
        put(bottom, "─" * (width - 2), 1)
        put(bottom + 1, context, 3 if view.mode == "question" else 1, True)
        if self.history_open:
            put(bottom + 1, 'Question history / Choose any question', 1, True)
            if self.edit_record is not None:
                record = view.records[self.edit_record - 1]
                put(bottom + 2, record["question"], 3, True)
                put(bottom + 4, '= Yes     - No     0 Rephrase')
                put(bottom + 6, '[r Withdraw]    [Esc Back to list]')
            else:
                put(bottom + 3, 'Up / Down Select    [Enter / e Edit]    [r Withdraw]')
                put(bottom + 5, 'Editing an earlier answer withdraws all later answers.')
                put(bottom + 7, 'Esc Back to conversation')
        elif concrete:
            from .execution import DIMENSIONS
            spec = execution.spec()
            dimension = DIMENSIONS[execution.selected]
            put(bottom + 2, dimension[1] + " · " + dimension[4], 3, True)
            put(bottom + 3, 'All controls stay in Terminal. Chrome only previews your webpage.', 1)
            put(bottom + 5, f"- {dimension[2]}    {round(spec['values'][dimension[0]] * 100)}%    = {dimension[3]}", 2)
            put(bottom + 6, '[ / ] Switch category    0 Send changes    C Back to questions')
            put(bottom + 7, execution.url)
        elif self.help:
            details = ['= Yes, - No. No Enter needed.',
                       'b / Backspace: previous; v: history; e: edit; r: withdraw.',
                       f'Main: 0 rephrases. Side: auto-send after {view.auto_submit or 2} answers; 0 sends now.',
                       'i: add text; Enter: send; Esc: cancel.',
                       'Up / Down: scroll; g: latest; h: close help; q: quit.',
                       'Files saved in: ' + str(self.config.workspace),
                       f"Model: {self.config.model} / Requests: {view.requests} / Reported: {view.usage.get('total_tokens', 0)} token"]
            for offset, line in enumerate(details, 2):
                put(bottom + offset, line)
        elif view.mode == "question":
            question = view.question["question"] if view.question else 'Thinking of the next question.'
            for offset, line in enumerate(wrap(question, width - 3)[:2], 2):
                put(bottom + offset, line, 3, True)
            if view.question:
                put(bottom + 5, '= Yes          - No          0 Rephrase', 2)
            if view.pending:
                put(bottom + 7, f'Keeping {len(view.pending)} earlier answers to send together later.')
        else:
            node = view.node
            if node:
                question = node.question
            elif view.plan_state.startswith('Not ready'):
                question = 'No next question yet. Press = to retry, or i to explain.'
            else:
                question = 'One moment. Next question coming up.' if self.config.lookahead else 'Press i to tell me what to change.'
            for offset, line in enumerate(wrap(question, width - 3)[:2], 2):
                put(bottom + offset, line, 3, True)
            if node:
                put(bottom + 5, '= Yes          - No', 2)
            if view.pending:
                put(bottom + 6, f'Still holding {len(view.pending)} answers    0 Send now', 2)
            put(bottom + 7, f'Auto-send every {view.auto_submit or 2} answers'
                + (f' / Handling {len(view.outstanding)} updates' if view.outstanding else ""), 1)
        put(rows - 4, view.notice, 4 if view.mode == "error" else 2)
        if self.editing:
            # Display the end of a long line without losing multibyte characters.
            tail = self.buffer
            while columns(tail) > width - 12 and tail:
                tail = tail[1:]
            put(rows - 3, 'Add > ' + tail, 3, True)
            put(rows - 2, 'Enter sends / Esc cancels; = - 0 are ordinary text here')
        else:
            put(rows - 2, '[b Previous] [v History]'
                         + (' [C Live controls]' if execution and execution.entry else "")
                         + ' i Add text  h Help  q Quit'
                         + ('    g Latest' if self.scroll else ""))
        screen.refresh()
