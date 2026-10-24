from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def create_project(root: Path, task: str) -> Path:
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\-]+", "-", task, flags=re.UNICODE).strip("-_")[:18] or 'new-project'
    stamp = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y%m%d-%H%M%S-%f")
    path = root / f"{stamp}-{slug}"
    path.mkdir()
    return path
