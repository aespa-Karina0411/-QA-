"""实时调度状态面板 — 只读 daemon 线程，零调度影响。
激活方式：export EDGE_VISION_DASHBOARD=1 && python main.py"""

import time
import threading


def _bar(label, queue, max_len):
    items = [it.get("text", it.get("trace_id", "?"))[:8] for it in queue]
    fill = "#" * len(items) + " " * max(0, max_len - len(items))
    return f"  {label:8s} [{fill}] {', '.join(items) if items else '(empty)'}"


def _run(controller):
    while True:
        try:
            arb = controller.arbitrator
            sm = controller.speech_manager
            ctx = controller.context
            focus = ctx["system"]["user_focus"]

            now = time.time()
            playing = sm.speech_lock["owner"] if sm.speech_lock["active"] else "idle"
            throttled = "ACTIVE" if now - arb.last_play_time < 1.5 else "idle"
            aging = "TRIGGERED" if any(
                now - it.get("enqueue_ts", now) > 4.0
                for it in arb.vlm_queue
            ) else "idle"
            user_focus = "TRUE" if focus.get("active") else "FALSE"

            print("\033[2J\033[H")  # clear screen
            print("=" * 44)
            print("  edge-visionQA  Dashboard")
            print("=" * 44)
            print(_bar("WARNING", arb.warning_queue, 3))
            print(_bar("VLM    ", arb.vlm_queue, 5))
            print(_bar("ENV    ", arb.env_queue, 3))
            print()
            print(f"  Playing:   {playing}")
            print(f"  Throttle:  {throttled}")
            print(f"  UserFocus: {user_focus}")
            print(f"  Aging:     {aging}")
            print(f"  Submits:   {sum(1 for q in (arb.warning_queue, arb.vlm_queue, arb.env_queue) for _ in q)} queued")
            print("=" * 44)

        except Exception:
            pass

        time.sleep(0.5)


def start(controller):
    t = threading.Thread(target=_run, args=(controller,), name="dashboard", daemon=True)
    t.start()
