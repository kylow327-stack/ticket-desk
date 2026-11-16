"""JSONL run traces. Secrets are never written."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

_SECRET_PATTERN = re.compile(
    r"(?i)(sk-[A-Za-z0-9]{8,}|DEEPSEEK_API_KEY\s*=\s*\S+|api[_-]?key\s*[:=]\s*\S+)"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if re.search(r"(?i)(api[_-]?key|secret|authorization|password|token)", str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_PATTERN.sub("[redacted]", value)
    return value


class TraceLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(redact(record), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def load(self, run_id: Optional[str] = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if run_id is None or item.get("run_id") == run_id or item.get("id") == run_id:
                    records.append(item)
        return records

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        matches = self.load(run_id)
        return matches[-1] if matches else None
