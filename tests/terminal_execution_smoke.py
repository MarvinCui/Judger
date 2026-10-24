"""Exercise concrete mode through a real Terminal; suppress only opening Chrome."""
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
    with tempfile.TemporaryDirectory(prefix='trigger-execution-tty-') as directory:
        pid, fd = pty.fork()
        if pid == 0:
            os.environ['TERM'] = 'xterm-256color'
            os.chdir(app)
            sys.path.insert(0, str(app))
            from trigger_agent.execution import Execution
            Execution._open_browser = lambda self: None
            from trigger_agent.cli import main as run
            run(['--demo', '--no-log', '--workspace', directory, '--task', 'Try the webpage controls'])
            os._exit(0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 112, 0, 0))
        output = bytearray()
        reaped = False

        def wait_for(text, timeout=10):
            start = len(output)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if text in output[start:].decode('utf-8', errors='replace'):
                    return
                if select.select([fd], [], [], .1)[0]:
                    try:
                        output.extend(os.read(fd, 65536))
                    except OSError:
                        break
            raise AssertionError(text + '\n' + output.decode('utf-8', errors='replace')[-2000:])

        try:
            wait_for('Webpage demo ready')
            os.write(fd, b'c')
            wait_for('All controls stay in Terminal')
            os.write(fd, b'=')
            wait_for('Color: 60%')
            os.write(fd, '】'.encode())
            wait_for('Make it bolder')
            os.write(fd, b'=')
            wait_for('Boldness: 60%')
            os.write(fd, b'b')
            wait_for('withdrawn')
            os.write(fd, b'c')
            wait_for('Back to questions')
            os.write(fd, b'q')
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                result, status = os.waitpid(pid, os.WNOHANG)
                if result:
                    reaped = True
                    assert os.waitstatus_to_exitcode(status) == 0
                    break
                if select.select([fd], [], [], .05)[0]:
                    try:
                        output.extend(os.read(fd, 65536))
                    except OSError:
                        pass
            assert reaped
            project = next(Path(directory).iterdir())
            assert (project/'index.html').is_file()
            assert 'saturate(1.20)' in (project/'trigger-style.css').read_text()
            print('PASS: Terminal C mode, category keys, = adjustments, b undo, C return, pure project HTML')
        finally:
            if not reaped:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
            os.close(fd)


if __name__ == '__main__':
    main()
