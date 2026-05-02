"""结构化日志系统 — 追加 JSONL，不替换 print"""

import json
import os
import time


class TraceLogger:
    def __init__(self, path="logs/trace.jsonl"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path

    def log(self, event_type, **kwargs):
        record = {"ts": time.time(), "event": event_type, **kwargs}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
