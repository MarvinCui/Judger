# Development

Judger runs directly from source. Python 3.11+ and a macOS/Linux terminal are enough for the offline demo and unit tests. No Python packages need to be installed for the application itself.

## Checks without credentials

```sh
python3 -m unittest discover -s tests -v
python3 tests/terminal_smoke.py
python3 tests/terminal_execution_smoke.py
```

The terminal tests use a pseudo-terminal and scripted offline tasks. They create temporary project directories and do not contact model providers. The live-control terminal test suppresses opening a browser while exercising the real curses UI.

## Real services

```sh
python3 main.py --setup
python3 tests/live_smoke.py
python3 tests/live_revision.py
python3 tests/live_codex.py
python3 tests/live_codex.py --agent
```

These checks make real model requests and can consume account usage. Codex checks additionally require an installed, signed-in Codex CLI. The default application model is configurable with --model; the Codex integration reads the CLI model catalog rather than hard-coding an execution model.

## Optional browser checks

The browser test uses Node.js, Playwright, and a local Chrome/Chromium installation. Provide executable/module locations if they are not available through the bundled local Codex runtime:

```sh
export JUDGER_NODE="/path/to/node"
export JUDGER_PLAYWRIGHT="/path/to/node_modules/playwright"
export JUDGER_CHROME="/path/to/chrome"
python3 tests/preview_smoke.py
```

These variables configure only the optional test tools. They are not required by the application. Tests verify that Terminal input changes computed page styles, history edits restore styles, and file changes reload the preview. The browser receives no Judger software controls.

## Layout

The entrypoint is main.py; the internal package is trigger_agent. Prompt instructions live in trigger_agent/prompts. Runtime files deliberately retain their original .trigger-agent and trigger-style.css paths so existing project metadata and previews remain compatible.

English UI questions are limited to one yes/no sentence, 12 words, and 72 characters. Keep language concrete. A no eliminates one hypothesis without proving a competing one.

## Local artifacts

Keep .local, workbench, .trigger-agent, credentials, and generated outputs out of commits. Unit tests do not need a key. Do not embed a real key in fixtures or documentation. --setup stores the application's local key with restrictive file permissions.

The EEG section of the README is a research roadmap, not an implemented acquisition or classification API.
