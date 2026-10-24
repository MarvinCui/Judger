from __future__ import annotations

import argparse
import curses
import getpass
import json
import locale
import sys
import threading
from datetime import datetime
from dataclasses import replace
from pathlib import Path

from .api import APIError, DeepSeek
from .config import APP_ROOT, KEY_FILE, LOCAL_DIR, Config, Journal, load_key, redact, save_key
from .engine import Agent, demo_agent, demo_main_planner, demo_planner, main_planner_loop, planner_loop
from .projects import create_project
from .execution import Execution
from .state import State
from .ui import Terminal


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Judger: answer = / - / 0. Let AI discover your intent.')
    result.add_argument("--task", help='Initial task; enter it after launch if omitted')
    result.add_argument("--workspace", type=Path, default=APP_ROOT / "workbench", help='Parent directory for new projects; default: workbench')
    result.add_argument("--project", type=Path, help='Continue in an existing project folder')
    result.add_argument("--model", default="deepseek-flash", help='DeepSeek model name')
    result.add_argument("--base-url", default="https://api.deepseek.com", help='Compatible API endpoint')
    result.add_argument("--allow-shell", action="store_true", help='Allow local commands; this is not an operating-system sandbox')
    result.add_argument("--codex", action="store_true", help='Let Codex execute while DeepSeek keeps asking about your intent')
    result.add_argument("--codex-bin", default="codex", help='Codex CLI executable; default: codex')
    result.add_argument("--preview-file", help='HTML preview entry within the project, such as index.html')
    result.add_argument("--lookahead", type=int, choices=range(0, 6), default=4, metavar="0..5",
                        help='Side-question lookahead depth; default: 4; 0 disables it')
    result.add_argument("--max-steps", type=int, default=60, help='Maximum model requests per round; default: 60')
    result.add_argument("--auto-submit", type=int, choices=(1, 2), default=2, help='Send side answers automatically after 1 or 2 choices; default: 2')
    result.add_argument("--demo", action="store_true", help='Offline scripted demo; no API usage')
    result.add_argument("--setup", action="store_true", help='Save or update the API key interactively, then exit')
    result.add_argument("--check", action="store_true", help='Check the API key and available models, then exit')
    result.add_argument("--no-log", action="store_true", help='Do not save conversation or key-event logs; file backups remain')
    return result


def main(argv: list[str] | None = None) -> None:
    options = parser().parse_args(argv)
    if options.setup:
        try:
            save_key(getpass.getpass('DeepSeek API key (hidden): '))
        except (ValueError, EOFError, KeyboardInterrupt) as error:
            raise SystemExit(str(error) or 'Cancelled.') from None
        print('Private local configuration saved: ' + str(KEY_FILE))
        return
    key = load_key()
    if not key and not options.demo:
        raise SystemExit('No API key configured. Run python3 main.py --setup, or try --demo offline.')
    if options.max_steps < 1:
        raise SystemExit('--max-steps must be greater than zero.')
    config = Config(workspace=options.workspace.expanduser().resolve(), model=options.model,
                    base_url=options.base_url, api_key=key, allow_shell=options.allow_shell,
                    lookahead=options.lookahead, max_steps=options.max_steps, demo=options.demo,
                    auto_submit=options.auto_submit)
    if options.codex:
        import shutil
        if options.demo:
            raise SystemExit('--demo is offline and cannot be combined with --codex.')
        if not shutil.which(options.codex_bin):
            raise SystemExit('Codex CLI was not found. Install it and run codex login first.')
        config = replace(config, executor="codex", codex_bin=options.codex_bin)
    if options.check:
        try:
            models = DeepSeek(config).json_request("/models")
            ids = [item["id"] for item in models.get("data", [])]
            print('Connected. Available models: ' + ", ".join(ids))
            if config.model not in ids:
                print('The selected model is not listed. Choose another with --model.')
        except APIError as error:
            raise SystemExit(redact(str(error), key)) from None
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('The interactive UI needs a real terminal. Use Terminal or iTerm; --check works without one.')
    locale.setlocale(locale.LC_ALL, "")
    task = options.task
    if not task:
        print('Judger / = Yes / - No\n')
        try:
            task = input('Give me a clue, or press Enter and let me ask:\n> ').strip()
        except (EOFError, KeyboardInterrupt):
            return
    if not task:
        task = 'Help me work out what I want to do. Start with the broad direction, then follow my answers.'
    if options.project:
        project = options.project.expanduser().resolve()
        if not project.is_dir():
            raise SystemExit('That project folder does not exist.')
    else:
        project = create_project(config.workspace, task)
    config = replace(config, workspace=project)
    session = LOCAL_DIR / "sessions" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    journal = Journal(None if options.no_log else session / "events.jsonl", key)
    state = State(journal, lookahead=config.lookahead, main_lookahead=config.main_lookahead,
                  auto_submit=config.auto_submit)
    state.task = task
    execution = Execution(config.workspace, state)
    if options.preview_file:
        execution.offer(options.preview_file)
    journal.write({"event": "session", "task": task, "workspace": str(config.workspace),
                   "model": config.model, "lookahead": config.lookahead, "demo": config.demo,
                   "allow_shell": config.allow_shell, "main_lookahead": config.main_lookahead,
                   "auto_submit": config.auto_submit})
    if config.demo:
        target = lambda: demo_agent(config, state)
        planner = lambda: demo_planner(state)
        main_planner = lambda: demo_main_planner(state)
    else:
        agent = Agent(config, state, None if options.no_log else session)
        target = agent.run
        planner = lambda: planner_loop(config, state)
        main_planner = lambda: main_planner_loop(config, state)
    worker = threading.Thread(target=target, name="main-agent", daemon=True)
    background = threading.Thread(target=planner, name="lookahead-planner", daemon=True)
    main_background = threading.Thread(target=main_planner, name="main-lookahead-planner", daemon=True)
    try:
        worker.start()
        main_background.start()
        if config.lookahead:
            background.start()
        curses.wrapper(Terminal(config, state).run)
    except KeyboardInterrupt:
        pass
    except curses.error as error:
        print('Could not start the terminal UI: ' + str(error), file=sys.stderr)
    finally:
        execution.flush()
        state.stop()
        worker.join(timeout=2)
        main_background.join(timeout=0.1)
        if config.lookahead:
            background.join(timeout=0.1)
        execution.close()
        if not config.demo and agent.codex:
            agent.codex.close()
    print('\nSession ended. Project: ' + str(config.workspace))
    if not options.no_log:
        print('Local session log: ' + str(session))


if __name__ == "__main__":
    main()
