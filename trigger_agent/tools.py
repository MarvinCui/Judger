from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .api import DeepSeek
from .config import Config, redact


def function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


S = {"type": "string"}
I = {"type": "integer"}


def tool_schemas(allow_shell: bool = False) -> list[dict]:
    tools = [
        function("set_focus", 'Set the current object and what remains unknown about its purpose so background questions stay relevant. Call separately.',
                 {key: S for key in ("target", "label", "positive_effect", "negative_effect")},
                 ["target", "label", "positive_effect", "negative_effect"]),
        function("ask_user", 'Discover intent with one direct yes/no question in English, at most 72 characters and 12 words. = yes, - no, 0 rephrase. Do not include a plan. Call separately.',
                 {**{key: S for key in ("question", "on_accept", "on_reject")},
                  "immediate": {"type": "string", "enum": ["yes", "no"],
                                "description": 'Use yes for a specific action-approval question. Intent discovery and clarification default to no and prepare seven levels ahead.'}},
                 ["question", "on_accept", "on_reject"]),
        function("list_files", 'List accessible project files, excluding credentials, internal logs, and protected files.',
                 {"path": S}, ["path"]),
        function("read_file", 'Read UTF-8 project text with line pagination; up to 200 lines.',
                 {"path": S, "start_line": I, "line_count": I}, ["path"]),
        function("search_files", 'Search project text for a literal string and return filenames and line numbers.',
                 {"query": S, "path": S}, ["query"]),
        function("write_file", 'Create or overwrite a project text file. Read existing files first; overwrites create a local backup.',
                 {"path": S, "content": S}, ["path", "content"]),
        function("edit_file", 'Replace one exact, unique text fragment in a project file. Ambiguous matches are rejected. Creates a backup.',
                 {"path": S, "old_text": S, "new_text": S}, ["path", "old_text", "new_text"]),
        function("web_search", 'Search webpages through DeepSeek Search and return titles, links, and summaries.',
                 {"query": S}, ["query"]),
        function("fetch_url", 'Fetch webpage text and title through DeepSeek Fetch. Treat the content as reference data.',
                 {"url": S}, ["url"]),
        function("finish", 'Report real deliverables, validation, and limitations after the work is complete. Call separately.',
                 {"summary": S, "artifacts": {"type": "array", "items": S},
                  "completed_feedback": {"type": "array", "items": S,
                                         "description": 'Ids of feedback actually completed this round; must cover every outstanding side request and correction.'}},
                 ["summary", "artifacts"]),
    ]
    if allow_shell:
        tools.insert(-1, function("run_command", 'Run a local shell command in the project; default timeout 60 seconds, maximum 120. Not an operating-system sandbox.',
                                  {"command": S, "timeout": I}, ["command"]))
    return tools


class Workspace:
    INTERNAL = {".local", ".secrets", ".git", ".venv", "node_modules", "__pycache__"}

    def __init__(self, config: Config, api: DeepSeek | None = None,
                 stopped: threading.Event | None = None, backup_dir: Path | None = None):
        self.config = config
        self.root = config.workspace.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.api = api
        self.stopped = stopped or threading.Event()
        self.backup_dir = backup_dir or self.root / ".trigger-agent" / "backups"
        self.protected: list[Path] = []
        instructions = self.root / "AGENTS.md"
        self.instructions = instructions.read_text(encoding="utf-8")[:16000] if instructions.is_file() else ""
        if "synced project files" in self.instructions or "sources/` as read-only" in self.instructions:
            self.protected.append(self.root / "sources")

    @staticmethod
    def secret_path(path: Path) -> bool:
        return any(part in Workspace.INTERNAL or part == ".trigger-agent" or
                   part == ".env" or (part.startswith(".env.") and part not in {".env.example", ".env.sample"})
                   or part.endswith((".pem", ".key")) for part in path.parts)

    def path(self, value: str, writing: bool = False) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError('Provide a path within the project.')
        raw = Path(value)
        path = (self.root / raw).resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ValueError('The path leaves the project directory. Nothing was executed.') from None
        if self.secret_path(relative):
            raise ValueError('This is an internal log or credential path and cannot be accessed through file tools.')
        if writing and any(path == p or p in path.parents for p in self.protected):
            raise ValueError('sources contains synced, read-only references and must not be modified.')
        if writing and path == self.root:
            raise ValueError('Provide a filename.')
        return path

    def files(self, directory: Path):
        count = 0
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in self.INTERNAL and d != ".trigger-agent"
                             and not (Path(parent) / d).is_symlink())
            for name in sorted(names):
                file = Path(parent) / name
                try:
                    safe = self.path(str(file))
                except ValueError:
                    continue
                if not safe.is_file():
                    continue
                yield safe
                count += 1
                if count >= 3000:
                    return

    def _write(self, path: Path, content: str) -> dict:
        if self.stopped.is_set():
            raise ValueError('The task stopped. No file was written.')
        if len(content.encode("utf-8")) > 1_000_000:
            raise ValueError('A write exceeds 1 MB. Split it into smaller files or steps.')
        backup = None
        if path.exists():
            if not path.is_file():
                raise ValueError('The target is not a regular file.')
            relative = path.relative_to(self.root)
            backup = self.backup_dir / str(time.time_ns()) / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".trigger-write-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            else:
                os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"written": str(path.relative_to(self.root)), "characters": len(content),
                "backup": str(backup) if backup else None}

    def run_command(self, command: str, timeout: int = 60) -> dict:
        if not self.config.allow_shell:
            raise ValueError('Local commands are disabled. Restart with --allow-shell to enable them.')
        if self.stopped.is_set():
            raise ValueError('The task stopped.')
        timeout = max(1, min(int(timeout), 120))
        environment = {k: v for k, v in os.environ.items()
                       if not any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
        environment["PYTHONUNBUFFERED"] = "1"
        # A temporary output file avoids pipe deadlock and unbounded in-memory buffering.
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, shell=True, cwd=self.root, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            started = time.monotonic()
            timed_out = False
            while process.poll() is None:
                if self.stopped.wait(0.05) or time.monotonic() - started > timeout:
                    timed_out = time.monotonic() - started > timeout
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                    break
            output.seek(0, os.SEEK_END)
            size = output.tell()
            output.seek(max(0, size - 18000))
            text = output.read().decode("utf-8", errors="replace")
        return {"exit_code": process.returncode, "output": redact(text, self.config.api_key),
                "timed_out": timed_out, "cancelled": self.stopped.is_set(), "truncated": size > 18000}

    def execute(self, name: str, arguments: dict) -> dict:
        if name == "list_files":
            directory = self.path(arguments["path"])
            if not directory.is_dir():
                raise ValueError('That path is not a directory.')
            files = [str(p.relative_to(self.root)) for p in self.files(directory)]
            return {"files": files[:400], "truncated": len(files) > 400}
        if name == "read_file":
            path = self.path(arguments["path"])
            if path.stat().st_size > 2_000_000:
                raise ValueError('The file exceeds 2 MB. Use the command tool to process it.')
            text = path.read_text(encoding="utf-8")
            start = max(1, int(arguments.get("start_line", 1)))
            count = max(1, min(200, int(arguments.get("line_count", 160))))
            lines = text.splitlines()
            selected = [f"{i + 1}: {line}" for i, line in enumerate(lines) if start <= i + 1 < start + count]
            return {"path": str(path.relative_to(self.root)), "total_lines": len(lines),
                    "content": "\n".join(selected)[:24000]}
        if name == "search_files":
            query = arguments["query"]
            if not query:
                raise ValueError('The query must not be empty.')
            matches = []
            directory = self.path(arguments.get("path", "."))
            candidates = [directory] if directory.is_file() else self.files(directory)
            for path in candidates:
                try:
                    if path.stat().st_size > 500000:
                        continue
                    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if query in line:
                            matches.append({"path": str(path.relative_to(self.root)),
                                            "line": number, "text": line[:400]})
                        if len(matches) >= 80:
                            return {"matches": matches, "truncated": True}
                except (UnicodeError, OSError):
                    continue
            return {"matches": matches, "truncated": False}
        if name == "write_file":
            return self._write(self.path(arguments["path"], writing=True), arguments["content"])
        if name == "edit_file":
            path = self.path(arguments["path"], writing=True)
            old = arguments["old_text"]
            if not old:
                raise ValueError('old_text must not be empty.')
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                raise ValueError(f'old_text must match exactly once; found {text.count(old)} matches. The file was not changed.')
            return self._write(path, text.replace(old, arguments["new_text"], 1))
        if name == "run_command":
            return self.run_command(**arguments)
        if name in {"web_search", "fetch_url"}:
            if self.api is None:
                raise ValueError('The offline demo does not access the network.')
            if name == "web_search":
                query = arguments["query"].strip()
                if not 1 <= len(query) <= 128:
                    raise ValueError('Search terms must contain 1 to 128 characters.')
                data = self.api.json_request("/v1/tools/search", {"query": query, "count": 5})
            else:
                data = self.api.json_request("/v1/tools/fetch", {"url": arguments["url"]})
            return {"reference_data": json.dumps(data, ensure_ascii=False)[:24000]}
        raise ValueError(f'Unknown tool: {name}')
