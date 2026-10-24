"""Render the README and export the editable SVG banner as a shareable PNG."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from browser_runtime import browser_runtime


def main():
    root = Path(__file__).resolve().parents[1]
    node, playwright = browser_runtime()
    marked = Path(os.environ.get('JUDGER_MARKED', str(Path(playwright).parent / 'marked')))
    if not marked.is_dir():
        raise SystemExit('Set JUDGER_MARKED to a local marked package directory for README rendering.')
    output = root / '.local' / 'qa'
    output.mkdir(parents=True, exist_ok=True)
    allowed = {'/assets/judger-banner.svg', '/assets/judger-banner.png', '/assets/judger-status.svg'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'<!doctype html><title>Judger README preview</title>')
                return
            path = root / self.path.lstrip('/')
            if self.path not in allowed or not path.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml' if path.suffix == '.svg' else 'image/png')
            self.end_headers()
            self.wfile.write(path.read_bytes())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        subprocess.run([node, str(root/'tests/readme_browser.cjs'), str(root), str(output),
                        playwright, str(marked), f'http://127.0.0.1:{server.server_port}/'], check=True, timeout=75)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
