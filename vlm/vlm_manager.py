"""System-facing VLM entrypoint.

Only upper-layer modules should depend on ``VLMManager``.
Controller must not call demo utilities or cloud SDKs directly.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from .vlm_cloud_adapter import VLMCloudAdapter
from core.global_config import CONFIG


class VLMManager:
    """Single entrypoint for VLM capabilities inside the system."""

    def __init__(self, cloud_adapter: Optional[VLMCloudAdapter] = None) -> None:
        self.cloud_adapter = cloud_adapter or VLMCloudAdapter()
        self.lock = threading.Lock()

        self.queue = deque()
        self.result_queue = deque()
        self.last_call_time = 0.0

        self._scheduler_interval = CONFIG.get("vlm.scheduler_interval", 3.0)
        self._max_queue = CONFIG.get("vlm.max_queue", 1)
        self._simulate_delay = CONFIG.get("vlm.simulate_delay", 0.0)

        threading.Thread(target=self._scheduler_loop, name="vlm-scheduler", daemon=True).start()

    def ask_async(
        self,
        image: str,
        text: str,
        context: dict,
        version: int = 0,
    ) -> None:
        """入队 VLM 请求（非阻塞）"""
        with self.lock:
            if self._max_queue == 1:
                self.queue.clear()
            elif len(self.queue) >= self._max_queue:
                self.queue.popleft()

            self.queue.append({
                "image": image,
                "text": text,
                "context": context,
                "version": version,
            })

    def poll_result(self):
        """Controller 调用，获取一条已完成的 VLM 结果"""
        with self.lock:
            if not self.result_queue:
                return None
            return self.result_queue.popleft()

    def _scheduler_loop(self):
        while True:
            task = None
            with self.lock:
                if self.queue and time.time() - self.last_call_time >= self._scheduler_interval:
                    task = self.queue.popleft()
                    self.last_call_time = time.time()

            if task is None:
                time.sleep(0.1)
                continue

            self._run_task(task)

    def _run_task(self, task):
        try:
            image = task["image"]
            text = task["text"]
            context = task["context"]
            version = task.get("version", 0)

            history = context.get("recent_events", [])

            messages = []

            # 1. System instruction (always)
            messages.append({
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "你是盲人视觉助手，请根据图片内容以最简洁的语言回答用户的问题，回答避免出现'图片中'等措辞。",
                }],
            })

            # 2. History (text only)
            for item in history:
                messages.append(
                    {"role": "user", "content": [{"type": "text", "text": item["user"]}]}
                )
                messages.append({"role": "assistant", "content": item["assistant"]})

            # 3. Current question WITH image (always)
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image}},
                    {"type": "text", "text": text},
                ],
            })

            has_image = any("image_url" in str(m) for m in messages)
            print("[VLM_PAYLOAD_CHECK]", {
                "has_image": has_image,
                "history_len": len(history),
                "total_messages": len(messages),
            })

            answer = self.cloud_adapter.ask(messages)

            if self._simulate_delay > 0:
                time.sleep(self._simulate_delay)

            if not answer or not answer.strip():
                result = "我暂时无法判断，可以换个角度再试一下"
                is_fallback = True
            else:
                result = answer
                is_fallback = False
        except Exception:
            result = "我暂时无法判断，可以换个角度再试一下"
            is_fallback = True

        with self.lock:
            self.result_queue.append({
                "text": result,
                "version": task.get("version", 0),
                "is_fallback": is_fallback,
            })
