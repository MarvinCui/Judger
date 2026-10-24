"""Opt-in: Terminal key handlers control an independent, preview-only Chrome page."""
import json
import select
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trigger_agent.config import APP_ROOT, Config
from trigger_agent.execution import Execution
from trigger_agent.projects import create_project
from trigger_agent.state import State
from trigger_agent.ui import Terminal
from browser_runtime import browser_runtime


def main():
    root = create_project(APP_ROOT / '.local' / 'qa', 'terminal-browser-preview')
    entry = root / 'index.html'
    entry.write_text((APP_ROOT / 'examples' / 'web-demo.html').read_text())
    state = State()
    execution = Execution(root, state)
    execution.offer('index.html', 'Make it feel right')
    terminal = Terminal(Config(root), state)
    node, playwright = browser_runtime()
    screenshot = APP_ROOT / '.local' / 'qa' / 'browser-preview.png'
    process = None
    try:
        url = execution.open(browser=False)
        process = subprocess.Popen([node, str(APP_ROOT / 'tests' / 'preview_browser.cjs'),
                          url, str(screenshot), playwright],
                          text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def wait_for(step):
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if select.select([process.stdout], [], [], .1)[0]:
                    line = process.stdout.readline()
                    if not line:
                        raise AssertionError('Browser closed early')
                    result = json.loads(line)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    assert result['step'] == step
                    return
            raise TimeoutError(step)

        def send(step):
            process.stdin.write(json.dumps({'step':step})+'\n')
            process.stdin.flush()
            wait_for('done' if step == 'file' else step)

        wait_for('ready')
        terminal.handle_key('=')
        send('color')
        terminal.handle_key(']')
        terminal.handle_key('=')
        send('bold')
        terminal.handle_key('b')
        send('undo')
        terminal.handle_key('v')
        terminal.history_index = 0
        terminal.handle_key('e')
        terminal.handle_key('-')
        send('revise')
        page = entry.read_text()
        entry.write_text(page.replace('Make everyday life<br>feel like yours.','A Page Changed from Terminal'))
        send('file')
        process.wait(timeout=5)
        assert process.returncode == 0
        assert state.records[0]['key'] == '-'
        assert execution.values['color'] == .4
        print('Screenshot:', screenshot)
    finally:
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()
        state.stop()
        execution.close()


if __name__ == '__main__':
    main()
