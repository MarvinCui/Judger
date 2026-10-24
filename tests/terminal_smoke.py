"""Exercise the real curses application through a pseudo-terminal, offline."""
from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time
from pathlib import Path


def main():
    app = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="trigger-tty-") as directory:
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["TERM"] = "xterm-256color"
            os.chdir(app)
            os.execv(sys.executable, [sys.executable, "main.py", "--demo", "--no-log",
                                     "--workspace", directory, "--task", 'Try three-key interaction'])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 34, 112, 0, 0))
        output = bytearray()

        def wait_for(text, timeout=20):
            start = len(output)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if text in output[start:].decode("utf-8", errors="replace"):
                    return
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    try:
                        output.extend(os.read(fd, 65536))
                    except OSError:
                        break
            print("Terminal check did not find:", text, flush=True)
            print(output.decode("utf-8", errors="replace")[-2500:], flush=True)
            raise AssertionError("Terminal did not show: " + text)

        reaped = False
        try:
            wait_for('Would you like to write something')
            os.write(fd, b"0")
            wait_for('a short article')
            os.write(fd, b"b")
            wait_for('something')
            os.write(fd, b"=")
            wait_for('Is it for other people')
            os.write(fd, b"=")
            wait_for('Will you post it online')
            os.write(fd, b"\x7f")
            wait_for('Is it for other people')
            # No Enter between keys: these must be immediate, mode-aware triggers.
            os.write(fd, b"=-=0")
            wait_for('I will use them')
            os.write(fd, b"v")
            wait_for('Question history')
            os.write(fd, b"e")
            wait_for('Rephrase')
            os.write(fd, b"-")
            wait_for('Correction')
            wait_for('Article saved', timeout=20)
            if not list(Path(directory).glob("*/demo-article.md")):
                raise AssertionError("The terminal demo did not save its artifact")
            os.write(fd, b"q")
            end = time.monotonic() + 5
            while time.monotonic() < end:
                result, status = os.waitpid(pid, os.WNOHANG)
                if result:
                    reaped = True
                    if os.waitstatus_to_exitcode(status) != 0:
                        raise AssertionError("Terminal application returned nonzero")
                    break
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        output.extend(os.read(fd, 65536))
                    except OSError:
                        pass
            if not reaped:
                raise AssertionError("Terminal application did not exit")
            print("PASS: main/side questions, back and Backspace, history edit, automatic answers, separate project folder, q exit")
        finally:
            if not reaped:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
            os.close(fd)


if __name__ == "__main__":
    main()
