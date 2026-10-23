from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config


class Audit:
    """Audit trail JSONL: siapa, kapan, URL apa, status berapa, ukuran berapa."""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def log(
        self,
        method: str,
        url: str,
        status: int | None = None,
        bytes_len: int | None = None,
        elapsed_ms: int | None = None,
        note: str = "",
    ) -> None:
        if not self.enabled:
            return
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "method": method,
            "url": url,
            "status": status,
            "bytes": bytes_len,
            "elapsed_ms": elapsed_ms,
            "note": note,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def event(self, kind: str, **data: Any) -> None:
        if not self.enabled:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": kind,
            **data,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        if not self.path.exists():
            return {"total": 0}
        counts: dict[str, int] = {}
        total_bytes = 0
        total = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in entry:
                counts[entry["event"]] = counts.get(entry["event"], 0) + 1
                continue
            total += 1
            key = str(entry.get("status"))
            counts[key] = counts.get(key, 0) + 1
            total_bytes += entry.get("bytes") or 0
        return {
            "total_requests": total,
            "total_bytes": total_bytes,
            "breakdown": counts,
        }


class RateLimiter:
    def __init__(self, min_delay: float, max_delay: float) -> None:
        self.min_delay = min_delay
        self.max_delay = max(max_delay, min_delay)
        self._last: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            elapsed = now - self._last
            jitter = ((hash(str(self._last)) % 1000) / 1000.0)
            target = self.min_delay + (self.max_delay - self.min_delay) * jitter
            sleep_for = target - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._last = time.monotonic()


def load_config(path: Path | None = None) -> Config:
    cfg = Config.load(path)
    cfg.validate()
    return cfg
