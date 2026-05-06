"""
Stage 2 Automation: Controller + USER_FOCUS verification
==========================================================
Verifies that navigation events are suppressed during USER_FOCUS
windows via Controller.handle_event() — the real scheduling chain.

3 rounds × 120s each. Alternating scene objects to trigger
navigation SUBMITs near USER_FOCUS windows.

Output: logs/stage2_controller_run{n}.jsonl
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import perception.speech_arbitrator as sa_module
from observe.trace_logger import TraceLogger
from core.controller import Controller
from perception.spatial_adapter import SpatialParserAdapter
from perception.decision_utils import DecisionMaker
from perception.speech_manager import SpeechManager


class MockTTS:
    def speak(self, text, interrupt=False):
        pass

    def stop(self):
        pass


# Object sets — distinct class+position to force scene changes
OBJ_A = [
    {
        "class": "person", "class_zh": "行人",
        "confidence": 0.9, "bbox": [200, 100, 440, 300],
        "distance": "较近", "is_danger": False,
    }
]

OBJ_B = [
    {
        "class": "car", "class_zh": "汽车",
        "confidence": 0.9, "bbox": [430, 100, 630, 300],
        "distance": "较近", "is_danger": False,
    }
]

TRIGGER_POINTS = [10, 40, 80]
SCENE_SWITCH_TIMES = [7, 37, 77]  # switch to B/A/B so new scene stabilises inside USER_FOCUS


def run_one_round(run_id):
    trace_path = os.path.join(ROOT, "logs", f"stage2_controller_run{run_id}.jsonl")
    if os.path.exists(trace_path):
        os.remove(trace_path)

    tl = TraceLogger(trace_path)
    sa_module._trace_logger = tl

    sm = SpeechManager(min_interval=2.0, stable_count=3, tts_backend=MockTTS())
    ctrl = Controller(
        spatial_parser=SpatialParserAdapter(),
        decision_maker=DecisionMaker(),
        speech_manager=sm,
        enable_logging=True,
    )
    ctrl.arbitrator.trace = tl
    ctrl.is_startup_phase = False
    ctrl.startup_played = True
    ctrl.cold_start_active = False

    triggered = {t: False for t in TRIGGER_POINTS}
    last_nav = 0.0
    start = time.time()

    while time.time() - start < 120:
        now = time.time()
        elapsed = now - start

        # scene alternation
        if elapsed < SCENE_SWITCH_TIMES[0]:
            objs = OBJ_A
        elif elapsed < SCENE_SWITCH_TIMES[1]:
            objs = OBJ_B
        elif elapsed < SCENE_SWITCH_TIMES[2]:
            objs = OBJ_A
        else:
            objs = OBJ_B

        # navigation every 2s
        if now - last_nav >= 2.0:
            nav = {
                "type": "navigation",
                "data": {"objects": objs, "frame_shape": (480, 640)},
                "timestamp": time.time(),
            }
            ctrl.handle_event(nav)
            last_nav = now

        # user_input one-shot
        for t in TRIGGER_POINTS:
            if not triggered[t] and elapsed >= t:
                user = {
                    "type": "user_input",
                    "data": {"text": "前面有什么？"},
                    "timestamp": time.time(),
                }
                ctrl.handle_event(user)
                triggered[t] = True

        ctrl._poll_vlm_results()
        ctrl._drain_arbitrator()

        time.sleep(0.05)

    sm.stop()
    return trace_path


# ── analysis ────────────────────────────────────────

def analyze_user_focus_windows(trace_path):
    """For each USER_FOCUS window, count navigation PLAYS inside the 5s window."""
    with open(trace_path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    # find user_input SUBMIT times
    focus_enter_ts = []
    for e in events:
        if e.get("event") == "USER_FOCUS_ENTER":
            focus_enter_ts.append(e.get("ts", 0))
    # fallback: find user_input events
    if not focus_enter_ts:
        focus_enter_ts = [e["ts"] for e in events if e.get("event") == "user_input"]

    results = []
    for ts in focus_enter_ts:
        window_start = ts
        window_end = ts + 5.0
        nav_plays = 0
        for e in events:
            if e.get("event") == "PLAY":
                pt = e.get("ts", 0)
                src = e.get("source", "")
                if window_start <= pt <= window_end and src == "decision":
                    nav_plays += 1
        results.append({"focus_ts": ts, "nav_plays_in_window": nav_plays})

    return results


def main():
    print("=" * 60)
    print("  Stage 2: Controller + USER_FOCUS")
    print("  (navigation suppression during user question windows)")
    print("=" * 60)

    t0 = time.time()
    all_results = []

    for run_id in range(1, 4):
        print(f"\n--- Run {run_id} ---")
        path = run_one_round(run_id)
        windows = analyze_user_focus_windows(path)
        all_results.append((run_id, windows))
        for w in windows:
            print(f"  USER_FOCUS @ t={w['focus_ts']:.0f}s  →  nav_plays_in_window={w['nav_plays_in_window']}")

    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  All done in {elapsed:.0f}s")
    print(f"{'=' * 60}")

    # verdict: should be 0 in every window
    total_violations = 0
    for rid, windows in all_results:
        for w in windows:
            total_violations += w["nav_plays_in_window"]

    print("\n=== STAGE 2 RESULT ===")
    if total_violations == 0:
        print("  PASS: USER_FOCUS 100% effective — no navigation plays during any window")
    else:
        print(f"  FAIL: {total_violations} navigation play(s) leaked into USER_FOCUS windows")

    print(f"\n  Outputs: logs/stage2_controller_run*.jsonl")


if __name__ == "__main__":
    main()
