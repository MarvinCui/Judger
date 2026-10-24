from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = APP_ROOT / ".local"
KEY_FILE = LOCAL_DIR / "deepseek.key"


def redact(text: str, secret: str = "") -> str:
    if secret:
        text = text.replace(secret, "[API KEY REDACTED]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[API KEY REDACTED]", text)


def save_key(key: str, path: Path = KEY_FILE) -> None:
    key = key.strip()
    if not key or "\n" in key or "\r" in key:
        raise ValueError('Enter a valid single-line API key.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(key + "\n")


def load_key(path: Path = KEY_FILE) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key and path.is_file():
        key = path.read_text(encoding="utf-8").strip()
    return key


@dataclass(frozen=True)
class Config:
    workspace: Path
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    allow_shell: bool = False
    lookahead: int = 4
    main_lookahead: int = 7
    auto_submit: int = 2
    max_steps: int = 60
    max_tokens: int = 8192
    demo: bool = False
    executor: str = "deepseek"
    codex_bin: str = "codex"


class Journal:
    """Local research log. Never records HTTP headers or the API key."""

    def __init__(self, path: Path | None, secret: str = ""):
        self.path = path
        self.secret = secret
        self.lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def write(self, item: dict) -> None:
        if not self.path:
            return
        from datetime import datetime, timezone

        item = {"time": datetime.now(timezone.utc).isoformat(), **item}
        line = redact(json.dumps(item, ensure_ascii=False), self.secret)
        with self.lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                stream.write(line + "\n")
