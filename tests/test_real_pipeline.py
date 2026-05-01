"""Real Pipeline Test — 走完整真实 Controller + Arbitrator + SpeechManager 链路"""

import sys
import os
import time
import json
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.controller import Controller
from core.intent_parser import IntentParser, IntentType, IntentResult
from perception.spatial_utils import parse_environment
from perception.decision_utils import DecisionMaker
from perception.speech_manager import SpeechManager
from perception.speech_arbitrator import SpeechArbitrator
from vlm.vlm_manager import VLMManager

random.seed(42)

# ═══════════════════════════════════════════════════════════════════
# Mock TTS
# ═══════════════════════════════════════════════════════════════════
class MockTTS:
    def speak(self, text, interrupt=False):
        pass
    def stop(self):
        pass

# ═══════════════════════════════════════════════════════════════════
# Mock VLM Cloud Adapter
# ═══════════════════════════════════════════════════════════════════
class MockVLMAdapter:
    def __init__(self):
        self.call_count = 0
    def ask(self, messages):
        self.call_count += 1
        time.sleep(0.3)
        return f"[VLM#{self.call_count}] 我看到场景中有行人和车辆。"

# ═══════════════════════════════════════════════════════════════════
# Spatial Adapter
# ═══════════════════════════════════════════════════════════════════
class SpatialAdapter:
    def parse(self, raw_objects, frame_shape):
        return parse_environment(raw_objects, frame_shape)

# ═══════════════════════════════════════════════════════════════════
# Build Controller
# ═══════════════════════════════════════════════════════════════════
print("[INIT] Building real Controller...")
vlm_mgr = VLMManager(cloud_adapter=MockVLMAdapter())
ctrl = Controller(
    speech_manager=SpeechManager(min_interval=1.0, stable_count=1, tts_backend=MockTTS()),
    spatial_parser=SpatialAdapter(),
    decision_maker=DecisionMaker(),
    intent_parser=IntentParser(),
    vlm_manager=vlm_mgr,
)
print("[INIT] Done.")

# Trigger startup speech BEFORE any navigation/VLM
ctrl._play_startup_message()

# ═══════════════════════════════════════════════════════════════════
# Event generators
# ═══════════════════════════════════════════════════════════════════
OBJ_POOL = ["行人", "汽车", "自行车", "公交车", "摩托车"]

# Stable scene (same objects) for scene stability to activate
STABLE_OBJECTS = [
    {"class": "car", "bbox": [100, 80, 220, 200], "confidence": 0.9},
    {"class": "person", "bbox": [300, 60, 380, 190], "confidence": 0.9},
]

def make_nav_stable(t, frame_shape=(480, 640, 3)):
    return {
        "type": "navigation",
        "data": {"objects": STABLE_OBJECTS, "frame_shape": frame_shape},
        "timestamp": t,
    }

def make_nav_random(t, frame_shape=(480, 640, 3)):
    objects = []
    for _ in range(random.randint(2, 4)):
        cls = random.choice(OBJ_POOL)
        x = {"左侧": 60, "前方": 240, "右侧": 450}[random.choice(["左侧", "前方", "右侧"])]
        objects.append({
            "class": cls,
            "bbox": [x, 100, x + 90, 200],
            "confidence": 0.85,
        })
    return {
        "type": "navigation",
        "data": {"objects": objects, "frame_shape": frame_shape},
        "timestamp": t,
    }

def make_user_vlm(t, question):
    return {
        "type": "user_input",
        "data": {"text": question},
        "timestamp": t,
    }

# ═══════════════════════════════════════════════════════════════════
# Run 60s test
# ═══════════════════════════════════════════════════════════════════
print("[RUN] 60s real pipeline test...")
start = time.time()
frame = 0
vlm_queries = 0

while time.time() - start < 60.0:
    now = time.time()
    frame += 1

    # Set dummy image (needed for GENERAL_QA)
    ctrl.context["current_image"] = "data:image/jpeg;base64,/9j/4AAQ=="

    # Navigation: every ~0.5s
    if frame % 10 == 0:
        # First 4 frames: stable scene (to trigger decision flow)
        if frame <= 40:
            nav = make_nav_stable(now)
        else:
            nav = make_nav_random(now)
        ctrl.handle_event(nav)

    # VLM query: every 6s
    if frame % 120 == 0 and vlm_queries < 10:
        questions = [
            "室内还是室外？",   # → ENV_QUERY (direct answer)
            "有没有人？",       # → OBJECT_QUERY (direct answer)
            "他戴眼镜吗？",     # → GENERAL_QA (VLM)
            "汽车在哪里？",     # → OBJECT_QUERY (direct answer)
            "前面有什么？",     # → VLM
            "他戴眼镜吗？",     # → VLM
            "有没有人？",       # → OBJECT_QUERY
            "室内还是室外？",   # → ENV_QUERY
            "汽车在哪里？",     # → OBJECT_QUERY
            "他戴眼镜吗？",     # → VLM
        ]
        q = questions[vlm_queries]
        ctrl.handle_event(make_user_vlm(now, q))
        vlm_queries += 1

    # Warning: ~every 12s (simulated by generating a dangerous close object)
    if frame % 240 == 0:
        warn_objs = [
            {"class": "汽车", "bbox": [300, 40, 500, 400], "confidence": 0.95},
        ]
        nav = {
            "type": "navigation",
            "data": {"objects": warn_objs, "frame_shape": (480, 640, 3)},
            "timestamp": now,
        }
        ctrl.handle_event(nav)

    ctrl._poll_vlm_results()
    ctrl._drain_arbitrator()

    time.sleep(0.04)

print(f"[DONE] frames={frame}, vlm_queries={vlm_queries}")

# ═══════════════════════════════════════════════════════════════════
# Analyze speech_manager internal state
# ═══════════════════════════════════════════════════════════════════
arb = ctrl.arbitrator
print()
print("=" * 60)
print("      REAL PIPELINE ANALYSIS")
print("=" * 60)
print(f"  warning_queue:      {len(arb.warning_queue)}")
print(f"  vlm_queue:          {len(arb.vlm_queue)}")
print(f"  env_queue:          {len(arb.env_queue)}")
print(f"  last_play_time:     {arb.last_play_time:.1f}")
print(f"  last_vlm_play_time: {arb.last_vlm_play_time:.1f}")
print(f"  output_policy:      {ctrl.enable_output_policy}")
print()
print("=" * 60)
