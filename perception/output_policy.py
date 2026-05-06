"""输出策略层：决定 speech 是否允许播放。
   在 Decision → Arbitrator 之间插入，不影响调度内核。"""

import time
from core.global_config import CONFIG


class OutputPolicy:

    def __init__(self):
        self.last_objects = None
        self.last_speech_time = []
        self.window = CONFIG.get("speech.speech_budget_window", 5.0)
        self.max_speech = CONFIG.get("speech.speech_budget_max", 2)

    def allow(self, item: dict) -> bool:
        """
        item: {text, priority, source, objects(optional)}
        返回 True 表示允许播放，False 表示抑制。
        """
        # Bypass: 启动语句、冷启动环境播报等特殊路径直接放行
        if item.get("bypass_policy") or item.get("is_cold_start_env"):
            return True

        now = time.time()

        # 1. WARNING 永远允许
        if item.get("priority") == 1:
            return True

        # 2. Speech Budget：窗口内最多 max_speech 条
        self._cleanup(now)
        if len(self.last_speech_time) >= self.max_speech:
            return False

        # 3. ENV 降噪：相同场景不重复
        if item.get("source") == "decision":
            if not self._is_significant(item):
                return False

        # 4. 通过 → 记录时间
        self.last_speech_time.append(now)
        return True

    def _cleanup(self, now):
        self.last_speech_time = [
            t for t in self.last_speech_time
            if now - t < self.window
        ]

    def _is_significant(self, item):
        objs = item.get("objects")
        if not objs:
            return True

        if isinstance(objs, list):
            key = tuple(
                (o.get("class_zh", o.get("class", "")),
                 o.get("distance", ""))
                for o in objs[:5]
            )
        else:
            return True

        if key != self.last_objects:
            self.last_objects = key
            return True

        return False
