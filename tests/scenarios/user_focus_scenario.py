"""Phase A: USER_FOCUS 场景生成 — 使用真实 Controller 驱动"""

import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.controller import Controller
from core.intent_parser import IntentParser
from perception.spatial_utils import parse_environment
from perception.decision_utils import DecisionMaker
from perception.speech_manager import SpeechManager
from perception.speech_arbitrator import SpeechArbitrator
from vlm.vlm_manager import VLMManager


class MockTTS:
    def speak(self, text, interrupt=False): pass
    def stop(self): pass


class MockVLMAdapter:
    def __init__(self):
        self.call_count = 0
    def ask(self, messages):
        self.call_count += 1
        time.sleep(0.3)
        return f"[VLM#{self.call_count}] 我看到前方有行人和车辆。"


class SpatialAdapter:
    def parse(self, raw_objects, frame_shape):
        return parse_environment(raw_objects, frame_shape)


STABLE_OBJECTS = [
    {"class": "car", "bbox": [100, 80, 220, 200], "confidence": 0.9},
    {"class": "person", "bbox": [300, 60, 380, 190], "confidence": 0.9},
]
VARIANT_OBJECTS = [
    {"class": "car", "bbox": [120, 80, 240, 200], "confidence": 0.9},
    {"class": "person", "bbox": [320, 60, 400, 190], "confidence": 0.9},
    {"class": "bicycle", "bbox": [60, 120, 140, 200], "confidence": 0.8},
]

_event_log = []


def _log_event(kind, **kwargs):
    entry = {"event": kind, "time": time.time(), **kwargs}
    _event_log.append(entry)


def generate_user_focus_log():
    """使用真实 Controller 驱动 USER_FOCUS 场景"""
    global _event_log
    _event_log = []

    vlm_mgr = VLMManager(cloud_adapter=MockVLMAdapter())
    ctrl = Controller(
        speech_manager=SpeechManager(min_interval=1.0, stable_count=1, tts_backend=MockTTS()),
        spatial_parser=SpatialAdapter(),
        decision_maker=DecisionMaker(),
        intent_parser=IntentParser(),
        vlm_manager=vlm_mgr,
    )
    ctrl._play_startup_message()

    # Patch _drain_arbitrator to capture played items
    _orig_drain = ctrl._drain_arbitrator
    def _patched_drain():
        if ctrl.speech.is_speaking():
            _orig_drain()
            return
        item = ctrl.arbitrator.select_next()
        if item:
            _log_event("played",
                       time=time.time(),
                       source=item.get("source", "?"),
                       priority=item.get("priority", 3),
                       text=item.get("text", "")[:40])
            ctrl.speech.speak_now(item["text"], priority=1)
            if item.get("source") == "vlm":
                ctrl.arbitrator.mark_vlm_played()
        else:
            _orig_drain()
    ctrl._drain_arbitrator = _patched_drain

    t0 = time.time()

    # 1. 稳定场景 → 产生 ENV 播报
    for _ in range(4):
        nav = {
            "type": "navigation",
            "data": {"objects": STABLE_OBJECTS, "frame_shape": (480, 640, 3)},
            "timestamp": time.time(),
        }
        ctrl.context["current_image"] = "data:image/jpeg;base64,/9j/4AAQ=="
        ctrl.handle_event(nav)
        ctrl._poll_vlm_results()
        ctrl._drain_arbitrator()
        time.sleep(0.05)

    time.sleep(0.3)
    _log_event("stage", stage="scene_stabilized")

    # 2. 用户提问 → 触发 USER_FOCUS
    t1 = time.time()
    user_event = {
        "type": "user_input",
        "data": {"text": "他戴眼镜吗？"},
        "timestamp": t1,
    }
    ctrl.context["current_image"] = "data:image/jpeg;base64,/9j/4AAQ=="
    ctrl.handle_event(user_event)
    _log_event("user_input", time=t1)
    ctrl._poll_vlm_results()
    ctrl._drain_arbitrator()

    # 3. ENV 干扰注入（应在 USER_FOCUS 中被阻断）
    # 使用相同 objects 避免 scene version 漂移导致 VLM 结果被丢弃
    for dt in [0.3, 0.6, 0.9]:
        nav = {
            "type": "navigation",
            "data": {"objects": STABLE_OBJECTS, "frame_shape": (480, 640, 3)},
            "timestamp": t1 + dt,
        }
        ctrl.context["current_image"] = "data:image/jpeg;base64,/9j/4AAQ=="
        ctrl.handle_event(nav)
        ctrl._poll_vlm_results()
        ctrl._drain_arbitrator()
        time.sleep(0.05)

    # 4. 等待 VLM 返回并播放（期间不注入 nav，避免 scene version 漂移）
    for _ in range(25):
        ctrl._poll_vlm_results()
        ctrl._drain_arbitrator()
        time.sleep(0.15)

    # 5. USER_FOCUS 过期后的 ENV → 应恢复正常
    time.sleep(5.5)
    for _ in range(4):
        nav = {
            "type": "navigation",
            "data": {"objects": VARIANT_OBJECTS, "frame_shape": (480, 640, 3)},
            "timestamp": time.time(),
        }
        ctrl.context["current_image"] = "data:image/jpeg;base64,/9j/4AAQ=="
        ctrl.handle_event(nav)
        ctrl._poll_vlm_results()
        ctrl._drain_arbitrator()
        time.sleep(0.05)

    # 6. 提取已播放的条目作为事件日志
    played_items = []
    while True:
        item = ctrl.arbitrator.select_next()
        if item is None:
            break
        _log_event("played",
                   time=item.get("_play_time", time.time()),
                   source=item.get("source", "?"),
                   priority=item.get("priority", 3),
                   text=item.get("text", "")[:40])
        played_items.append(item)

    # 补充: 记录冷启动和已播放的项目
    _log_event("played",
               time=t0,
               source="startup",
               priority=0,
               text="系统已启动。")

    return _event_log


if __name__ == "__main__":
    log = generate_user_focus_log()
    print(f"Generated {len(log)} events")
    for e in log:
        print(f"  {e}")
