"""结构化日志系统 — 追加 JSONL，内存 buffer + 定时 flush"""

import json
import os
import time


class TraceLogger:
    def __init__(self, path="logs/trace.jsonl", flush_interval=2.0, buffer_size=20):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._buffer = []
        self._buf_size = buffer_size
        self._flush_interval = flush_interval
        self._last_flush = 0.0

    def log(self, event_type, **kwargs):
        record = json.dumps(
            {"ts": time.time(), "event": event_type, **kwargs},
            ensure_ascii=False,
        )
        self._buffer.append(record)

        now = time.time()
        if len(self._buffer) >= self._buf_size or now - self._last_flush >= self._flush_interval:
            self._flush()

    def _flush(self):
        if not self._buffer:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(self._buffer) + "\n")
            self._buffer.clear()
            self._last_flush = time.time()
        except Exception:
            pass
