"""Locate optional browser-test dependencies without affecting the application."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def browser_runtime() -> tuple[str, str]:
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('JUDGER_NODE') or (str(bundled / 'bin/node') if (bundled / 'bin/node').is_file() else shutil.which('node'))
    playwright = os.environ.get('JUDGER_PLAYWRIGHT') or str(bundled / 'node_modules/playwright')
    if not node or not Path(playwright).is_dir():
        raise SystemExit('Optional browser tests need Node and Playwright. Set JUDGER_NODE and JUDGER_PLAYWRIGHT; see docs/DEVELOPMENT.md.')
    return node, playwright
