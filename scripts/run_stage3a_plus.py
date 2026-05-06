"""
Stage 3A+ 参数扫描实验
======================
通过控制主循环节奏模拟 Pi 性能约束，扫描 delay × interval 组合。
输出：trace 文件 + summary CSV。

DELAYS    = [0.0, 0.1, 0.3, 0.5]  秒 (模拟 YOLO+ASR 帧处理延迟)
INTERVALS = [2, 5]                 秒 (navigation 触发间隔)
combo × 60s × 1 round = 预计 8 分钟
"""

import csv
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


OBJ_A = [{"class": "person", "class_zh": "行人", "confidence": 0.9,
           "bbox": [200, 100, 440, 300], "distance": "较近", "is_danger": False}]
OBJ_B = [{"class": "car", "class_zh": "汽车", "confidence": 0.9,
           "bbox": [430, 100, 630, 300], "distance": "较近", "is_danger": False}]

USER_TIMES = [10, 30, 50]


def run_one(delay: float, interval: float, out_path: str) -> dict:
    if os.path.exists(out_path):
        os.remove(out_path)

    tl = TraceLogger(out_path)
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

    triggered = {t: False for t in USER_TIMES}
    last_nav = 0.0
    loop_start = time.time()

    try:
        while time.time() - loop_start < 60:
            # 模拟帧处理延迟 (Pi 的 camera+YOLO 耗时)
            if delay > 0:
                time.sleep(delay)

            now = time.time()
            elapsed = now - loop_start

            # 场景交替
            if int(elapsed) % 16 < 8:
                objs = OBJ_A
            else:
                objs = OBJ_B

            # navigation
            if now - last_nav >= interval:
                ctrl.handle_event({
                    "type": "navigation",
                    "data": {"objects": objs, "frame_shape": (480, 640)},
                    "timestamp": time.time(),
                })
                last_nav = now

            # user_input
            for t in USER_TIMES:
                if not triggered[t] and elapsed >= t:
                    ctrl.handle_event({
                        "type": "user_input",
                        "data": {"text": "前面有什么？"},
                        "timestamp": time.time(),
                    })
                    triggered[t] = True

            ctrl._poll_vlm_results()
            ctrl._drain_arbitrator()

    except Exception as e:
        print(f"  [ERROR] {e}")
        raise
    finally:
        sm.stop()

    return _analyze(out_path)


def _analyze(path: str) -> dict:
    events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    tasks = {}
    for e in events:
        tid = e.get("id")
        if not tid: continue
        if tid not in tasks: tasks[tid] = {}
        if e["event"] == "SUBMIT":
            tasks[tid]["submit_ts"] = e["ts"]
            tasks[tid]["source"] = e.get("source", "")
            tasks[tid]["priority"] = e.get("priority", 3)
        if e["event"] == "PLAY":
            tasks[tid]["play_ts"] = e["ts"]
        if e["event"] == "SELECT":
            tasks[tid]["select_ts"] = e["ts"]

    nav_sub = usr_sub = nav_ply = usr_ply = 0
    waits = []
    wait_selects = []
    wait_plays = []

    for tid, t in tasks.items():
        src = t.get("source", "")
        if src in ("decision", "navigation"):
            nav_sub += 1
            if "play_ts" in t: nav_ply += 1
        elif src == "user_direct":
            usr_sub += 1
            if "play_ts" in t: usr_ply += 1

        if "submit_ts" in t and "play_ts" in t:
            w = t["play_ts"] - t["submit_ts"]
            if w >= 0: waits.append(w)
        if "submit_ts" in t and "select_ts" in t:
            ws = t["select_ts"] - t["submit_ts"]
            if ws >= 0: wait_selects.append(ws)
        if "select_ts" in t and "play_ts" in t:
            wp = t["play_ts"] - t["select_ts"]
            if wp >= 0: wait_plays.append(wp)

    drop_env = sum(1 for e in events if "throttled_drop_env" in str(e.get("reason", "")))
    drop_vlm = sum(1 for e in events if e.get("reason") == "throttled_drop")
    drop_q   = sum(1 for e in events if "queue_full" in str(e.get("reason", "")))

    qlen = 0
    qsum = 0
    qcnt = 0
    for e in events:
        if e.get("event") == "SUBMIT": qlen += 1
        if e.get("event") == "PLAY": qlen = max(0, qlen - 1)
        qsum += qlen; qcnt += 1
    avg_qlen = round(qsum / qcnt, 2) if qcnt else 0

    return {
        "nav_submit": nav_sub, "nav_play": nav_ply,
        "nav_play_rate": round(nav_ply / nav_sub, 3) if nav_sub else 0,
        "usr_submit": usr_sub, "usr_play": usr_ply,
        "usr_play_rate": round(usr_ply / usr_sub, 3) if usr_sub else 0,
        "drop_env": drop_env, "drop_vlm": drop_vlm, "drop_queue": drop_q,
        "avg_queue_len": avg_qlen,
        "avg_wait": round(sum(waits) / len(waits), 3) if waits else 0,
        "max_wait": round(max(waits), 3) if waits else 0,
        "avg_wait_select": round(sum(wait_selects) / len(wait_selects), 3) if wait_selects else 0,
        "avg_wait_play": round(sum(wait_plays) / len(wait_plays), 3) if wait_plays else 0,
    }


def main():
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    delays = [0.0, 0.1, 0.3, 0.5]
    intervals = [2, 5]
    rows = []

    print("=" * 60)
    print("  Stage 3A+ 参数扫描")
    print(f"  {len(delays)*len(intervals)} combos x 60s")
    print("=" * 60)

    for delay in delays:
        for interval in intervals:
            name = f"d{int(delay*1000)}_i{interval}"
            path = os.path.join(ROOT, "logs", f"stage3a_plus_{name}.jsonl")
            label = f"[delay={delay:.1f}s int={interval}s]"
            print(f"\n{label}", end=" ", flush=True)

            try:
                m = run_one(delay, interval, path)
                m["delay"] = delay
                m["interval"] = interval
                rows.append(m)
                print(f"nav={m['nav_play']}/{m['nav_submit']} ({m['nav_play_rate']:.1%})  "
                      f"usr={m['usr_play']}/{m['usr_submit']} ({m['usr_play_rate']:.1%})  "
                      f"wait={m['avg_wait']:.3f}s  "
                      f"drop_env={m['drop_env']}  drop_vlm={m['drop_vlm']}  qlen={m['avg_queue_len']}")
            except Exception as e:
                print(f"[FAILED] {e}")

    # CSV
    csv_path = os.path.join(ROOT, "logs", "stage3a_plus_summary.csv")
    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV: {csv_path}")

    # Quick analysis
    print(f"\n{'=' * 60}")
    print("  趋势速览")
    print("  delay ↑     nav_play: " + " → ".join(
        f"{round(sum(r['nav_play_rate'] for r in rows if r['delay']==d)/max(1,sum(1 for r in rows if r['delay']==d)),2)}"
        for d in delays))
    print("  delay ↑     usr_play: " + " → ".join(
        f"{round(sum(r['usr_play_rate'] for r in rows if r['delay']==d)/max(1,sum(1 for r in rows if r['delay']==d)),2)}"
        for d in delays))
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
